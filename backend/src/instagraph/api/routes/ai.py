"""AI assistant endpoints (docs/ai-assistant.md A8.5).

Off by default: every endpoint except ``GET /api/ai/status`` returns ``409 ai_disabled``
while ``ai_enabled`` is false, so no AI code path runs. Answers stream as Server-Sent
Events from ``POST /api/ai/conversations/{conv_id}/messages``.
"""

import json
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from sse_starlette import EventSourceResponse

from instagraph.ai import keys, loop
from instagraph.ai.providers import get_provider, providers
from instagraph.ai.scope import folders_for, resolve_scope
from instagraph.ai.store import ConversationStore
from instagraph.api.deps import DatasetDep, SettingsDep, StoreDep
from instagraph.errors import ERROR_RESPONSES, ApiError, NotFoundError, ValidationApiError
from instagraph.schemas.ai import (
    AiConversation,
    AiConversationCreate,
    AiConversationList,
    AiConversationPatch,
    AiConversationSummary,
    AiDeleted,
    AiKeyInfo,
    AiKeySave,
    AiKeyTest,
    AiLastRequest,
    AiMessageCreate,
    AiProviderInfo,
    AiStatusResponse,
)
from instagraph.store.app_settings import AppSettings
from instagraph.store.dataset import Dataset, DatasetStore

router = APIRouter(prefix="/api/ai", tags=["ai"], responses=ERROR_RESPONSES)

_stores: dict[Path, ConversationStore] = {}
_stores_lock = threading.Lock()


def conversations(store: DatasetStore) -> ConversationStore:
    """The conversation store of this data directory (one instance per directory)."""
    with _stores_lock:
        cs = _stores.get(store.data_dir)
        if cs is None:
            cs = _stores[store.data_dir] = ConversationStore(store.data_dir)
        return cs


def _chat_folders(ds: Dataset, chat_ids: list[int]) -> list[str]:
    rows = ds.thread_rows()
    unknown = [c for c in chat_ids if c not in rows]
    if unknown:
        raise ValidationApiError(f"Unknown chat ids: {unknown}")
    return folders_for(ds, chat_ids)


# --------------------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------------------


@router.get("/status", response_model=AiStatusResponse)
def ai_status(store: StoreDep, settings: SettingsDep) -> AiStatusResponse:
    """Whether the AI is on, which providers exist and whether they are ready.
    Works with the feature off and without data."""
    ds = store.peek()
    infos = []
    for p in providers().values():
        st = p.status(settings)
        infos.append(
            AiProviderInfo(
                id=p.id,
                label=p.label,
                available=st.available,
                message=st.message,
                models=list(st.models),
            )
        )
    default_ids = sorted(resolve_scope(ds, settings.ai_default_scope)) if ds else []
    return AiStatusResponse(
        enabled=settings.ai_enabled,
        confirmed_notice=settings.ai_confirmed_notice,
        has_data=ds is not None,
        provider=settings.ai_provider,
        model=settings.ai_model,
        effort=settings.ai_effort,
        hide_names_default=settings.ai_hide_names,
        default_chat_ids=default_ids,
        providers=infos,
        api_key=_key_info(store),
    )


# --------------------------------------------------------------------------------------
# API key (Anthropic provider). Allowed while the feature is off: saving a key sends
# nothing, and "Test key" is an explicit action of the setup screen.
# --------------------------------------------------------------------------------------


def _key_info(store: DatasetStore) -> AiKeyInfo:
    info = keys.key_info(store.data_dir)
    return AiKeyInfo(set=info.set, source=info.source, hint=info.hint)


@router.put("/key", response_model=AiKeyInfo)
def save_key(body: AiKeySave, store: StoreDep) -> AiKeyInfo:
    """Store the Anthropic API key (owner-only file in the data directory)."""
    try:
        keys.save_api_key(store.data_dir, body.key)
    except ValueError as exc:
        raise ValidationApiError(str(exc)) from exc
    return _key_info(store)


@router.delete("/key", response_model=AiKeyInfo)
def remove_key(store: StoreDep) -> AiKeyInfo:
    """Remove the stored key (a key from ``ANTHROPIC_API_KEY`` stays in effect)."""
    keys.delete_api_key(store.data_dir)
    return _key_info(store)


@router.post("/key/test", response_model=AiKeyTest)
async def test_key(store: StoreDep, body: AiKeySave | None = None) -> AiKeyTest:
    """Check a key (the given one, else the stored one) with a free API call."""
    provider = get_provider("anthropic")
    key = body.key.strip() if body else keys.get_api_key(store.data_dir)
    if provider is None or not hasattr(provider, "test_key"):
        return AiKeyTest(ok=False, message="The Anthropic provider isn't available.")
    if not key:
        return AiKeyTest(ok=False, message="No API key set.")
    ok, message = await provider.test_key(key)
    return AiKeyTest(ok=ok, message=message)


# --------------------------------------------------------------------------------------
# Conversations
# --------------------------------------------------------------------------------------


@router.get("/conversations", response_model=AiConversationList)
def list_conversations(store: StoreDep, settings: SettingsDep) -> AiConversationList:
    loop.ensure_enabled(settings)
    screen = loop.screener(store.peek(), settings)
    items = [
        AiConversationSummary.model_validate(s.model_dump() | {"title": screen(s.title)})
        for s in conversations(store).list()
    ]
    return AiConversationList(items=items)


def _pick_model(provider_id: str, wanted: str | None, settings: AppSettings) -> str:
    provider = get_provider(provider_id)
    models = list(provider.status(settings).models) if provider else []
    model = wanted or (settings.ai_model if provider_id == settings.ai_provider else "")
    if models and model not in models:
        model = models[0]
    return model or settings.ai_model


@router.post("/conversations", response_model=AiConversation, status_code=201)
def create_conversation(
    body: AiConversationCreate, store: StoreDep, ds: DatasetDep, settings: SettingsDep
) -> dict[str, Any]:
    """New conversation. Unset fields come from the AI settings (chats, name hiding,
    provider, model, effort)."""
    loop.ensure_enabled(settings)
    provider_id = body.provider or settings.ai_provider
    if get_provider(provider_id) is None:
        raise ValidationApiError(f"Unknown AI provider {provider_id!r}.")
    if body.chat_ids is None:
        scope = sorted(set(settings.ai_default_scope))
    else:
        scope = _chat_folders(ds, body.chat_ids)
    conv = conversations(store).create(
        provider=provider_id,
        model=_pick_model(provider_id, body.model, settings),
        effort=body.effort or settings.ai_effort,
        hide_names=settings.ai_hide_names if body.hide_names is None else body.hide_names,
        scope=scope,
        mode=body.mode,
        title=body.title,
    )
    return loop.present(conv, ds, settings)


@router.get("/conversations/{conv_id}", response_model=AiConversation)
def get_conversation(conv_id: str, store: StoreDep, settings: SettingsDep) -> dict[str, Any]:
    loop.ensure_enabled(settings)
    return loop.present(conversations(store).get(conv_id), store.peek(), settings)


@router.patch("/conversations/{conv_id}", response_model=AiConversation)
def patch_conversation(
    conv_id: str,
    body: AiConversationPatch,
    store: StoreDep,
    ds: DatasetDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Rename, change the allowed chats, or (before the first message) name hiding."""
    loop.ensure_enabled(settings)
    cs = conversations(store)
    conv = cs.get(conv_id)
    if body.hide_names is not None and body.hide_names != conv.hide_names:
        if conv.names_locked:
            raise ApiError(
                "names_locked",
                "Name hiding is fixed once a conversation has started. Start a new one.",
                409,
            )
        conv.hide_names = body.hide_names
    if body.title is not None and body.title.strip():
        conv.title = body.title.strip()[:80]
    if body.chat_ids is not None:
        scope = _chat_folders(ds, body.chat_ids)
        if scope != conv.scope:
            conv.scope = scope
            if conv.turns:
                conv.pending_note = loop.scope_note(len(resolve_scope(ds, scope)))
    cs.save(conv)
    return loop.present(conv, ds, settings)


@router.delete("/conversations/{conv_id}", response_model=AiDeleted)
def delete_conversation(conv_id: str, store: StoreDep, settings: SettingsDep) -> AiDeleted:
    loop.ensure_enabled(settings)
    loop.cancel(conv_id)
    conversations(store).delete(conv_id)
    return AiDeleted(deleted=1)


@router.delete("/conversations", response_model=AiDeleted)
def delete_all_conversations(store: StoreDep, settings: SettingsDep) -> AiDeleted:
    loop.ensure_enabled(settings)
    cs = conversations(store)
    for s in cs.list():
        loop.cancel(s.id)
    return AiDeleted(deleted=cs.delete_all())


# --------------------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------------------


async def _sse(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[dict[str, str]]:
    async for ev in events:
        yield {"event": str(ev["type"]), "data": json.dumps(ev, ensure_ascii=False)}


@router.post(
    "/conversations/{conv_id}/messages",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": "SSE stream of `text`, `tool_start`, `tool_end`, `usage` and "
            "`error` events, then one `done` event. Each `data` is a JSON object with a "
            "`type` field.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def send_message(
    conv_id: str, body: AiMessageCreate, store: StoreDep, ds: DatasetDep, settings: SettingsDep
) -> EventSourceResponse:
    """Send a message and stream the answer. Problems found before the answer starts
    (feature off, provider not ready, an answer already running) are normal HTTP errors."""
    events = await loop.run_turn(
        conversations(store), conv_id, body.text, dataset=ds, settings=settings
    )
    return EventSourceResponse(_sse(events), ping=15)


@router.post("/conversations/{conv_id}/stop", response_model=AiDeleted)
def stop_answer(conv_id: str, store: StoreDep, settings: SettingsDep) -> AiDeleted:
    """Stop the running answer (``deleted`` = 1 if one was running)."""
    loop.ensure_enabled(settings)
    conversations(store).get(conv_id)  # 404 for unknown ids
    return AiDeleted(deleted=int(loop.cancel(conv_id)))


@router.get("/conversations/{conv_id}/last-request", response_model=AiLastRequest)
def last_request(conv_id: str, store: StoreDep, settings: SettingsDep) -> dict[str, Any]:
    """What the last answer sent to the provider: system prompt, the message, and every
    tool call with its result, exactly as sent (pseudonymized when names were hidden)."""
    loop.ensure_enabled(settings)
    conv = conversations(store).get(conv_id)
    if conv.last_request is None:
        raise NotFoundError("Nothing was sent in this conversation yet.")
    screen = loop.screener(store.peek(), settings)
    data: dict[str, Any] = loop.screen_value(screen, conv.last_request)
    return data
