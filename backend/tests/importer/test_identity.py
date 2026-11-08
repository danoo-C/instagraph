"""Owner detection, people resolution and thread grouping."""

from instagraph.importer.identity import (
    SourceInfo,
    folder_prefix,
    is_deleted_name,
    resolve_identity,
)
from instagraph.store.app_settings import PeopleMerge, ThreadMerge, ThreadSplit


def src(
    idx: int,
    folder: str,
    participants: list[str],
    senders: dict[str, tuple[int, int]] | None = None,
    *,
    title: str | None = None,
    category: str = "inbox",
    first: int | None = None,
    last: int | None = None,
    still: bool = True,
) -> SourceInfo:
    senders = senders if senders is not None else {p: (1, idx * 10) for p in participants}
    lasts = [v[1] for v in senders.values()]
    return SourceInfo(
        source_idx=idx,
        folder=folder,
        category=category,  # type: ignore[arg-type]
        title=title,
        participants=participants,
        is_still_participant=still,
        first_ts_ms=first if first is not None else (min(lasts) if lasts else None),
        last_ts_ms=last if last is not None else (max(lasts) if lasts else None),
        sender_stats=senders,
    )


def names(ident: object) -> dict[str, int]:
    return ident.name_person  # type: ignore[attr-defined, no-any-return]


def test_helpers() -> None:
    assert folder_prefix("jananovakova_123456") == "jananovakova"
    assert folder_prefix("a_b_12") == "a_b"
    assert folder_prefix("plain") == "plain"
    assert is_deleted_name("Instagram User")
    assert is_deleted_name("  ")
    assert is_deleted_name("")
    assert not is_deleted_name("Jana")


def test_owner_most_threads() -> None:
    sources = [
        src(0, "jana_1", ["Jana", "Daniel"]),
        src(1, "peter_2", ["Peter", "Daniel"]),
        src(2, "eva_3", ["Eva", "Daniel"]),
    ]
    ident = resolve_identity(sources)
    assert ident.owner_method == "most_threads"
    assert ident.owner_name == "Daniel"
    owner = ident.people[ident.owner_id]  # type: ignore[index]
    assert owner.is_owner and owner.display_name == "Daniel"
    assert sum(p.is_owner for p in ident.people) == 1


def test_owner_personal_information_and_settings_override() -> None:
    sources = [src(0, "jana_1", ["Jana", "Daniel"]), src(1, "peter_2", ["Peter", "Daniel"])]
    ident = resolve_identity(sources, personal_name="Jana")
    assert (ident.owner_name, ident.owner_method) == ("Jana", "personal_information")
    ident = resolve_identity(sources, personal_name="Jana", owner_name="peter")
    assert (ident.owner_name, ident.owner_method) == ("Peter", "settings")
    assert ident.auto_owner_id == ident.name_person["Jana"]
    # Unknown names fall back to detection.
    ident = resolve_identity(sources, personal_name="Nobody", owner_name="Ghost")
    assert (ident.owner_name, ident.owner_method) == ("Daniel", "most_threads")


def test_same_name_is_same_person_across_threads() -> None:
    sources = [src(0, "a_1", ["Jana", "Me"]), src(1, "group_2", ["Jana", "Me", "Peter"])]
    ident = resolve_identity(sources)
    assert len(ident.people) == 3
    jana = names(ident)["Jana"]
    assert all(jana in t.participants for t in ident.threads)
    group = next(t for t in ident.threads if t.folder == "group_2")
    assert group.is_group
    assert not next(t for t in ident.threads if t.folder == "a_1").is_group


def test_people_merge_uses_latest_name_or_explicit_display() -> None:
    sources = [
        src(0, "jana_1", ["Jana", "Me"], {"Jana": (5, 100), "Me": (5, 100)}),
        src(1, "jananovakova_2", ["Jana Nováková", "Me"], {"Jana Nováková": (5, 900)}),
    ]
    ident = resolve_identity(sources, people_merges=[PeopleMerge(names=["Jana", "Jana Nováková"])])
    pid = names(ident)["Jana"]
    assert names(ident)["Jana Nováková"] == pid
    person = ident.people[pid]
    assert person.display_name == "Jana Nováková"
    assert person.aliases == ["Jana", "Jana Nováková"]
    ident = resolve_identity(
        sources,
        people_merges=[PeopleMerge(names=["Jana", "Jana Nováková"], display_name="Janka")],
    )
    assert ident.people[names(ident)["Jana"]].display_name == "Janka"


def test_deleted_accounts_are_per_thread() -> None:
    sources = [
        src(0, "instagramuser_1", ["Instagram User", "Me"]),
        src(1, "instagramuser_2", ["Instagram User", "Me"]),
        src(2, "group_3", ["", "Me", "Jana"]),
    ]
    ident = resolve_identity(sources)
    assert len(ident.threads) == 3  # never merged although prefix + set match
    deleted = [p for p in ident.people if p.is_deleted_account]
    assert len(deleted) == 3
    assert len({p.person_id for p in deleted}) == 3
    assert {p.display_name for p in deleted} == {
        "Instagram User (instagramuser_1)",
        "Instagram User (instagramuser_2)",
        "Instagram User (group_3)",
    }
    t0 = ident.source_thread[0]
    assert ident.person_for("Instagram User", t0) == ident.deleted_person[t0]
    assert ident.person_for("", t0) == ident.deleted_person[t0]
    assert ident.owner_name == "Me"  # deleted pseudo-people are never the owner


def test_thread_grouping_merge_and_split() -> None:
    sources = [
        src(0, "jana_111", ["Jana", "Me"], first=10, last=20, title="Old"),
        src(1, "jana_222", ["Me", "Jana"], first=30, last=40, title="New", category="archived"),
        src(2, "jana_333", ["Jana", "Me", "Peter"], first=50, last=60),
        src(3, "peter_444", ["Peter", "Me"], first=5, last=6),
    ]
    ident = resolve_identity(sources)
    assert len(ident.threads) == 3
    merged = ident.threads[ident.source_thread[0]]
    assert ident.source_thread[0] == ident.source_thread[1]
    assert merged.title == "New"  # newest source wins
    assert merged.folder == "jana_222"
    assert merged.folders == ["jana_111", "jana_222"]
    assert merged.category == "inbox"  # inbox beats archived
    assert ident.threads_merged == 1

    split = resolve_identity(sources, thread_splits=[ThreadSplit(folders=["jana_111", "jana_222"])])
    assert len(split.threads) == 4
    assert split.threads_merged == 0

    forced = resolve_identity(
        sources, thread_merges=[ThreadMerge(folders=["jana_333", "peter_444"])]
    )
    assert forced.source_thread[2] == forced.source_thread[3]
    assert len(forced.threads) == 2


def test_alias_merge_enables_thread_merge() -> None:
    sources = [
        src(0, "jana_1", ["Jana", "Me"], first=1, last=2),
        src(1, "jana_2", ["Jana N", "Me"], first=3, last=4),
    ]
    assert len(resolve_identity(sources).threads) == 2
    ident = resolve_identity(sources, people_merges=[PeopleMerge(names=["Jana", "Jana N"])])
    assert len(ident.threads) == 1


def test_untitled_thread_gets_participant_title_and_reaction_actors_are_people() -> None:
    s = src(0, "x_1", ["Jana", "Me"])
    s.other_names = {"Lurker"}
    ident = resolve_identity([s, src(1, "y_2", ["Me", "Eva"], title="Eva")], owner_name="Me")
    assert ident.threads[ident.source_thread[0]].title == "Jana"
    assert "Lurker" in ident.name_person


def test_empty() -> None:
    ident = resolve_identity([])
    assert ident.people == [] and ident.threads == []
    assert ident.owner_id is None and ident.owner_method == "none"
