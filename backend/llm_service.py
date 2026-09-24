"""
Local LLM integration via Semantic Kernel, against any OpenAI-compatible
server (LM Studio / Bionic) at LLM_BASE_URL — Qwen 2.5 or Qwen 3.

All prompt text lives in prompts/prompt_library.py — this module is purely
orchestration: kernel setup, availability probing, invocation, retries,
fallbacks, and a single-worker background queue for non-interactive jobs.

Design note: the ranking engine never *depends* on the LLM. Scores, setups
and trade plans are deterministic (analysis_service); the LLM adds narrative
on top. When the model server is down, every method returns its fallback
immediately instead of hanging through retries.
"""
import asyncio
import hashlib
import re
import time
from typing import Awaitable, Callable, Optional

from openai import AsyncOpenAI
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import (
    OpenAIChatCompletion,
    OpenAIChatPromptExecutionSettings,
)
from semantic_kernel.contents.chat_history import ChatHistory
from semantic_kernel.functions import KernelArguments

from config import settings
from prompts.prompt_library import PromptLibrary
from utils.decorators import async_retry
from utils.logger import get_logger

logger = get_logger(__name__)

EMBEDDING_DIM = 1536
_AVAILABILITY_TTL_SECONDS = 30
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def clean_output(text: str) -> str:
    """Strip Qwen 3 style <think> reasoning blocks (and a dangling opener) from model output."""
    text = _THINK_RE.sub("", text)
    if "<think>" in text.lower():
        text = text[: text.lower().index("<think>")]
    return text.strip()


class LLMService:
    """Wraps a Semantic Kernel chat-completion service pointed at the local LLM."""

    def __init__(self) -> None:
        self._async_client = AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            timeout=settings.LLM_TIMEOUT,
        )

        self.kernel = Kernel()
        self.service_id = "local-qwen"
        self.kernel.add_service(
            OpenAIChatCompletion(
                service_id=self.service_id,
                ai_model_id=settings.LLM_MODEL,
                async_client=self._async_client,
            )
        )
        self._execution_settings = OpenAIChatPromptExecutionSettings(
            service_id=self.service_id,
            temperature=settings.LLM_TEMPERATURE,
            max_tokens=700,
        )
        self._available: Optional[bool] = None
        self._available_checked_at = 0.0
        self._queue: Optional[asyncio.Queue] = None
        self._worker: Optional[asyncio.Task] = None
        self._busy = False
        self.last_interactive_at = 0.0  # monotonic time of the last user chat (background work yields to it)
        logger.info("LLMService initialized against %s (model=%s)", settings.LLM_BASE_URL, settings.LLM_MODEL)

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------
    async def is_available(self) -> bool:
        """Cheap cached probe of GET /models, so a stopped server costs ~2s once, not minutes per call."""
        if self._available is not None and time.monotonic() - self._available_checked_at < _AVAILABILITY_TTL_SECONDS:
            return self._available
        try:
            await self._async_client.with_options(timeout=3.0, max_retries=0).models.list()
            available = True
        except Exception as exc:  # noqa: BLE001
            if self._available is not False:
                logger.warning("Local LLM unavailable at %s: %s", settings.LLM_BASE_URL, exc)
            available = False
        self._available, self._available_checked_at = available, time.monotonic()
        return available

    # ------------------------------------------------------------------
    # Low-level invocation
    # ------------------------------------------------------------------
    @async_retry(max_retries=settings.LLM_MAX_RETRIES, base_delay=0.75)
    async def _invoke_prompt(self, prompt: str) -> str:
        result = await self.kernel.invoke_prompt(
            prompt=prompt,
            arguments=KernelArguments(settings=self._execution_settings),
        )
        return clean_output(str(result))

    async def complete(self, prompt: str, temperature: float = 0.2, max_tokens: int = 400) -> Optional[str]:
        """
        Single-turn completion for the structured AI-analyst jobs (news reads,
        forecasts, reflection): low temperature, no templating, None on failure
        so callers can skip rather than store a fallback as if the model said it.
        """
        if not await self.is_available():
            return None
        try:
            response = await self._async_client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            text = clean_output(response.choices[0].message.content or "")
            return text or None
        except Exception as exc:  # noqa: BLE001
            logger.error("LLM completion failed: %s", exc)
            self._available = None
            return None

    async def _safe_invoke(self, prompt: str, fallback: str) -> str:
        if not await self.is_available():
            return fallback
        try:
            text = await self._invoke_prompt(prompt)
            return text or fallback
        except Exception as exc:  # noqa: BLE001
            logger.error("LLM invocation failed, using fallback response: %s", exc)
            self._available = None  # re-probe next time
            return fallback

    # ------------------------------------------------------------------
    # Background queue (one job at a time: a local model can't usefully
    # serve parallel requests, and interactive chat should stay responsive)
    # ------------------------------------------------------------------
    def start_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._queue = asyncio.Queue(maxsize=50)
            self._worker = asyncio.create_task(self._run_worker())

    async def stop_worker(self) -> None:
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None

    @property
    def idle(self) -> bool:
        """No background job running or waiting."""
        return not self._busy and (self._queue is None or self._queue.empty())

    def seconds_since_interactive(self) -> float:
        return time.monotonic() - self.last_interactive_at

    def enqueue(self, job: Callable[[], Awaitable[None]], label: str = "job") -> bool:
        """Queue a background LLM job; silently drops it if the worker isn't running or the queue is full."""
        if self._queue is None:
            return False
        try:
            self._queue.put_nowait((label, job))
            return True
        except asyncio.QueueFull:
            logger.warning("LLM queue full, dropping %s", label)
            return False

    async def _run_worker(self) -> None:
        while True:
            label, job = await self._queue.get()
            self._busy = True
            try:
                if await self.is_available():
                    await job()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.error("Background LLM job %s failed: %s", label, exc)
            finally:
                self._busy = False
                self._queue.task_done()

    # ------------------------------------------------------------------
    # High-level orchestration methods
    # ------------------------------------------------------------------
    async def analyze_stock(self, stock_data: dict) -> str:
        """Narrative read for one stock, grounded in the deterministic analysis sections."""
        prompt = PromptLibrary.stock_analysis(**stock_data)
        fallback = ""
        return await self._safe_invoke(prompt, fallback)

    async def generate_recommendation_reasoning(self, rec_data: dict, fallback: str = "") -> str:
        """Narrative commentary for a trade recommendation."""
        prompt = PromptLibrary.recommendation_reasoning(**rec_data)
        return await self._safe_invoke(prompt, fallback)

    async def interpret_market_context(self, context_data: dict, fallback: str = "") -> str:
        """Summarize FII/DII activity, news, and global cues into a market brief."""
        prompt = PromptLibrary.market_context(**context_data)
        return await self._safe_invoke(prompt, fallback or "Market context summary unavailable.")

    async def generate_morning_brief(self, brief_data: dict, fallback: str) -> str:
        prompt = PromptLibrary.morning_brief(**brief_data)
        return await self._safe_invoke(prompt, fallback)

    async def generate_coaching_feedback(self, coaching_data: dict) -> str:
        prompt = PromptLibrary.trading_coach(**coaching_data)
        fallback = "Coaching feedback unavailable right now."
        return await self._safe_invoke(prompt, fallback)

    async def explain_ranking_change(self, change_data: dict) -> str:
        prompt = PromptLibrary.ranking_change_explanation(**change_data)
        fallback = (
            f"{change_data.get('symbol')} moved from #{change_data.get('old_rank')} "
            f"to #{change_data.get('new_rank')} based on updated conviction score."
        )
        return await self._safe_invoke(prompt, fallback)

    async def chat(self, user_message: str, stock_context: Optional[str] = None,
                   context_snippets: Optional[list[str]] = None,
                   history: Optional[list[tuple[str, str, Optional[str]]]] = None,
                   focus: Optional[tuple[str, str]] = None) -> str:
        """
        ChatGPT-style reply. `history` is prior (user, assistant, viewing-label)
        turns, oldest first; `context_snippets` carry live market state; `focus`
        is the (name, symbol) of the stock the user has open. Every user turn is
        tagged with the stock that was open when it was asked, so "this share"
        resolves to the right stock even for a small local model.
        """
        self.last_interactive_at = time.monotonic()
        if not await self.is_available():
            return (
                "I can't reach the local model right now. Start LM Studio (or Bionic) with the model "
                f"'{settings.LLM_MODEL}' loaded at {settings.LLM_BASE_URL} and try again. "
                "Rankings, trade plans and analysis keep working without it."
            )

        chat_history = ChatHistory()
        chat_history.add_system_message(PromptLibrary.CHAT_SYSTEM)
        # Earlier turns first, then today's live facts right next to the question:
        # a small model leans on whatever is nearest, so fresh facts must win over
        # old answers (which may be about other stocks or stale prices).
        for user_turn, assistant_turn, label in history or []:
            chat_history.add_user_message(PromptLibrary.tag_chat_turn(label, user_turn))
            chat_history.add_assistant_message(assistant_turn)
        if context_snippets:
            chat_history.add_system_message("Live context (current, overrides anything said earlier):\n"
                                            + "\n".join(context_snippets))
        focus_label = None
        if focus:
            # Placed right before the question: small models weight recent instructions most.
            chat_history.add_system_message(PromptLibrary.chat_focus(*focus))
            focus_label = f"{focus[0]} ({focus[1]})"
        elif stock_context:
            focus_label = stock_context
        chat_history.add_user_message(PromptLibrary.tag_chat_turn(focus_label, user_message))

        chat_service: OpenAIChatCompletion = self.kernel.get_service(self.service_id)
        try:
            response = await chat_service.get_chat_message_content(
                chat_history=chat_history,
                settings=self._execution_settings,
                kernel=self.kernel,
            )
            return clean_output(str(response)) or "I don't have an answer to that yet."
        except Exception as exc:  # noqa: BLE001
            logger.error("Chat completion failed: %s", exc)
            self._available = None
            return "The local model failed to answer (see backend logs). Please try again."

    # ------------------------------------------------------------------
    # Embeddings (used by memory_service for semantic search over past setups)
    # ------------------------------------------------------------------
    async def embed_text(self, text: str) -> list[float]:
        """
        Generate an embedding vector for `text`. Falls back to a deterministic
        pseudo-embedding if the local server doesn't expose an embeddings endpoint
        (common for lightweight local inference servers).
        """
        if not await self.is_available():
            return _pseudo_embedding(text)
        try:
            response = await self._async_client.embeddings.create(
                model=settings.LLM_EMBEDDING_MODEL,
                input=text,
            )
            vector = response.data[0].embedding
            if len(vector) != EMBEDDING_DIM:
                vector = _resize_vector(vector, EMBEDDING_DIM)
            return vector
        except Exception as exc:  # noqa: BLE001
            logger.warning("Embeddings endpoint unavailable (%s), using deterministic fallback", exc)
            return _pseudo_embedding(text)


def _resize_vector(vector: list[float], target_dim: int) -> list[float]:
    if len(vector) >= target_dim:
        return vector[:target_dim]
    return vector + [0.0] * (target_dim - len(vector))


def _pseudo_embedding(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """
    Deterministic, dependency-free fallback embedding built from a SHA-256 hash
    stream. Not semantically meaningful, but keeps pgvector similarity search
    functional (self-consistent) when no real embedding model is available.
    """
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    values = []
    counter = 0
    while len(values) < dim:
        block = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        values.extend(b / 255.0 - 0.5 for b in block)
        counter += 1
    return values[:dim]


llm_service = LLMService()
