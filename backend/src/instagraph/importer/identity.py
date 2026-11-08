"""People, owner and thread identity resolution (spec §3.4, §5.11).

Input is one :class:`SourceInfo` per parsed thread folder *source* (the same folder can
appear in several zip parts → several sources). Output is an :class:`Identity`:
person ids, thread ids, the source → thread mapping and the owner.

Rules
-----
* **People.** ``sender_name`` (and participant / reaction-actor names) is the identity:
  the same name in two threads is the same person. ``AppSettings.people_merges`` joins
  several names into one person (display name = the merge's ``display_name`` or the
  name used most recently).
* **Deleted accounts.** ``"Instagram User"`` and empty names become one pseudo-person per
  *thread* (``Instagram User (<folder>)``, ``is_deleted_account``) and are never merged
  across threads.
* **Owner.** ``AppSettings.owner_name`` (if it matches a known name) → the name from
  ``personal_information.json`` (if it matches) → the person present in the most threads
  (ties: more messages, then name). The method is reported.
* **Threads.** Sources with the same folder-name prefix (folder without the trailing
  ``_<digits>`` id) and the same participant set (after people merges) are merged into
  one chat. Threads with a deleted account only merge sources of the *same* folder.
  ``AppSettings.thread_splits`` keeps listed folders apart; ``thread_merges`` joins
  listed folders. The newest source (latest message) gives the title / folder /
  ``owner_still_participant``; category priority is inbox > archived > request.
"""

import re
from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass, field

from instagraph.store.app_settings import PeopleMerge, ThreadMerge, ThreadSplit
from instagraph.store.dataset import Category

DELETED_ACCOUNT_NAME = "Instagram User"
_DELETED_TOKEN = "\x00deleted"
_FOLDER_ID_RE = re.compile(r"^(.*?)_\d+$")
_CATEGORY_PRIORITY: dict[str, int] = {"inbox": 0, "archived": 1, "request": 2}


def is_deleted_name(name: str | None) -> bool:
    """``True`` for deleted / deactivated accounts (``"Instagram User"`` or empty)."""
    if name is None:
        return True
    stripped = name.strip()
    return not stripped or stripped == DELETED_ACCOUNT_NAME


def folder_prefix(folder: str) -> str:
    """``"jananovakova_123456"`` → ``"jananovakova"`` (unchanged without an id suffix)."""
    m = _FOLDER_ID_RE.match(folder)
    return m.group(1) if m else folder


class _UnionFind[K: Hashable]:
    def __init__(self) -> None:
        self._parent: dict[K, K] = {}

    def add(self, item: K) -> None:
        self._parent.setdefault(item, item)

    def find(self, item: K) -> K:
        self.add(item)
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: K, b: K) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra

    def groups(self) -> dict[K, list[K]]:
        out: dict[K, list[K]] = {}
        for item in self._parent:
            out.setdefault(self.find(item), []).append(item)
        return out


@dataclass(slots=True)
class SourceInfo:
    """What identity resolution needs to know about one parsed thread source."""

    source_idx: int
    folder: str
    category: Category
    title: str | None
    participants: list[str]
    is_still_participant: bool = True
    source: str = ""
    first_ts_ms: int | None = None
    last_ts_ms: int | None = None
    sender_stats: dict[str, tuple[int, int]] = field(default_factory=dict)
    """Raw sender name → (message count, last timestamp ms)."""
    other_names: set[str] = field(default_factory=set)
    """Other names seen (reaction actors)."""


@dataclass(slots=True)
class PersonRecord:
    person_id: int
    display_name: str
    aliases: list[str]
    is_owner: bool = False
    is_deleted_account: bool = False


@dataclass(slots=True)
class ThreadRecord:
    thread_id: int
    title: str
    folder: str
    folders: list[str]
    category: Category
    participants: list[int]
    is_group: bool
    owner_still_participant: bool
    source_indices: list[int]


@dataclass(slots=True)
class Identity:
    people: list[PersonRecord]
    threads: list[ThreadRecord]
    source_thread: dict[int, int]
    """``source_idx`` → ``thread_id``."""
    name_person: dict[str, int]
    """Normal (non-deleted) raw name → ``person_id``."""
    deleted_person: dict[int, int]
    """``thread_id`` → pseudo-person id for deleted accounts in that thread."""
    owner_id: int | None
    owner_name: str | None
    owner_method: str
    """``settings`` / ``personal_information`` / ``most_threads`` / ``none``."""
    auto_owner_id: int | None
    """Owner as detected without the settings override."""
    auto_owner_method: str
    threads_merged: int
    """Number of thread folders folded into another chat."""

    def person_for(self, name: str | None, thread_id: int) -> int | None:
        """Person id for a raw name seen in ``thread_id``."""
        if is_deleted_name(name):
            return self.deleted_person.get(thread_id)
        assert name is not None
        return self.name_person.get(name)


def _match_name(name: str | None, names: Iterable[str]) -> str | None:
    """Exact match first, then case-insensitive / whitespace-trimmed."""
    if not name or is_deleted_name(name):
        return None
    pool = list(names)
    if name in pool:
        return name
    key = name.strip().casefold()
    for candidate in pool:
        if candidate.strip().casefold() == key:
            return candidate
    return None


def resolve_identity(
    sources: Sequence[SourceInfo],
    *,
    owner_name: str | None = None,
    personal_name: str | None = None,
    people_merges: Sequence[PeopleMerge] = (),
    thread_merges: Sequence[ThreadMerge] = (),
    thread_splits: Sequence[ThreadSplit] = (),
) -> Identity:
    """Resolve people, owner and threads (see module docstring)."""
    # ---- people clusters (normal names) -------------------------------------------
    names_uf: _UnionFind[str] = _UnionFind()
    last_seen: dict[str, int] = {}
    msg_count: dict[str, int] = {}
    for src in sources:
        for name in (*src.participants, *src.sender_stats, *src.other_names):
            if not is_deleted_name(name):
                names_uf.add(name)
        for name, (count, last) in src.sender_stats.items():
            msg_count[name] = msg_count.get(name, 0) + count
            last_seen[name] = max(last_seen.get(name, last), last)
    merge_display: dict[str, str] = {}
    for merge in people_merges:
        present = [n for n in merge.names if not is_deleted_name(n)]
        for n in present:
            names_uf.add(n)
        for n in present[1:]:
            names_uf.union(present[0], n)
        if merge.display_name and present:
            merge_display[present[0]] = merge.display_name
    clusters = names_uf.groups()
    # A merge entry may mention names that never occur; keep only clusters with real names.
    seen_names = set(last_seen) | {
        n for src in sources for n in (*src.participants, *src.other_names)
    }
    cluster_display: dict[str, str] = {}
    for root, cluster_names in clusters.items():
        real = [m for m in cluster_names if m in seen_names]
        if not real:
            continue
        display = next((merge_display[m] for m in cluster_names if m in merge_display), None)
        if display is None:
            display = max(real, key=lambda n: (last_seen.get(n, -1), n))
        cluster_display[root] = display
    ordered_roots = sorted(cluster_display, key=lambda r: (cluster_display[r].casefold(), r))
    root_pid = {root: i for i, root in enumerate(ordered_roots)}
    people: list[PersonRecord] = [
        PersonRecord(
            person_id=root_pid[root],
            display_name=cluster_display[root],
            aliases=sorted(m for m in clusters[root] if m in seen_names),
        )
        for root in ordered_roots
    ]
    name_person: dict[str, int] = {}
    for root, cluster_names in clusters.items():
        if root in root_pid:
            for m in cluster_names:
                name_person[m] = root_pid[root]

    # ---- thread grouping ----------------------------------------------------------
    split_folders = {f for s in thread_splits for f in s.folders}

    def person_key(name: str) -> str:
        return _DELETED_TOKEN if is_deleted_name(name) else names_uf.find(name)

    keys: list[Hashable] = []
    for src in sources:
        member_names = src.participants or list(src.sender_stats)
        pset = frozenset(person_key(n) for n in member_names)
        if src.folder in split_folders:
            keys.append(("split", src.folder))
        elif _DELETED_TOKEN in pset:
            keys.append(("folder", src.folder, pset))
        else:
            keys.append(("auto", folder_prefix(src.folder), pset))
    groups_uf: _UnionFind[Hashable] = _UnionFind()
    for key in keys:
        groups_uf.add(key)
    for merge_entry in thread_merges:
        wanted = set(merge_entry.folders)
        hits = [keys[i] for i, src in enumerate(sources) if src.folder in wanted]
        for k in hits[1:]:
            groups_uf.union(hits[0], k)
    grouped: dict[Hashable, list[int]] = {}
    for i, key in enumerate(keys):
        grouped.setdefault(groups_uf.find(key), []).append(i)

    def group_order(members: list[int]) -> tuple[int, str]:
        firsts = [t for i in members if (t := sources[i].first_ts_ms) is not None]
        return (min(firsts) if firsts else 2**62, min(sources[i].folder for i in members))

    group_list = sorted(grouped.values(), key=group_order)

    # ---- deleted-account pseudo people (one per thread) ----------------------------
    deleted_person: dict[int, int] = {}
    next_pid = len(people)
    for tid, members in enumerate(group_list):
        raw_deleted = sorted(
            {
                n
                for i in members
                for n in (
                    *sources[i].participants,
                    *sources[i].sender_stats,
                    *sources[i].other_names,
                )
                if is_deleted_name(n)
            }
        )
        if not raw_deleted:
            continue
        rep = _representative(sources, members)
        people.append(
            PersonRecord(
                person_id=next_pid,
                display_name=f"{DELETED_ACCOUNT_NAME} ({rep.folder})",
                aliases=sorted({DELETED_ACCOUNT_NAME, *(n for n in raw_deleted if n.strip())}),
                is_deleted_account=True,
            )
        )
        deleted_person[tid] = next_pid
        next_pid += 1

    def pid_of(name: str, tid: int) -> int | None:
        if is_deleted_name(name):
            return deleted_person.get(tid)
        return name_person.get(name)

    # ---- threads ---------------------------------------------------------------------
    threads: list[ThreadRecord] = []
    source_thread: dict[int, int] = {}
    threads_merged = 0
    thread_people: list[set[int]] = []
    for tid, members in enumerate(group_list):
        rep = _representative(sources, members)
        pids: set[int] = set()
        max_listed = 0
        for i in members:
            src = sources[i]
            source_thread[src.source_idx] = tid
            listed = {p for n in src.participants if (p := pid_of(n, tid)) is not None}
            max_listed = max(max_listed, len(listed))
            pids |= listed
            pids |= {p for n in src.sender_stats if (p := pid_of(n, tid)) is not None}
        folders = sorted({sources[i].folder for i in members})
        threads_merged += len(folders) - 1
        category = min(
            (sources[i].category for i in members), key=lambda c: _CATEGORY_PRIORITY.get(c, 9)
        )
        thread_people.append(pids)
        threads.append(
            ThreadRecord(
                thread_id=tid,
                title=rep.title or "",
                folder=rep.folder,
                folders=folders,
                category=category,
                participants=sorted(pids),
                is_group=(max_listed if max_listed else len(pids)) > 2,
                owner_still_participant=rep.is_still_participant,
                source_indices=[sources[i].source_idx for i in members],
            )
        )

    # ---- owner -----------------------------------------------------------------------
    all_names = list(name_person)
    auto_id: int | None = None
    auto_method = "none"
    matched = _match_name(personal_name, all_names)
    if matched is not None:
        auto_id = name_person[matched]
        auto_method = "personal_information"
    elif people:
        thread_hits: dict[int, int] = {}
        for pids in thread_people:
            for p in pids:
                thread_hits[p] = thread_hits.get(p, 0) + 1
        person_msgs: dict[int, int] = {}
        for name, count in msg_count.items():
            if name in name_person:
                pid = name_person[name]
                person_msgs[pid] = person_msgs.get(pid, 0) + count
        candidates = [p.person_id for p in people if not p.is_deleted_account]
        if candidates:
            auto_id = max(
                candidates,
                key=lambda p: (thread_hits.get(p, 0), person_msgs.get(p, 0), -p),
            )
            auto_method = "most_threads"
    owner_id, method = auto_id, auto_method
    matched = _match_name(owner_name, all_names)
    if matched is not None:
        owner_id, method = name_person[matched], "settings"
    owner_display: str | None = None
    if owner_id is not None:
        people[owner_id].is_owner = True
        owner_display = people[owner_id].display_name

    # Untitled threads: name them after the other participants.
    for t in threads:
        if not t.title:
            others = [people[p].display_name for p in t.participants if p != owner_id]
            t.title = ", ".join(others) or t.folder

    return Identity(
        people=people,
        threads=threads,
        source_thread=source_thread,
        name_person=name_person,
        deleted_person=deleted_person,
        owner_id=owner_id,
        owner_name=owner_display,
        owner_method=method,
        auto_owner_id=auto_id,
        auto_owner_method=auto_method,
        threads_merged=threads_merged,
    )


def _representative(sources: Sequence[SourceInfo], members: list[int]) -> SourceInfo:
    """The newest source of a group (latest message; ties → later source)."""
    best = max(members, key=lambda i: (sources[i].last_ts_ms or -1, sources[i].source_idx))
    return sources[best]
