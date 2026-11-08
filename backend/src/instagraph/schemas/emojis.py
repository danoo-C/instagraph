"""Emojis tab (spec §6.6, §7.2) and Overview top-emojis (§7.1 #13) response models.

Emojis are not treated as private text: they stay visible in anonymize mode (message
snippets in :class:`MessageRef` are still null).
"""

from pydantic import Field

from instagraph.schemas.common import (
    ApiModel,
    ApiResponse,
    CategoryChart,
    MessageQuery,
    MessageRef,
    PersonRef,
    TimeSeries,
)


class EmojiCount(ApiModel):
    emoji: str
    count: int
    category: str | None = None
    score: float | None = Field(default=None, description="Log-odds z-score (signature)")
    drilldown: MessageQuery | None = None


class EmojiChain(ApiModel):
    length: int
    emojis: str
    message: MessageRef


class PersonEmojiStats(ApiModel):
    person: PersonRef
    text_messages: int
    total: int
    per_message: float = Field(description="Emojis per text message")
    with_emoji_rate: float = Field(description="Share of text messages with ≥1 emoji")
    unique: int
    emoji_only_messages: int
    emoticons: int
    top: list[EmojiCount] = Field(description="Top 20")
    signature: EmojiCount | None = None
    longest_chain: EmojiChain | None = None
    drilldown: MessageQuery


class FirstUse(ApiModel):
    emoji: str
    message: MessageRef


class EmojisResponse(ApiResponse):
    chat_id: int
    merge_skin_tones: bool
    merge_gendered: bool
    total: int
    unique: int
    emoji_only_messages: int
    people: list[PersonEmojiStats]
    top: list[EmojiCount] = Field(description="Top 20 overall")
    longest_chain: EmojiChain | None = None
    usage_over_time: TimeSeries = Field(description="Emojis per bucket, one series per person")
    top_over_time: TimeSeries = Field(description="Stream graph: one series per top-10 emoji")
    first_use: list[FirstUse] = Field(description="First use of each top emoji")
    emoticons: CategoryChart = Field(description="categories = emoticons; series per person")
    categories: CategoryChart = Field(description="categories = emoji categories; per person")
    hearts_over_time: TimeSeries = Field(description="Heart emojis per bucket, per person")


class OverviewEmojisResponse(ApiResponse):
    total: int
    unique: int
    top: list[EmojiCount] = Field(description="Top emojis overall (everyone)")
    mine: list[EmojiCount] = Field(description="Top emojis sent by the owner")
    received: list[EmojiCount] = Field(description="Top emojis sent by others")
