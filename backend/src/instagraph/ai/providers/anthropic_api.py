"""Anthropic API provider (docs/ai-assistant.md A8.1-A8.3) - the public provider.

The one place in Instagraph that calls ``api.anthropic.com``, with the user's own API key.
Manual tool loop with streaming, so text and tool activity reach the UI as they happen:

* adaptive thinking, ``output_config.effort`` always set explicitly (Claude Opus 5.5
  defaults to ``medium``); thinking can't be turned off on the 5.5 models;
* strict tool schemas, ``tool_choice`` left at auto (forced tool use is rejected);
  ``eager_input_streaming`` on, so inputs are validated by the tool handlers;
* automatic prompt caching (tools and system prompt never change in a conversation);
* server-side refusal fallback (``fallbacks: "default"``) on Opus/Sonnet 5.5;
* the conversation history is append-only (thinking blocks are passed back unchanged);
  an unfinished turn (error, refusal, Stop) is rolled back so the next one starts clean.
"""

import importlib.util
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from instagraph.ai import keys
from instagraph.ai.providers.base import (
    ErrorEvent,
    ProviderEvent,
    ProviderStatus,
    TextDelta,
    ToolEnd,
    ToolStart,
    TurnRequest,
    UsageEvent,
)
from instagraph.ai.store import Usage
from instagraph.store.app_settings import AppSettings
from instagraph.store.dataset import get_store

MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
#: Models with server-side refusal fallback (Claude Haiku 5.5 has none).
FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-sonnet-5-5"})
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 32_000
MAX_ROUNDS = 20
MAX_JSON_RETRIES = 2
#: Model-internal block types dropped before the last ``fallback`` block when echoing a
#: mid-output fallback back (docs: "Echoing fallback turns back").
_PRE_FALLBACK_DROP = frozenset({"thinking", "redacted_thinking", "tool_use", "server_tool_use"})

INSTALL_HINT = "Install the AI extra: pip install 'instagraph[ai]'."


def sdk_installed() -> bool:
    return importlib.util.find_spec("anthropic") is not None


def echo_content(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assistant content as it must be sent back: after a mid-output fallback, only the
    text before the last ``fallback`` marker is kept (tool calls there never ran)."""
    last = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
    if last < 0:
        return blocks
    before = [b for b in blocks[:last] if b.get("type") not in _PRE_FALLBACK_DROP]
    return [*before, *blocks[last + 1 :]]


def _usage(final: Any) -> Usage:
    u = getattr(final, "usage", None)

    def get(name: str) -> int:
        return int(getattr(u, name, 0) or 0)

    return Usage(
        input_tokens=get("input_tokens"),
        output_tokens=get("output_tokens"),
        cache_read_input_tokens=get("cache_read_input_tokens"),
        cache_creation_input_tokens=get("cache_creation_input_tokens"),
    )


def _block_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    data: dict[str, Any] = block.to_dict(mode="json")
    return data


@dataclass
class AnthropicProvider:
    id: str = "anthropic"
    label: str = "Anthropic API (your API key)"
    client_factory: Callable[[str], Any] | None = None
    """Builds the async client from a key (tests inject a fake)."""

    def status(self, settings: AppSettings) -> ProviderStatus:
        if self.client_factory is None and not sdk_installed():
            return ProviderStatus(False, INSTALL_HINT, MODELS)
        info = keys.key_info(get_store().data_dir)
        if not info.set:
            return ProviderStatus(False, "Add your Anthropic API key to use this provider.", MODELS)
        where = "from ANTHROPIC_API_KEY" if info.source == "env" else "saved"
        return ProviderStatus(True, f"API key {info.hint} ({where})", MODELS)

    def _client(self, key: str) -> Any:
        if self.client_factory is not None:
            return self.client_factory(key)
        import anthropic

        return anthropic.AsyncAnthropic(api_key=key, max_retries=2)

    async def test_key(self, key: str) -> tuple[bool, str]:
        """Check a key with a free call (list models). Returns ``(ok, message)``."""
        if self.client_factory is None and not sdk_installed():
            return False, INSTALL_HINT
        try:
            await self._client(key).models.list(limit=1)
        except Exception as exc:
            return False, _error_message(exc)[1]
        return True, "The key works."

    async def run_turn(self, request: TurnRequest) -> AsyncIterator[ProviderEvent]:
        key = keys.get_api_key(get_store().data_dir)
        if not key:
            yield ErrorEvent("no_api_key", "Add your Anthropic API key in Settings → AI.")
            return
        client = self._client(key)
        messages: list[dict[str, Any]] = request.conversation.provider_state.setdefault(
            "messages", []
        )
        start = len(messages)
        messages.append({"role": "user", "content": request.user_message})
        model = request.model if request.model in MODELS else MODELS[0]
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "system": request.system_prompt,
            "tools": [
                {**d, "strict": True, "eager_input_streaming": True}
                for d in request.tools.definitions()
            ],
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": request.effort},
            "cache_control": {"type": "ephemeral"},
        }
        if model in FALLBACK_MODELS:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"

        finished = False
        json_retries = 0
        try:
            for _ in range(MAX_ROUNDS):
                if request.cancel.is_set():
                    return
                try:
                    async with client.beta.messages.stream(**params) as stream:
                        async for event in stream:
                            if request.cancel.is_set():
                                return
                            delta = getattr(event, "delta", None)
                            if (
                                delta is not None
                                and getattr(event, "type", None) == "content_block_delta"
                                and getattr(delta, "type", None) == "text_delta"
                            ):
                                yield TextDelta(str(delta.text))
                        final = await stream.get_final_message()
                except ValueError:
                    # Tool input JSON the SDK couldn't parse at all: re-issue the round.
                    json_retries += 1
                    if json_retries > MAX_JSON_RETRIES:
                        yield ErrorEvent("bad_tool_input", "The model sent unreadable tool input.")
                        return
                    continue
                except Exception as exc:
                    code, message = _error_message(exc)
                    yield ErrorEvent(code, message)
                    return
                json_retries = 0
                yield UsageEvent(_usage(final))

                stop = getattr(final, "stop_reason", None)
                if stop == "refusal":
                    details = getattr(final, "stop_details", None)
                    category = getattr(details, "category", None)
                    note = f" (category: {category})" if category else ""
                    yield ErrorEvent(
                        "refusal",
                        f"The AI declined to answer this{note}. Try rephrasing the question.",
                    )
                    return
                content = echo_content([_block_dict(b) for b in final.content])
                if stop == "max_tokens":
                    yield ErrorEvent("max_tokens", "The answer got too long and was cut off.")
                    return
                messages.append({"role": "assistant", "content": content})
                if stop == "pause_turn":
                    continue
                tool_uses = [b for b in content if b.get("type") == "tool_use"]
                if stop != "tool_use" or not tool_uses:
                    finished = True
                    return
                results: list[dict[str, Any]] = []
                for block in tool_uses:
                    args = block.get("input")
                    yield ToolStart(
                        str(block["id"]), str(block["name"]), args if isinstance(args, dict) else {}
                    )
                    if not isinstance(args, dict):
                        result_text, is_error, summary, truncated = (
                            "INVALID_JSON: tool input must be an object.",
                            True,
                            "invalid input",
                            False,
                        )
                    else:
                        r = await request.tools.acall(str(block["name"]), args)
                        result_text, is_error = r.as_text(), r.is_error
                        summary, truncated = r.summary, r.truncated
                    yield ToolEnd(
                        str(block["id"]), str(block["name"]), summary, is_error, truncated
                    )
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block["id"],
                            "content": result_text,
                            "is_error": is_error,
                        }
                    )
                messages.append({"role": "user", "content": results})
            yield ErrorEvent("too_many_steps", "The AI took too many steps for one answer.")
        finally:
            if not finished:
                del messages[start:]  # roll back the unfinished turn


def _error_message(exc: Exception) -> tuple[str, str]:
    """``(code, friendly message)`` for an SDK exception (never includes the key)."""
    try:
        import anthropic
    except ImportError:  # pragma: no cover - only with a fake client
        return "provider_error", str(exc)[:300]
    if isinstance(exc, anthropic.AuthenticationError):
        return "auth", "The API key was rejected. Check it in Settings → AI."
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "permission", "This API key isn't allowed to use that model."
    if isinstance(exc, anthropic.RateLimitError):
        return "rate_limit", "Rate limit reached. Wait a moment and try again."
    if isinstance(exc, anthropic.APIConnectionError):
        return "network", "Couldn't reach Anthropic. Check your internet connection."
    if isinstance(exc, anthropic.APIStatusError):
        return f"api_{exc.status_code}", f"Anthropic API error ({exc.status_code}): {exc.message}"
    return "provider_error", str(exc)[:300] or type(exc).__name__


provider = AnthropicProvider()
