"""Deterministic fake Instagram JSON export generator.

Writes files in the *raw* Instagram export format (mojibake-encoded strings, newest-first
messages, threads split into ``message_N.json`` files of at most 10,000 messages) so that
the real importer can be exercised end-to-end. Threads are generated and written one at a
time, so memory use is bounded by the largest thread.
"""

from __future__ import annotations

import io
import math
import random
import re
import unicodedata
import zipfile
from bisect import bisect
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import accumulate
from pathlib import Path
from typing import Any, Literal

import orjson

__all__ = ["MESSAGES_PER_FILE", "generate_export", "mojibake", "zip_export"]

MESSAGES_PER_FILE = 10_000
Layout = Literal["new", "old"]
JsonObj = dict[str, Any]

_NON_ASCII = re.compile(rb"[\x80-\xff]")
_ESC = {i: b"\\u00%02x" % i for i in range(0x80, 0x100)}


def mojibake(s: str) -> str:
    """Encode a string the way Instagram does (UTF-8 bytes read as Latin-1)."""
    return s.encode("utf-8").decode("latin-1")


def _dump_instagram(obj: JsonObj) -> bytes:
    """JSON-dump with every string mojibake-encoded and escaped as ``\\u00XX``.

    The UTF-8 bytes of the normal JSON are exactly the Latin-1 code points of the mojibake
    strings, so each non-ASCII byte becomes one ``\\u00XX`` escape (like real exports).
    """
    raw = orjson.dumps(obj, option=orjson.OPT_INDENT_2)
    return _NON_ASCII.sub(lambda m: _ESC[m[0][0]], raw)


# --------------------------------------------------------------------------- vocab

_FIRST = [
    "Jana", "Peter", "Lucia", "Martin", "Zuzana", "Tomáš", "Katarína", "Michal", "Veronika",
    "Ján", "Simona", "Lukáš", "Petra", "Matúš", "Barbora", "Ondřej", "Tereza", "Jiří",
    "Eliška", "Šimon", "Natália", "Dávid", "Ema", "Samuel", "Kristína", "Adam", "Nikola",
    "Filip", "Michaela", "Jakub", "Sophie", "James", "Emily", "Oliver", "Chloe", "Liam",
    "Anna", "Marek", "Dominika", "Radoslav", "Ľubica", "Štefan", "Žofia", "Igor",
]  # fmt: skip
_LAST = [
    "Nováková", "Kováč", "Horváthová", "Varga", "Tóthová", "Baláž", "Szabó", "Molnárová",
    "Dvořák", "Černá", "Procházka", "Kučerová", "Smith", "Brown", "Šťastný", "Ďurica",
    "Mrázová", "Králik", "Benková", "Hudák", "Wilson", "Taylor", "Krejčí", "Pokorná",
]  # fmt: skip

_SK = [
    "ahoj", "čau, ako sa máš?", "dobre, a ty?", "kde si?", "idem neskôr", "už som doma",
    "to je super", "nevieeem", "uvidíme", "zajtra máš čas?", "môžeme ísť na kávu",
    "ďakujem", "prepáč, nestihla som", "ten film bol úžasný", "ešte stále v práci 😩",
    "dobrú noc", "dobré ráno ☀️", "kedy prídeš?", "počkaj chvíľu", "čo robíš večer?",
    "šťastnú cestu", "veľa šťastia na skúške!", "nemôžem teraz, zavolám ti", "áno", "nie",
    "teším sa", "to snáď nie", "ľúbim ťa ❤️", "mňam, to vyzerá dobre", "už spíš?",
]  # fmt: skip
_SK_PLAIN = [
    "ahoj", "cau", "co robis", "kde si", "idem uz", "jj", "nn", "ok", "dik", "v pohode",
    "zajtra?", "este nie", "uz som tu", "mas cas?", "pises?", "presne", "to je hrozne",
    "nechapem", "ideme von?", "som hore", "neviem", "dobre dobre", "aha", "jasne",
]  # fmt: skip
_CS = [
    "ahoj, jak se máš?", "díky moc", "to je pecka", "kdy přijedeš?", "už jsem na cestě",
    "nevím, uvidíme", "dneska nemůžu", "mám hlad", "jdu spát", "dobrou noc", "čau",
    "to je fakt dobrý", "počkej chvilku", "zítra ve tři?", "jo", "ne", "super",
]  # fmt: skip
_EN = [
    "hey", "what's up?", "omg", "lol that's amazing", "see you tomorrow", "on my way",
    "can't wait!", "thanks!", "sorry, just saw this", "good night", "where are you?",
    "did you see that?", "same", "ok", "sounds good", "let's do it", "miss you",
    "happy birthday!! 🎉", "that's wild", "brb",
]  # fmt: skip
_LAUGH = ["hahaha", "haha", "hahahahaha", "xd", "xD", "XDDD", "😂", "😂😂😂", "🤣", "lol", "jaja"]
_EMOJI = [
    "😂", "❤️", "😍", "🙏", "👍", "👍🏻", "👍🏽", "😭", "🥺", "🔥", "✨", "😅", "🙈", "🤔", "💀",
    "👩‍❤️‍👨", "👨‍👩‍👧", "🏳️‍🌈", "🇸🇰", "🇨🇿", "🇬🇧", "🤷‍♀️", "🤦🏼‍♂️", "💪🏿", "🥰", "😴", "☕",
]  # fmt: skip
_REACTIONS = ["❤", "😂", "😮", "😢", "😡", "👍", "🔥", "😍", "❤️", "👍🏼"]
_LINKS = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://sme.sk/clanok/123",
    "https://en.wikipedia.org/wiki/Bratislava", "https://open.spotify.com/track/4uLU6hMC",
    "https://www.idnes.cz/zpravy", "https://github.com/pola-rs/polars",
]  # fmt: skip
_ACCOUNTS = ["natgeo", "9gag", "memes.sk", "nasa", "bratislava.city", "spotify", "cats_of_ig"]
_SHARE_TEXT = ["😂😂", "toto si ty", "this is so us", "pozri", "mood", "", "omg"]
_GROUP_NAMES = [
    "Chata 2023 🏔️", "Spolubývajúci", "Rodina ❤️", "Gymko IV.A", "Weekend crew", "Výlet Tatry",
    "Futbal ⚽", "Kamoši", "Projekt FIIT", "Bratislava → Praha",
]  # fmt: skip

_SYSTEM_EN = {
    "rename": "{a} named the group {g}.",
    "add": "{a} added {b} to the group.",
    "left": "{a} left the group.",
    "photo": "{a} changed the group photo.",
}
#: Slovak system texts: (masculine, feminine) actor forms and the owner ("Vy") form.
_SYSTEM_SK: dict[str, tuple[str, str, str]] = {
    "rename": (
        "{a} pomenoval skupinu {g}.",
        "{a} pomenovala skupinu {g}.",
        "Pomenovali ste skupinu {g}.",
    ),
    "add": (
        "{a} pridal používateľa {b} do skupiny.",
        "{a} pridala používateľa {b} do skupiny.",
        "Pridali ste používateľa {b} do skupiny.",
    ),
    "left": ("{a} opustil skupinu.", "{a} opustila skupinu.", "Opustili ste skupinu."),
    "photo": (
        "{a} zmenil fotku skupiny.",
        "{a} zmenila fotku skupiny.",
        "Zmenili ste fotku skupiny.",
    ),
    "attachment": ("{a} poslal prílohu.", "{a} poslala prílohu.", "Poslali ste prílohu."),
}
#: Female first names (Slovak grammatical gender of the system texts).
_FEMALE = frozenset(
    {
        "Jana", "Lucia", "Zuzana", "Katarína", "Veronika", "Simona", "Petra", "Barbora",
        "Tereza", "Eliška", "Natália", "Ema", "Kristína", "Nikola", "Michaela", "Sophie",
        "Emily", "Chloe", "Anna", "Dominika", "Ľubica", "Žofia",
    }
)  # fmt: skip


def _sk_system(kind: str, a: _Person, b: _Person | None, g: str, me: _Person) -> str:
    masc, fem, you = _SYSTEM_SK[kind]
    tpl = you if a is me else fem if a.name.split()[0] in _FEMALE else masc
    return tpl.format(a=a.name, b=b.name if b else "", g=g)


_SPAN_START = int(datetime(2018, 3, 1, tzinfo=UTC).timestamp() * 1000)
_SPAN_END = int(datetime(2025, 10, 1, tzinfo=UTC).timestamp() * 1000)
_DAY = 86_400_000
_HOUR = 3_600_000


# --------------------------------------------------------------------------- model


@dataclass
class _Person:
    name: str
    lang: str  # "sk", "sk_plain", "cs", "en"
    peak_hour: int
    laugh: str
    emoji_rate: float
    double_text: float


@dataclass
class _Thread:
    category: str  # inbox / message_requests / archived_threads
    title: str
    members: list[_Person]  # excluding owner
    weight: float
    start: int
    end: int
    is_group: bool = False
    group_lang_sk: bool = False
    folder: str = ""
    extra_members: list[_Person] = field(default_factory=list)  # join later in groups


def _slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s) or "instagramuser"


def _make_person(rng: random.Random, name: str) -> _Person:
    lang = rng.choices(["sk", "sk_plain", "cs", "en"], weights=[5, 3, 2, 2])[0]
    night_owl = rng.random() < 0.25
    peak = rng.choice([22, 23, 0, 1]) if night_owl else rng.choice([9, 12, 17, 19, 20, 21])
    return _Person(
        name=name,
        lang=lang,
        peak_hour=peak,
        laugh=rng.choice(_LAUGH),
        emoji_rate=rng.uniform(0.03, 0.35),
        double_text=rng.uniform(0.1, 0.5),
    )


def _plan(rng: random.Random, owner: str) -> tuple[_Person, list[_Thread]]:
    names: list[str] = []
    while len(names) < 46:
        n = f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
        if n not in names and n != owner:
            names.append(n)
    people = [_make_person(rng, n) for n in names]
    me = _make_person(rng, owner)
    me.lang = "sk"
    contacts, extra = people[:40], people[40:]

    def window(min_days: int = 120) -> tuple[int, int]:
        a = rng.randint(_SPAN_START, _SPAN_END - min_days * _DAY)
        b = rng.randint(a + min_days * _DAY, _SPAN_END)
        return a, b

    threads: list[_Thread] = []
    for i, p in enumerate(contacts):
        w = 1.0 / (i + 1) ** 1.1  # Zipf-like activity
        if i == 0:
            threads.append(_Thread("inbox", p.name, [p], w, _SPAN_START, _SPAN_END))
        elif i == 1:  # same person in an old and a new thread
            mid = (_SPAN_START + _SPAN_END) // 2
            threads.append(_Thread("inbox", p.name, [p], w * 0.4, _SPAN_START, mid))
            threads.append(_Thread("inbox", p.name, [p], w * 0.6, mid + 30 * _DAY, _SPAN_END))
        elif i in (37, 38, 39):
            a, b = window(30)
            threads.append(_Thread("message_requests", p.name, [p], w * 0.3, a, b))
        elif i in (30, 31):
            a, b = window()
            threads.append(_Thread("archived_threads", p.name, [p], w, a, b))
        else:
            a, b = window()
            threads.append(_Thread("inbox", p.name, [p], w, a, b))

    ghost = _make_person(rng, "Instagram User")
    a, b = window()
    threads.append(_Thread("inbox", "Instagram User", [ghost], 0.03, a, b))

    for g in range(6):
        size = rng.randint(3, 8) - 1
        members = rng.sample(contacts[:25], size)
        a, b = window(365)
        t = _Thread("inbox", rng.choice(_GROUP_NAMES), members, 0.25 / (g + 1), a, b)
        t.is_group = True
        t.group_lang_sk = g % 2 == 1
        t.extra_members = [extra[g % len(extra)]]
        threads.append(t)

    used: dict[str, int] = {}
    for t in threads:
        base = _slug(t.title)
        used[base] = used.get(base, 0) + 1
        tid = 1_000_000_000_000 + rng.randrange(10**12) + used[base]
        t.folder = f"{base}_{tid}"
    return me, threads


# --------------------------------------------------------------------------- content


def _text(rng: random.Random, p: _Person) -> str:
    r = rng.random()
    if r < 0.08:
        return p.laugh if rng.random() < 0.7 else rng.choice(_LAUGH)
    if r < 0.10:
        return f"{rng.choice(_LINKS)}"
    pool = {"sk": _SK, "sk_plain": _SK_PLAIN, "cs": _CS, "en": _EN}[p.lang]
    if rng.random() < 0.07:
        pool = rng.choice((_SK, _CS, _EN))
    s = rng.choice(pool)
    if rng.random() < 0.25:
        s = f"{s} {rng.choice(pool)}"
    if rng.random() < 0.15:
        s = f"{s} {p.laugh}"
    if rng.random() < p.emoji_rate:
        e = rng.choice(_EMOJI)
        s = f"{s} {e * 3 if rng.random() < 0.2 else e}"
    return s


def _media(folder_uri: str, kind: str, ts: int, n: int) -> list[JsonObj]:
    ext = {"photos": "jpg", "videos": "mp4", "audio": "mp4", "gifs": "gif"}[kind]
    return [
        {"uri": f"{folder_uri}/{kind}/{ts}_{n}{i}.{ext}", "creation_timestamp": ts // 1000}
        for i in range(1)
    ]


class _ThreadGen:
    def __init__(self, rng: random.Random, me: _Person, t: _Thread, prefix: str, n: int) -> None:
        self.rng, self.me, self.t, self.n = rng, me, t, n
        self.uri = f"{prefix}/{t.category}/{t.folder}"
        self.media_uris: list[str] = []
        self.final_title = t.title
        self.phase = 1.0
        self.mean_gap = max(1, t.end - t.start) / max(1.0, n / 14)
        # Very busy threads: shrink in-session delays so everything fits the window.
        need_ms = max(1, n) * 700_000
        self.delay_scale = min(1.0, 0.4 * (t.end - t.start) / need_ms)
        self.hour_w = {
            p.name: list(accumulate(self._hour_weights(p.peak_hour)))
            for p in [me, *t.members, *t.extra_members]
        }

    @staticmethod
    def _hour_weights(peak: int) -> list[float]:
        w = []
        for h in range(24):
            d = min((h - peak) % 24, (peak - h) % 24)
            w.append(math.exp(-(d**2) / 18) + (0.02 if 2 <= h <= 6 else 0.08))
        return w

    def _session_start(self, who: _Person, after: int) -> int:
        """Next session start after ``after``: sessions are spread evenly in expectation
        over the thread window, with bursty phases and a weekly rhythm."""
        rng, t = self.rng, self.t
        if rng.random() < 0.02:  # switch activity phase (intense vs. quiet periods)
            self.phase = rng.choice((0.3, 1.0, 1.0, 2.5))
        mean_gap = self.mean_gap * self.phase
        day = after // _DAY * _DAY
        for _ in range(3):
            cand = day + int(rng.expovariate(1 / mean_gap)) // _DAY * _DAY
            wd = (cand // _DAY + 3) % 7  # 0 = Monday
            if wd >= 5 or rng.random() < 0.8:  # weekends are busier
                break
        day = min(max(cand, t.start // _DAY * _DAY), t.end)
        cum = self.hour_w[who.name]
        hour = min(bisect(cum, rng.random() * cum[-1]), 23)
        return day + hour * _HOUR + rng.randrange(_HOUR)

    def _message(self, sender: _Person, ts: int, active: list[_Person]) -> JsonObj:
        rng = self.rng
        m: JsonObj = {"sender_name": sender.name, "timestamp_ms": ts}
        r = rng.random()
        if r < 0.70:
            text = _text(rng, sender)
            if self.t.is_group and rng.random() < 0.03:
                other = rng.choice(active)
                text = f"@{_slug(other.name)} {text}"
            m["content"] = text
        elif r < 0.78:
            m["photos"] = _media(self.uri, "photos", ts, 0)
            self.media_uris.append(m["photos"][0]["uri"])
            if rng.random() < 0.2:
                m["content"] = _text(rng, sender)
        elif r < 0.81:
            m["videos"] = _media(self.uri, "videos", ts, 0)
        elif r < 0.83:
            m["audio_files"] = _media(self.uri, "audio", ts, 0)
        elif r < 0.85:
            m["gifs"] = [{"uri": f"https://media.giphy.com/media/{ts % 99991}/giphy.gif"}]
        elif r < 0.86:
            m["sticker"] = {"uri": f"{self.uri}/stickers/{ts % 997}.png"}
        elif r < 0.93:
            m.update(self._share(sender, ts))
        elif r < 0.95:
            m["content"] = "Liked a message"
        elif r < 0.96:
            m["content"] = ""
            m["is_unsent"] = True
        elif r < 0.97 and not self.t.is_group:
            return self._call(sender, ts)
        else:
            m["content"] = _text(rng, sender)
        if rng.random() < 0.06:
            reactors = [p for p in active if p is not sender] or [sender]
            k = 1 if not self.t.is_group else rng.randint(1, min(3, len(reactors)))
            m["reactions"] = [
                {
                    "reaction": rng.choice(_REACTIONS),
                    "actor": a.name,
                    "timestamp": ts // 1000 + rng.randint(1, 7200),
                }
                for a in rng.sample(reactors, k)
            ]
        m["is_geoblocked_for_viewer"] = False
        m["is_unsent_image_by_messenger_kid_parent"] = False
        return m

    def _share(self, sender: _Person, ts: int) -> JsonObj:
        rng = self.rng
        kind = rng.choices(["reel", "p", "profile", "ext"], weights=[5, 3, 1, 1])[0]
        acct = rng.choice(_ACCOUNTS)
        code = f"C{ts % 10**9:x}"
        share: JsonObj
        if kind == "profile":
            share = {"link": f"https://www.instagram.com/{acct}"}
        elif kind == "ext":
            share = {"link": rng.choice(_LINKS), "share_text": rng.choice(_SHARE_TEXT)}
        else:
            share = {
                "link": f"https://www.instagram.com/{kind}/{code}/",
                "share_text": rng.choice(_SHARE_TEXT),
                "original_content_owner": acct,
            }
        out: JsonObj = {"share": share}
        if kind != "ext":
            if self.t.group_lang_sk and rng.random() < 0.6:
                out["content"] = _sk_system("attachment", sender, None, "", self.me)
            else:
                out["content"] = f"{sender.name} sent an attachment."
        return out

    def _call(self, sender: _Person, ts: int) -> JsonObj:
        rng = self.rng
        video = rng.random() < 0.4
        r = rng.random()
        if r < 0.25:
            text = "You missed a video chat" if video else "You missed an audio call"
            dur = 0
        elif r < 0.5:
            text = (
                f"{sender.name} started a video chat"
                if video
                else (f"{sender.name} started an audio call")
            )
            dur = 0
        else:
            text = "Video chat ended" if video else "Audio call ended"
            dur = int(rng.expovariate(1 / 600)) + 5
        return {"sender_name": sender.name, "timestamp_ms": ts, "content": text,
                "call_duration": dur}  # fmt: skip

    def _system(self, kind: str, a: _Person, b: _Person | None, ts: int, g: str) -> JsonObj:
        if self.t.group_lang_sk and self.rng.random() < 0.6:
            text = _sk_system(kind, a, b, g, self.me)
        else:
            text = _SYSTEM_EN[kind].format(a=a.name, b=b.name if b else "", g=g)
        return {"sender_name": a.name, "timestamp_ms": ts, "content": text}

    def generate(self) -> list[JsonObj]:
        """Return messages ascending by timestamp."""
        rng, t, me = self.rng, self.t, self.me
        msgs: list[JsonObj] = []
        active = list(t.members)
        everyone = [me, *active]
        joiner = t.extra_members[0] if t.extra_members else None
        leaver = active[-1] if t.is_group and len(active) > 2 else None
        title = t.title
        events: list[tuple[float, str]] = []
        if t.is_group:
            events = sorted(
                [(0.0, "rename"), (0.3, "add"), (0.5, "photo"), (0.6, "rename"), (0.8, "left")]
            )
        ev_i = 0
        produced = 0
        while produced < self.n:
            frac = produced / self.n
            while ev_i < len(events) and events[ev_i][0] <= frac:
                kind = events[ev_i][1]
                ev_i += 1
                ts = (msgs[-1]["timestamp_ms"] + 60_000) if msgs else t.start
                actor = rng.choice(everyone)
                if kind == "rename":
                    title = rng.choice(_GROUP_NAMES) if ev_i > 1 else t.title
                    msgs.append(self._system("rename", actor, None, ts, title))
                elif kind == "add" and joiner is not None:
                    msgs.append(self._system("add", actor, joiner, ts, title))
                    active.append(joiner)
                elif kind == "left" and leaver is not None and leaver in active:
                    msgs.append(self._system("left", leaver, None, ts, title))
                    active.remove(leaver)
                elif kind == "photo":
                    msgs.append(self._system("photo", actor, None, ts, title))
                everyone = [me, *active]
                produced = len(msgs)
            # one bursty session
            starter = me if rng.random() < 0.45 else rng.choice(active)
            ts = self._session_start(starter, msgs[-1]["timestamp_ms"] if msgs else t.start)
            if msgs and ts <= msgs[-1]["timestamp_ms"]:
                ts = msgs[-1]["timestamp_ms"] + rng.randint(10 * 60_000, 3 * _HOUR)
            length = min(self.n - produced, max(1, int(rng.expovariate(1 / 14))))
            sender = starter
            for _ in range(length):
                msgs.append(self._message(sender, ts, everyone))
                produced += 1
                if rng.random() < sender.double_text:
                    ts += rng.randint(1_000, 25_000)  # double text
                else:
                    others = [p for p in everyone if p is not sender]
                    sender = rng.choice(others)
                    ts += int(
                        (rng.expovariate(1 / 90_000) + 2_000) * self.delay_scale
                    )  # reply delay
                    if rng.random() < 0.03:
                        ts += int(rng.randint(_HOUR, 10 * _HOUR) * self.delay_scale)  # slow reply
        self.final_title = title
        last = msgs[-1]["timestamp_ms"] if msgs else t.end
        if last > t.end:  # safety net: compress linearly into the window
            # Anchor at the window start when the first message is already late (e.g. a
            # 1-message thread), so the span is never zero.
            t0 = min(msgs[0]["timestamp_ms"], t.start)
            k = max(t.end - t0, 0) / max(last - t0, 1)
            prev = -1
            for m in msgs:
                m["timestamp_ms"] = max(prev + 1, t0 + int((m["timestamp_ms"] - t0) * k))
                prev = m["timestamp_ms"]
        return msgs[: self.n] if self.n else msgs


# --------------------------------------------------------------------------- writer


def _write_jpeg(path: Path, seed: int) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (8, 8), ((seed * 37) % 256, (seed * 91) % 256, (seed * 13) % 256))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    path.write_bytes(buf.getvalue())


def generate_export(
    out_dir: Path,
    *,
    messages: int = 20_000,
    seed: int = 42,
    layout: Layout = "new",
    owner: str = "Daniel",
    with_media_files: bool = False,
) -> Path:
    """Generate a fake Instagram JSON export under ``out_dir`` and return its root."""
    rng = random.Random(seed)
    me, threads = _plan(rng, owner)
    root = Path(out_dir)
    prefix = "your_instagram_activity/messages" if layout == "new" else "messages"

    total_w = sum(t.weight for t in threads)
    counts = [max(1, int(messages * t.weight / total_w)) for t in threads]
    counts[0] += messages - sum(counts)  # top contact absorbs rounding
    if counts[0] < 1:
        counts[0] = 1

    for t, n in zip(threads, counts, strict=True):
        trng = random.Random(f"{seed}:{t.folder}")
        gen = _ThreadGen(trng, me, t, prefix, n)
        asc = gen.generate()
        asc.reverse()  # newest first
        tdir = root / prefix / t.category / t.folder
        tdir.mkdir(parents=True, exist_ok=True)
        participants = [{"name": p.name} for p in [*t.members, *t.extra_members, me]]
        if not t.is_group:
            participants = [{"name": t.members[0].name}, {"name": me.name}]
        for i in range(0, max(len(asc), 1), MESSAGES_PER_FILE):
            doc: JsonObj = {
                "participants": participants,
                "messages": asc[i : i + MESSAGES_PER_FILE],
                "title": gen.final_title if t.is_group else t.title,
                "is_still_participant": True,
                "thread_path": f"{t.category}/{t.folder}",
                "magic_words": [],
            }
            if t.is_group:
                doc["joinable_mode"] = {"mode": 1, "link": ""}
            path = tdir / f"message_{i // MESSAGES_PER_FILE + 1}.json"
            path.write_bytes(_dump_instagram(doc))
        if with_media_files:
            for k, uri in enumerate(gen.media_uris[:3]):
                _write_jpeg(root / uri, seed + k)

    pi = root / "personal_information" / "personal_information" / "personal_information.json"
    pi.parent.mkdir(parents=True, exist_ok=True)
    pi.write_bytes(
        _dump_instagram(
            {
                "profile_user": [
                    {
                        "media_map_data": {},
                        "string_map_data": {
                            "Name": {"href": "", "value": owner, "timestamp": 0},
                            "Username": {"href": "", "value": _slug(owner), "timestamp": 0},
                        },
                    }
                ]
            }
        )
    )
    return root


def zip_export(root: Path, zip_path: Path) -> Path:
    """Zip an export folder (paths relative to the export root)."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for f in sorted(root.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(root).as_posix())
    return zip_path
