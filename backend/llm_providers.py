"""
LLM provider adapters, so the app can switch models at runtime:

- OpenAICompatibleProvider: any server speaking the OpenAI chat-completions
  API — local Bionic / LM Studio / Ollama, Kimi (Moonshot), OpenAI,
  OpenRouter, Google Gemini's OpenAI endpoint, or a custom URL.
- AnthropicProvider: Claude through the official Anthropic SDK (not an
  OpenAI-compatibility shim).

Both take provider-neutral messages ({"role": "system"|"user"|"assistant",
"content": str}) and return plain text, or raise ProviderError with a message
fit to show the user.
"""
import re
import time
from dataclasses import dataclass
from typing import Optional

import anthropic
import openai
from openai import AsyncOpenAI

from utils.logger import get_logger

logger = get_logger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# Claude models that accept the server-side refusal fallback (`fallbacks: "default"`).
_CLAUDE_FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
_CLAUDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Room for adaptive thinking plus the answer; billing is per token actually produced.
_CLAUDE_MAX_TOKENS = 16000


def clean_output(text: str) -> str:
    """Strip Qwen 3 style <think> reasoning blocks (and a dangling opener) from model output."""
    text = _THINK_RE.sub("", text or "")
    if "<think>" in text.lower():
        text = text[: text.lower().index("<think>")]
    return text.strip()


class ProviderError(Exception):
    """A provider call failed; `message` is safe to show in the UI."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


@dataclass
class ChatResult:
    """A reply plus the token usage the provider reported (0 when it reports none)."""
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ProviderConfig:
    kind: str                 # "openai_compatible" | "anthropic"
    model: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    timeout: float = 90.0


class OpenAICompatibleProvider:
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self.client = AsyncOpenAI(
            base_url=config.base_url or None,
            api_key=config.api_key or "not-needed",
            timeout=config.timeout,
            max_retries=1,
        )

    async def chat(self, messages: list[dict], max_tokens: int = 700, temperature: float = 0.3,
                   effort: str = "medium") -> str:
        return (await self.chat_ex(messages, max_tokens=max_tokens, temperature=temperature, effort=effort)).text

    async def chat_ex(self, messages: list[dict], max_tokens: int = 700, temperature: float = 0.3,
                      effort: str = "medium", timeout: Optional[float] = None) -> ChatResult:
        client = self.client.with_options(timeout=timeout) if timeout else self.client
        try:
            response = await client.chat.completions.create(
                model=self.config.model, messages=messages, temperature=temperature, max_tokens=max_tokens,
            )
        except openai.BadRequestError as exc:
            # Reasoning models (e.g. OpenAI o-series) reject temperature/max_tokens: retry in their shape.
            text = str(exc).lower()
            if "temperature" not in text and "max_tokens" not in text:
                raise ProviderError(f"The provider rejected the request: {exc}") from exc
            try:
                response = await client.chat.completions.create(
                    model=self.config.model, messages=messages, max_completion_tokens=max(max_tokens, 4000),
                )
            except openai.APIError as retry_exc:
                raise _openai_error(retry_exc) from retry_exc
        except openai.APIError as exc:
            raise _openai_error(exc) from exc
        usage = getattr(response, "usage", None)
        return ChatResult(text=clean_output(response.choices[0].message.content or ""),
                          input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                          output_tokens=int(getattr(usage, "completion_tokens", 0) or 0))

    async def list_models(self) -> list[str]:
        try:
            page = await self.client.with_options(timeout=15.0).models.list()
            return sorted(m.id for m in page.data)
        except openai.APIError as exc:
            raise _openai_error(exc) from exc

    async def ping(self) -> bool:
        try:
            await self.client.with_options(timeout=4.0, max_retries=0).models.list()
            return True
        except Exception:  # noqa: BLE001
            return False


def _openai_error(exc: Exception) -> ProviderError:
    if isinstance(exc, openai.AuthenticationError):
        return ProviderError("Invalid API key for this provider.")
    if isinstance(exc, openai.NotFoundError):
        return ProviderError("Model or endpoint not found — check the model name and base URL.")
    if isinstance(exc, openai.RateLimitError):
        return ProviderError("Rate limited by the provider — try again shortly.", retryable=True)
    if isinstance(exc, openai.APITimeoutError):
        return ProviderError("The provider timed out.", retryable=True)
    if isinstance(exc, openai.APIConnectionError):
        return ProviderError("Can't reach the provider — is the server running / the URL right?", retryable=True)
    if isinstance(exc, openai.APIStatusError):
        return ProviderError(f"Provider error {exc.status_code}: {exc.message}", retryable=exc.status_code >= 500)
    return ProviderError(f"Provider error: {exc}")


class AnthropicProvider:
    """Claude via the official Anthropic SDK (async)."""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        kwargs: dict = {"timeout": config.timeout, "max_retries": 2}
        if config.api_key:
            kwargs["api_key"] = config.api_key   # otherwise the SDK resolves ANTHROPIC_API_KEY etc.
        if config.base_url:
            kwargs["base_url"] = config.base_url
        self.client = anthropic.AsyncAnthropic(**kwargs)

    def _supports_effort(self) -> bool:
        return "haiku" not in self.config.model   # Haiku 4.5 rejects output_config.effort

    def _supports_fallbacks(self) -> bool:
        # Server-side fallback is a Claude API feature; skip it behind a custom proxy URL.
        return not self.config.base_url and self.config.model.startswith(_CLAUDE_FALLBACK_MODELS)

    async def chat(self, messages: list[dict], max_tokens: int = 700, temperature: float = 0.3,
                   effort: str = "medium") -> str:
        return (await self.chat_ex(messages, max_tokens=max_tokens, temperature=temperature, effort=effort)).text

    async def chat_ex(self, messages: list[dict], max_tokens: int = 700, temperature: float = 0.3,
                      effort: str = "medium", timeout: Optional[float] = None) -> ChatResult:
        # Claude takes the system prompt separately; fold every system message into it.
        # Sampling parameters (temperature) are rejected on current models, so they're not sent.
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system") or None
        convo = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ("user", "assistant")]
        if not convo or convo[0]["role"] != "user":
            convo.insert(0, {"role": "user", "content": "(context above)"})

        params: dict = {"model": self.config.model, "max_tokens": _CLAUDE_MAX_TOKENS, "messages": convo}
        if system:
            params["system"] = system
        if self._supports_effort():
            params["output_config"] = {"effort": effort}
        client = self.client.with_options(timeout=timeout) if timeout else self.client
        try:
            if self._supports_fallbacks():
                response = await client.beta.messages.create(
                    **params, betas=[_CLAUDE_FALLBACK_BETA], fallbacks="default",
                )
            else:
                response = await client.messages.create(**params)
        except anthropic.AuthenticationError as exc:
            raise ProviderError("Invalid Anthropic API key.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ProviderError("This Anthropic API key can't use that model.") from exc
        except anthropic.NotFoundError as exc:
            raise ProviderError(f"Claude model '{self.config.model}' not found — pick one from 'Fetch models'.") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError("Rate limited by Anthropic — try again shortly.", retryable=True) from exc
        except anthropic.BadRequestError as exc:
            raise ProviderError(f"Anthropic rejected the request: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"Anthropic error {exc.status_code}: {exc.message}",
                                retryable=exc.status_code >= 500) from exc
        except anthropic.APITimeoutError as exc:
            raise ProviderError("Claude timed out.", retryable=True) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("Can't reach the Anthropic API — check the internet connection.", retryable=True) from exc
        except TypeError as exc:   # the SDK raises this when no credentials can be resolved
            raise ProviderError("No Anthropic API key — paste one in the model settings, or set the ANTHROPIC_API_KEY environment variable.") from exc

        if response.stop_reason == "refusal":
            category = getattr(getattr(response, "stop_details", None), "category", None)
            raise ProviderError(f"Claude declined this request{f' ({category})' if category else ''}.")
        usage = getattr(response, "usage", None)
        return ChatResult(text="".join(block.text for block in response.content if block.type == "text").strip(),
                          input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                          output_tokens=int(getattr(usage, "output_tokens", 0) or 0))

    async def list_models(self) -> list[str]:
        try:
            ids = [m.id async for m in self.client.with_options(timeout=15.0).models.list()]
            return sorted(ids)
        except anthropic.AuthenticationError as exc:
            raise ProviderError("Invalid Anthropic API key.") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("Can't reach the Anthropic API.", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"Anthropic error {exc.status_code}: {exc.message}") from exc
        except TypeError as exc:   # no credentials
            raise ProviderError("No Anthropic API key — paste one in the model settings, or set the ANTHROPIC_API_KEY environment variable.") from exc

    async def ping(self) -> bool:
        try:
            await self.client.with_options(timeout=6.0, max_retries=0).models.list(limit=1)
            return True
        except Exception:  # noqa: BLE001
            return False


def build_provider(config: ProviderConfig):
    if config.kind == "anthropic":
        return AnthropicProvider(config)
    return OpenAICompatibleProvider(config)


# Starting points for the "Add model" dialog. Model ids are fetched live from
# each provider ("Fetch models"), so none of these are hard requirements.
PRESETS: list[dict] = [
    {"key": "bionic", "name": "Local — Bionic", "kind": "openai_compatible", "base_url": "http://localhost:1234/v1",
     "api_key": "not-needed", "model": "qwen2.5-1.5b-instruct", "daily_limit": 0, "allow_practice": True,
     "notes": "Free, runs on this PC. Load the model in Bionic and start its server."},
    {"key": "lmstudio", "name": "Local — LM Studio", "kind": "openai_compatible", "base_url": "http://localhost:1234/v1",
     "api_key": "lm-studio", "model": "", "daily_limit": 0, "allow_practice": True,
     "notes": "Free, runs on this PC. Start LM Studio's local server (Developer tab)."},
    {"key": "ollama", "name": "Local — Ollama", "kind": "openai_compatible", "base_url": "http://localhost:11434/v1",
     "api_key": "ollama", "model": "", "daily_limit": 0, "allow_practice": True,
     "notes": "Free, runs on this PC. `ollama pull <model>` first."},
    {"key": "vllm", "name": "Local — vLLM", "kind": "openai_compatible", "base_url": "",
     "api_key": "", "model": "", "daily_limit": 0, "allow_practice": True,
     "notes": "Enter your vLLM server's URL ending in /v1 (vLLM's default port 8000 clashes with AiTrading's "
              "backend — start vLLM on another port, e.g. --port 8001). Fetch models to pick the served model."},
    {"key": "local_custom", "name": "Local — OpenAI-compatible API", "kind": "openai_compatible", "base_url": "",
     "api_key": "", "model": "", "daily_limit": 0, "allow_practice": True,
     "notes": "Any local runtime that serves the OpenAI chat-completions API (llama.cpp server, Jan, "
              "text-generation-webui, KoboldCpp …). Enter its URL (usually ending in /v1); the key is optional."},
    {"key": "anthropic", "name": "Claude (Anthropic API)", "kind": "anthropic", "base_url": "",
     "api_key": "", "model": "claude-opus-5-5", "daily_limit": 300, "allow_practice": False,
     "notes": "Paid. Opus 5.5 $4/$20 per 1M tokens (in/out); Sonnet 5.5 $2/$10; Haiku 4.5 $1/$5. "
              "Leave the key blank to use the ANTHROPIC_API_KEY environment variable."},
    {"key": "kimi", "name": "Kimi (Moonshot AI)", "kind": "openai_compatible", "base_url": "https://api.moonshot.ai/v1",
     "api_key": "", "model": "", "daily_limit": 300, "allow_practice": False,
     "notes": "Paid. Use https://api.moonshot.cn/v1 for a China-region account. Fetch models to pick one."},
    {"key": "openai", "name": "OpenAI", "kind": "openai_compatible", "base_url": "https://api.openai.com/v1",
     "api_key": "", "model": "", "daily_limit": 300, "allow_practice": False, "notes": "Paid."},
    {"key": "openrouter", "name": "OpenRouter", "kind": "openai_compatible", "base_url": "https://openrouter.ai/api/v1",
     "api_key": "", "model": "", "daily_limit": 300, "allow_practice": False,
     "notes": "Paid; one key for many providers' models."},
    {"key": "gemini", "name": "Google Gemini", "kind": "openai_compatible",
     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "api_key": "", "model": "",
     "daily_limit": 300, "allow_practice": False, "notes": "Gemini's OpenAI-compatible endpoint."},
    {"key": "custom", "name": "Cloud — OpenAI-compatible API", "kind": "openai_compatible", "base_url": "",
     "api_key": "", "model": "", "daily_limit": 0, "allow_practice": False,
     "notes": "Any server that speaks the OpenAI chat-completions API."},
]


def mask_key(key: Optional[str]) -> str:
    if not key:
        return ""
    if key.startswith("keyring:"):
        return "•••• (Windows Credential Manager)"
    if key.startswith("env:") or key in ("not-needed", "ollama", "lm-studio"):
        return key
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "••••"


def resolve_key(key: Optional[str]) -> Optional[str]:
    """Turn a stored reference into the key: 'keyring:NAME' (Windows Credential Manager), 'env:NAME', or a literal."""
    from credential_store import read_secret
    return read_secret(key) or None


async def timed(coro) -> tuple[float, object]:
    started = time.monotonic()
    result = await coro
    return round(time.monotonic() - started, 2), result
