"""AI assistant API models (docs/ai-assistant.md A8.5)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from instagraph.schemas.common import ApiModel

Effort = Literal["low", "medium", "high"]


class AiProviderInfo(ApiModel):
    id: str
    label: str
    available: bool
    message: str = ""
    models: list[str] = Field(default_factory=list)


class AiKeyInfo(ApiModel):
    set: bool
    source: Literal["env", "file"] | None = None
    hint: str = ""
    """Last 4 characters only; the key itself is never returned."""


class AiKeySave(ApiModel):
    key: str = Field(min_length=1, max_length=500)


class AiKeyTest(ApiModel):
    ok: bool
    message: str


class AiStatusResponse(ApiModel):
    enabled: bool
    confirmed_notice: bool
    has_data: bool
    provider: str
    """The provider selected in settings."""
    model: str
    effort: Effort
    hide_names_default: bool
    default_chat_ids: list[int]
    providers: list[AiProviderInfo]
    """Every provider found on this installation (the public build: Anthropic API only)."""
    api_key: AiKeyInfo
    """The Anthropic API key (Anthropic provider)."""


class AiScopeChat(ApiModel):
    chat_id: int
    title: str


class AiToolActivity(ApiModel):
    id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    is_error: bool = False
    truncated: bool = False


class AiUsage(ApiModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class AiTurn(ApiModel):
    user_text: str
    answer: str
    tools: list[AiToolActivity]
    usage: AiUsage
    status: Literal["running", "done", "stopped", "error"]
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class AiConversationSummary(ApiModel):
    id: str
    title: str
    mode: str
    provider: str
    hide_names: bool
    scope_size: int
    turns: int
    updated_at: datetime


class AiConversationList(ApiModel):
    items: list[AiConversationSummary]


class AiConversation(ApiModel):
    id: str
    title: str
    mode: Literal["ask", "report"]
    provider: str
    model: str
    effort: Effort
    hide_names: bool
    names_locked: bool
    """Name hiding can't change once the first message was sent."""
    running: bool
    scope_chats: list[AiScopeChat]
    scope_missing: int
    """Allowed chats that no longer exist in the current dataset."""
    turns: list[AiTurn]
    created_at: datetime
    updated_at: datetime


class AiConversationCreate(ApiModel):
    chat_ids: list[int] | None = None
    """Allowed chats; ``None`` = the default scope from settings."""
    hide_names: bool | None = None
    """``None`` = the default from settings."""
    provider: str | None = None
    model: str | None = None
    effort: Effort | None = None
    mode: Literal["ask", "report"] = "ask"
    title: str | None = Field(default=None, max_length=200)


class AiConversationPatch(ApiModel):
    title: str | None = Field(default=None, max_length=200)
    chat_ids: list[int] | None = None
    hide_names: bool | None = None


class AiMessageCreate(ApiModel):
    text: str = Field(min_length=1, max_length=100_000)


class AiToolCall(ApiModel):
    name: str
    args: dict[str, Any]
    result: Any
    is_error: bool


class AiLastRequest(ApiModel):
    provider: str
    model: str
    system_prompt: str
    user_message: str
    tools: list[str]
    tool_calls: list[AiToolCall]


class AiDeleted(ApiModel):
    deleted: int
