"""
LLM orchestration with switchable providers.

Two roles, each served by a saved LLM profile (llm_profiles table) that the
user picks in the app:
- "chat": the Trading Coach chat and on-demand analyst notes;
- "background": news reads, forecasts, chart practice, outlooks, reflection,
  recommendation commentary and the morning brief prose.
So you can, for example, chat with Claude while a free local Qwen does the
24/7 background work.

Providers (llm_providers.py): any OpenAI-compatible server (Bionic, LM Studio,
Ollama, Kimi, OpenAI, OpenRouter, Gemini) or Claude via the Anthropic SDK.
Each profile can carry a daily request cap so a paid API can't run up a bill.

All prompt text lives in prompts/prompt_library.py. The ranking engine never
depends on the LLM: when a model is down or over its cap, every method returns
its fallback immediately.
"""
import asyncio
import hashlib
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from openai import AsyncOpenAI

from config import settings
from llm_providers import (
    PRESETS, ProviderConfig, ProviderError, build_provider, clean_output, resolve_key,
)
from prompts.prompt_library import PromptLibrary
from utils.logger import get_logger

logger = get_logger(__name__)

EMBEDDING_DIM = 1536
_AVAILABILITY_TTL_SECONDS = 30
IST = timezone(timedelta(hours=5, minutes=30))
# practice = chart practice on historical charts (defaults to the background model).
ROLES = ("chat", "background", "practice")

# Re-exported for callers/tests that import it from here.
__all__ = ["llm_service", "clean_output", "ungrounded_numbers", "record_usage", "LLMService"]


def usage_hour(now: Optional[datetime] = None) -> datetime:
    """UTC hour bucket for llm_usage rows."""
    return (now or datetime.utcnow()).replace(minute=0, second=0, microsecond=0)


def record_usage(profile_id: int, ok: bool, input_tokens: int = 0, output_tokens: int = 0) -> None:
    """Count one request and its tokens for a profile (hourly caps and the spend estimate)."""
    from database import db_session
    from models import LLMUsage

    with db_session() as db:
        row = db.query(LLMUsage).filter(LLMUsage.profile_id == profile_id, LLMUsage.hour == usage_hour()).first()
        if row is None:
            row = LLMUsage(profile_id=profile_id, hour=usage_hour(), requests=0, failures=0, input_tokens=0,
                           output_tokens=0)
            db.add(row)
        row.requests += 1
        row.failures += 0 if ok else 1
        row.input_tokens += input_tokens
        row.output_tokens += output_tokens

_NUMBER_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
# Integers this small are usually counts ("3 reasons", "2 days"), not data.
_FREE_INTEGER_MAX = 10


def _numbers(text: str) -> list[tuple[str, float]]:
    found = []
    for token in _NUMBER_RE.findall(text):
        try:
            found.append((token, float(token.rstrip(",").replace(",", ""))))
        except ValueError:
            continue
    return found


def ungrounded_numbers(text: str, source: str) -> list[str]:
    """
    Numbers in model output that don't appear in the facts it was given
    (allowing for rounding). A small local model invents or garbles prices, so
    prose with any such number is not shown as fact.
    """
    known = [value for _, value in _numbers(source)]
    bad = []
    for token, value in _numbers(text):
        if value.is_integer() and value <= _FREE_INTEGER_MAX:
            continue
        if not any(abs(value - k) <= max(0.01, 0.006 * abs(k)) for k in known):
            bad.append(token)
    return bad


@dataclass
class ActiveProfile:
    id: int
    name: str
    kind: str
    model: str
    base_url: Optional[str]
    api_key: Optional[str]
    daily_limit: int
    allow_practice: bool
    updated_at: datetime


class LLMService:
    def __init__(self) -> None:
        # Embeddings stay on the local OpenAI-compatible server (Claude has no embeddings API).
        self._embed_client = AsyncOpenAI(base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY,
                                         timeout=settings.LLM_TIMEOUT)
        self._active: dict[str, Optional[ActiveProfile]] = {role: None for role in ROLES}
        self._providers: dict[tuple, object] = {}
        self._availability: dict[int, tuple[bool, float]] = {}
        self._cap_warned: set[tuple[int, date]] = set()
        # profile id -> {"state": "ok" | "no_credit", "message", "at"}: the credit state the
        # latest real call revealed (for providers with no balance API, the only signal there is).
        self.credit: dict[int, dict] = {}
        self._queue: Optional[asyncio.Queue] = None
        self._worker: Optional[asyncio.Task] = None
        self._busy = False
        self.last_interactive_at = 0.0  # monotonic time of the last user chat (background work yields to it)

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------
    def reload_profiles(self) -> None:
        """(Re)load the chat/background profiles from the database; seeds a local default on first run."""
        from database import db_session
        from models import AppSettings, LLMProfile

        with db_session() as db:
            if db.query(LLMProfile).count() == 0:
                preset = next(p for p in PRESETS if p["key"] == "bionic")
                db.add(LLMProfile(name=preset["name"], kind=preset["kind"], base_url=settings.LLM_BASE_URL,
                                  api_key=settings.LLM_API_KEY, model=settings.LLM_MODEL,
                                  daily_limit=0, allow_practice=True))
                db.flush()
            cfg = db.get(AppSettings, 1)
            first = db.query(LLMProfile).order_by(LLMProfile.id).first()
            if cfg is not None:
                if cfg.chat_profile_id is None or db.get(LLMProfile, cfg.chat_profile_id) is None:
                    cfg.chat_profile_id = first.id
                if cfg.background_profile_id is None or db.get(LLMProfile, cfg.background_profile_id) is None:
                    cfg.background_profile_id = first.id
            background_id = cfg.background_profile_id if cfg else first.id
            practice_id = cfg.practice_profile_id if cfg else None
            if practice_id is not None and db.get(LLMProfile, practice_id) is None:
                practice_id = None
            ids = {"chat": cfg.chat_profile_id if cfg else first.id, "background": background_id,
                   "practice": practice_id or background_id}
            for role, profile_id in ids.items():
                p = db.get(LLMProfile, profile_id)
                self._active[role] = ActiveProfile(
                    id=p.id, name=p.name, kind=p.kind, model=p.model, base_url=p.base_url,
                    api_key=resolve_key(p.api_key), daily_limit=int(p.daily_limit or 0),
                    allow_practice=bool(p.allow_practice), updated_at=p.updated_at or datetime.utcnow(),
                ) if p else None
        self._availability.clear()
        for role in ROLES:
            p = self._active[role]
            if p:
                logger.info("LLM %s model: %s (%s, %s)", role, p.name, p.kind, p.model)

    def active(self, role: str) -> Optional[ActiveProfile]:
        if self._active.get(role) is None:
            try:
                self.reload_profiles()
            except Exception as exc:  # noqa: BLE001  (e.g. database not ready yet)
                logger.warning("Could not load LLM profiles: %s", exc)
        return self._active.get(role)

    def _provider(self, profile: ActiveProfile):
        key = (profile.id, profile.updated_at)
        provider = self._providers.get(key)
        if provider is None:
            provider = build_provider(ProviderConfig(kind=profile.kind, model=profile.model,
                                                     base_url=profile.base_url, api_key=profile.api_key,
                                                     timeout=float(settings.LLM_TIMEOUT) + 30))
            self._providers = {k: v for k, v in self._providers.items() if k[0] != profile.id}
            self._providers[key] = provider
        return provider

    def practice_allowed(self) -> bool:
        """Chart practice makes hundreds of calls; only profiles marked for it (local by default) may run it."""
        p = self.active("practice")
        return bool(p and p.allow_practice)

    def describe(self) -> dict:
        return {role: ({"id": p.id, "name": p.name, "kind": p.kind, "model": p.model,
                        "local": p.kind != "anthropic" and any(h in (p.base_url or "").lower()
                                                               for h in ("localhost", "127.0.0.1"))}
                       if (p := self.active(role)) else None)
                for role in ROLES}

    # ------------------------------------------------------------------
    # Availability and daily caps
    # ------------------------------------------------------------------
    async def is_available(self, role: str = "background") -> bool:
        """Cached probe of the role's provider, so a stopped server costs a few seconds once, not per call."""
        profile = self.active(role)
        if profile is None:
            return False
        cached = self._availability.get(profile.id)
        if cached and time.monotonic() - cached[1] < _AVAILABILITY_TTL_SECONDS:
            return cached[0]
        ok = await self._provider(profile).ping()
        if not ok and (not cached or cached[0]):
            logger.warning("LLM %s model unreachable: %s (%s)", role, profile.name, profile.model)
        self._availability[profile.id] = (ok, time.monotonic())
        return ok

    def _consume(self, profile: ActiveProfile) -> bool:
        """Count one request against the profile's daily cap; False when the cap is reached."""
        from database import db_session
        from models import LLMProfile

        today = datetime.now(IST).date()
        with db_session() as db:
            row = db.get(LLMProfile, profile.id)
            if row is None:
                return False
            if row.usage_date != today:
                row.usage_date, row.usage_count = today, 0
            if row.daily_limit and row.usage_count >= row.daily_limit:
                if (profile.id, today) not in self._cap_warned:
                    logger.warning("LLM profile %s reached its daily cap of %d requests", row.name, row.daily_limit)
                    self._cap_warned.add((profile.id, today))
                return False
            row.usage_count += 1
        return True

    def note_credit(self, profile_id: int, error: Optional[ProviderError] = None) -> None:
        """Record what a real call said about the account's credit (success = credit available)."""
        if error is not None and not error.no_credit:
            return   # other failures say nothing about credit
        self.credit[profile_id] = {"state": "no_credit" if error else "ok",
                                   "message": error.message if error else None,
                                   "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    async def _call(self, role: str, messages: list[dict], max_tokens: int, temperature: float,
                    effort: str) -> Optional[str]:
        profile = self.active(role)
        if profile is None or not await self.is_available(role) or not self._consume(profile):
            return None
        try:
            reply = await self._provider(profile).chat_ex(messages, max_tokens=max_tokens, temperature=temperature,
                                                          effort=effort)
            self.note_credit(profile.id)
            record_usage(profile.id, bool(reply.text), reply.input_tokens, reply.output_tokens)
            return reply.text or None
        except ProviderError as exc:
            self.note_credit(profile.id, exc)
            record_usage(profile.id, False)
            logger.error("LLM %s call failed (%s): %s", role, profile.name, exc.message)
            if exc.retryable:
                self._availability.pop(profile.id, None)
            return None

    # ------------------------------------------------------------------
    # Low-level invocation
    # ------------------------------------------------------------------
    async def complete(self, prompt: str, temperature: float = 0.2, max_tokens: int = 400,
                       role: str = "background") -> Optional[str]:
        """Single-turn completion for structured jobs; None on failure so callers skip rather than guess."""
        return await self._call(role, [{"role": "user", "content": prompt}], max_tokens, temperature,
                                effort="low" if role == "background" else "medium")

    async def _safe_invoke(self, prompt: str, fallback: str, role: str = "background") -> str:
        """Fact-based prose: discarded (fallback used) if it states a number the prompt didn't contain."""
        text = await self.complete(prompt, temperature=settings.LLM_TEMPERATURE, max_tokens=700, role=role)
        if text:
            bad = ungrounded_numbers(text, prompt)
            if bad:
                logger.warning("Discarded LLM text with numbers not in its facts: %s", ", ".join(bad[:8]))
                return fallback
        return text or fallback

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
                if await self.is_available("background"):
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
        """On-demand analyst note (chat model), grounded in the deterministic analysis sections."""
        return await self._safe_invoke(PromptLibrary.stock_analysis(**stock_data), "", role="chat")

    async def generate_recommendation_reasoning(self, rec_data: dict, fallback: str = "") -> str:
        return await self._safe_invoke(PromptLibrary.recommendation_reasoning(**rec_data), fallback)

    async def interpret_market_context(self, context_data: dict, fallback: str = "") -> str:
        return await self._safe_invoke(PromptLibrary.market_context(**context_data),
                                       fallback or "Market context summary unavailable.")

    async def generate_morning_brief(self, brief_data: dict, fallback: str) -> str:
        return await self._safe_invoke(PromptLibrary.morning_brief(**brief_data), fallback)

    async def generate_coaching_feedback(self, coaching_data: dict) -> str:
        return await self._safe_invoke(PromptLibrary.trading_coach(**coaching_data),
                                       "Coaching feedback unavailable right now.", role="chat")

    async def explain_ranking_change(self, change_data: dict) -> str:
        fallback = (f"{change_data.get('symbol')} moved from #{change_data.get('old_rank')} "
                    f"to #{change_data.get('new_rank')} based on updated conviction score.")
        return await self._safe_invoke(PromptLibrary.ranking_change_explanation(**change_data), fallback)

    async def chat(self, user_message: str, stock_context: Optional[str] = None,
                   context_snippets: Optional[list[str]] = None,
                   history: Optional[list[tuple[str, str, Optional[str]]]] = None,
                   focus: Optional[tuple[str, str]] = None) -> str:
        """
        ChatGPT-style reply on the chat model. `history` is prior (user, assistant,
        viewing-label) turns, oldest first; `context_snippets` carry live market
        state; `focus` is the (name, symbol) of the stock the user has open. Every
        user turn is tagged with the stock that was open when it was asked, so
        "this share" resolves to the right stock even for a small local model.
        """
        self.last_interactive_at = time.monotonic()
        profile = self.active("chat")
        if profile is None or not await self.is_available("chat"):
            name = f"'{profile.name}' ({profile.model})" if profile else "the chat model"
            return (f"I can't reach {name} right now. Check it in the AI model settings (top bar), or start "
                    "the local server if it's a local model. Rankings, trade plans and analysis keep working.")

        messages: list[dict] = [{"role": "system", "content": PromptLibrary.CHAT_SYSTEM}]
        # Earlier turns first, then today's live facts right next to the question:
        # a small model leans on whatever is nearest, so fresh facts must win over
        # old answers (which may be about other stocks or stale prices).
        for user_turn, assistant_turn, label in history or []:
            messages.append({"role": "user", "content": PromptLibrary.tag_chat_turn(label, user_turn)})
            messages.append({"role": "assistant", "content": assistant_turn})
        if context_snippets:
            messages.append({"role": "system", "content": "Live context (current, overrides anything said earlier):\n"
                                                          + "\n".join(context_snippets)})
        focus_label = None
        if focus:
            messages.append({"role": "system", "content": PromptLibrary.chat_focus(*focus)})
            focus_label = f"{focus[0]} ({focus[1]})"
        elif stock_context:
            focus_label = stock_context
        messages.append({"role": "user", "content": PromptLibrary.tag_chat_turn(focus_label, user_message)})

        if not self._consume(profile):
            return f"'{profile.name}' has reached its daily request limit ({profile.daily_limit}). Raise it in the AI model settings."
        try:
            reply = await self._provider(profile).chat_ex(messages, max_tokens=900,
                                                          temperature=settings.LLM_TEMPERATURE, effort="medium")
            self.note_credit(profile.id)
            record_usage(profile.id, bool(reply.text), reply.input_tokens, reply.output_tokens)
            return reply.text or "I don't have an answer to that yet."
        except ProviderError as exc:
            self.note_credit(profile.id, exc)
            record_usage(profile.id, False)
            logger.error("Chat failed (%s): %s", profile.name, exc.message)
            self._availability.pop(profile.id, None)
            return f"The chat model ({profile.name}) failed to answer: {exc.message}"

    # ------------------------------------------------------------------
    # Profile tools for the settings UI
    # ------------------------------------------------------------------
    @staticmethod
    async def list_models_for(config: ProviderConfig) -> list[str]:
        return await build_provider(config).list_models()

    @staticmethod
    async def test_config(config: ProviderConfig) -> dict:
        """One tiny real request, so the user knows the key, URL and model all work."""
        provider = build_provider(config)
        started = time.monotonic()
        try:
            reply = await provider.chat([{"role": "user", "content": "Reply with the single word: OK"}],
                                        max_tokens=20, temperature=0.0, effort="low")
            return {"ok": True, "reply": reply[:200], "seconds": round(time.monotonic() - started, 2)}
        except ProviderError as exc:
            return {"ok": False, "error": exc.message, "seconds": round(time.monotonic() - started, 2)}

    # ------------------------------------------------------------------
    # Embeddings (used by memory_service for semantic search over past setups)
    # ------------------------------------------------------------------
    async def embed_text(self, text: str) -> list[float]:
        """Embedding from the local server; deterministic pseudo-embedding when it isn't available."""
        try:
            response = await self._embed_client.with_options(timeout=20.0, max_retries=0).embeddings.create(
                model=settings.LLM_EMBEDDING_MODEL, input=text)
            vector = response.data[0].embedding
            return _resize_vector(vector, EMBEDDING_DIM) if len(vector) != EMBEDDING_DIM else vector
        except Exception as exc:  # noqa: BLE001
            logger.debug("Embeddings unavailable (%s), using deterministic fallback", exc)
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
