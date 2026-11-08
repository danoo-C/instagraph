"""Settings models (spec §5.11).

``SettingsUpdate`` is a partial version of :class:`AppSettings` (every field optional,
unknown fields rejected) built from AppSettings' own fields, so it never drifts.
"""

from typing import TYPE_CHECKING, Any

from pydantic import ConfigDict, Field, create_model

from instagraph.schemas.common import ApiModel, PersonRef
from instagraph.store.app_settings import AppSettings
from instagraph.store.dataset import Category

if TYPE_CHECKING:
    SettingsUpdate = AppSettings
else:
    _fields: dict[str, Any] = {
        name: (info.annotation | None, Field(default=None, description=info.description))
        for name, info in AppSettings.model_fields.items()
    }
    SettingsUpdate = create_model(
        "SettingsUpdate",
        __config__=ConfigDict(extra="forbid"),
        __doc__="Partial settings update: only the fields sent are changed.",
        **_fields,
    )


class SettingsResponse(ApiModel):
    settings: AppSettings
    dataset_version: int
    rederived: bool = Field(
        default=False, description="True if the dataset was re-derived by this update"
    )
    redacted: bool = Field(
        default=False,
        description="Anonymize is on: name-bearing fields (merges, owner name, custom "
        "terms) are hidden; sending them back unchanged keeps the stored values",
    )


class SettingsPerson(ApiModel):
    person: PersonRef
    display_name: str
    aliases: list[str] = Field(description="Empty when anonymized")
    message_count: int
    is_deleted_account: bool = False


class SettingsPeople(ApiModel):
    dataset_version: int
    items: list[SettingsPerson]


class SettingsThread(ApiModel):
    thread_id: int
    title: str
    folder: str | None = Field(description="Merge/split key; null when anonymized")
    category: Category
    is_group: bool
    message_count: int
    participants: list[PersonRef]


class SettingsThreads(ApiModel):
    dataset_version: int
    items: list[SettingsThread]
