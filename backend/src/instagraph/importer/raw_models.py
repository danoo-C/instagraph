"""Pydantic v2 models of the raw thread JSON (spec §3.3).

All models use ``extra="allow"`` so unknown keys never break parsing; they are collected
by :func:`collect_unknown_keys` as dotted paths (``"messages[].foo"``,
``"messages[].share.bar"``, ``"participants[].baz"``) for the import report (§5.4).
Every field except ``participants`` and ``messages`` is optional.
"""

from collections import Counter
from typing import Any

from pydantic import BaseModel, ConfigDict

__all__ = [
    "RawJoinableMode",
    "RawMedia",
    "RawMessage",
    "RawParticipant",
    "RawReaction",
    "RawShare",
    "RawThread",
    "RawThreadHeader",
    "RawUser",
    "collect_unknown_keys",
]


class _Raw(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=False, populate_by_name=True)


class RawParticipant(_Raw):
    name: str | None = None


class RawUser(_Raw):
    """Entry of ``users`` on old ``Subscribe``/``Unsubscribe`` messages."""

    name: str | None = None


class RawReaction(_Raw):
    reaction: str | None = None
    actor: str | None = None
    timestamp: int | None = None
    """Seconds (sometimes milliseconds); may be missing."""


class RawMedia(_Raw):
    uri: str | None = None
    creation_timestamp: int | None = None


class RawShare(_Raw):
    link: str | None = None
    share_text: str | None = None
    original_content_owner: str | None = None
    profile_share_username: str | None = None
    profile_share_name: str | None = None


class RawMessage(_Raw):
    sender_name: str | None = None
    timestamp_ms: int | None = None
    content: str | None = None
    reactions: list[RawReaction] | None = None
    photos: list[RawMedia] | None = None
    videos: list[RawMedia] | None = None
    audio_files: list[RawMedia] | None = None
    gifs: list[RawMedia] | None = None
    files: list[RawMedia] | None = None
    sticker: RawMedia | None = None
    share: RawShare | None = None
    call_duration: int | None = None
    missed: bool | None = None
    is_unsent: bool | None = None
    is_geoblocked_for_viewer: bool | None = None
    is_unsent_image_by_messenger_kid_parent: bool | None = None
    is_taken_down: bool | None = None
    bumped_message_metadata: dict[str, Any] | None = None
    type: str | None = None
    """Old exports: ``Generic`` / ``Share`` / ``Call`` / ``Subscribe`` / ``Unsubscribe``."""
    users: list[RawUser] | None = None


class RawJoinableMode(_Raw):
    mode: int | None = None
    link: str | None = None


class _ThreadFields(_Raw):
    participants: list[RawParticipant]
    title: str | None = None
    is_still_participant: bool | None = None
    thread_path: str | None = None
    thread_type: str | None = None
    magic_words: list[Any] | None = None
    joinable_mode: RawJoinableMode | None = None
    image: RawMedia | None = None


class RawThread(_ThreadFields):
    """A whole ``message_N.json`` file."""

    messages: list[RawMessage]


class RawThreadHeader(_ThreadFields):
    """A thread file with messages left raw (validated one by one by the parser so that
    a single malformed message can't fail the whole file)."""

    messages: list[Any]


_NESTED: dict[str, str] = {
    # field name → dotted segment used in the key
    "participants": "participants[]",
    "messages": "messages[]",
    "reactions": "reactions[]",
    "photos": "photos[]",
    "videos": "videos[]",
    "audio_files": "audio_files[]",
    "gifs": "gifs[]",
    "files": "files[]",
    "users": "users[]",
    "sticker": "sticker",
    "share": "share",
    "joinable_mode": "joinable_mode",
    "image": "image",
}


def collect_unknown_keys(
    model: BaseModel, counter: Counter[str] | None = None, prefix: str = ""
) -> Counter[str]:
    """Add every unknown key of ``model`` (recursively) to ``counter`` and return it.

    Keys are dotted paths; list levels end with ``[]``: ``"foo"`` (top level),
    ``"messages[].foo"``, ``"messages[].reactions[].bar"``. ``prefix`` is prepended
    (e.g. ``"messages[]"`` when collecting a single message).
    """
    if counter is None:
        counter = Counter()
    dot = f"{prefix}." if prefix else ""
    extra = model.model_extra
    if extra:
        for key in extra:
            counter[f"{dot}{key}"] += 1
    for name, segment in _NESTED.items():
        if name not in type(model).model_fields:
            continue
        value = getattr(model, name, None)
        if value is None:
            continue
        child_prefix = f"{dot}{segment}"
        if isinstance(value, BaseModel):
            collect_unknown_keys(value, counter, child_prefix)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, BaseModel):
                    collect_unknown_keys(item, counter, child_prefix)
    return counter
