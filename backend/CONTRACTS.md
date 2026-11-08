# Backend contracts (for contributors / parallel agents)

Read `spec.md` for *what* to build; this file says *how to plug in*. Source docstrings
are the detailed reference.

## File ownership

| Area | Owner files | Rule |
|---|---|---|
| Foundation (frozen API) | `config.py`, `errors.py`, `main.py`, `textproc.py`, `testing.py`, `store/*`, `api/deps.py`, `api/filters.py`, `schemas/common.py`, `importer/types.py` | Don't edit. If you need a change, add a helper in your own module or ask (note it in your report). |
| Routes | `api/routes/<area>.py` | One module per area; create new ones freely. |
| Schemas | `schemas/<area>.py` | Area-specific Pydantic models. |
| Analysis | `analysis/<family>.py` | Pure functions over LazyFrames (§8.3 list). |
| Importer | `importer/*.py` (except `types.py`), `importer/locales/*.json` | |
| Media / resources / sample | `media/`, `resources/`, `sample/` | |
| Tests | `tests/<area>/test_*.py` | Don't edit `tests/conftest.py` (add local fixtures in your own `conftest.py` inside your test subfolder). |

**Absolute imports only** (`from instagraph.store.dataset import Dataset`). Ruff bans relative imports.

## Adding a route

Create `src/instagraph/api/routes/<area>.py` with a module-level `router`. `create_app()`
auto-includes every module in that package that has `router: APIRouter`. Never edit `main.py`.

```python
from fastapi import APIRouter
from instagraph.api.deps import Context
from instagraph.api.filters import is_counted_message
from instagraph.errors import ERROR_RESPONSES, NotFoundError
from instagraph.schemas.overview import OverviewResponse

router = APIRouter(prefix="/api/overview", tags=["overview"], responses=ERROR_RESPONSES)

@router.get("", response_model=OverviewResponse)
def overview(ctx: Context) -> OverviewResponse:          # plain def → runs in thread pool
    def compute() -> OverviewResponse:
        msgs = ctx.messages().filter(is_counted_message())
        ...
        return OverviewResponse(dataset_version=ctx.dataset_version, ...)
    return ctx.cached("overview", compute)               # extra=... for path/query params
```

Reference module: `api/routes/status.py`. Paths start with `/api/`. Use plain `def` for
CPU-bound work. Every endpoint has a typed `response_model`.

## Getting data

* `Context` (= `Annotated[AnalysisContext, Depends(get_context)]`, `api/deps.py`) gives:
  `ctx.dataset`, `ctx.filters` (`FilterParams`, `tz` already resolved & validated),
  `ctx.settings` (`AppSettings`), `ctx.dataset_version`, `ctx.owner_id`,
  `ctx.messages()`, `ctx.reactions()`, `ctx.person_ref(pid)` / `ctx.person_refs()`,
  `ctx.thread_title(tid)` (O(1), cached per dataset version + settings) /
  `ctx.thread_titles()`, `ctx.text(s)` (None when anonymized), `ctx.cached(...)`, and the
  chat-scope helpers below.
  It raises `NoDataError` → 409 automatically when nothing is imported.
* Other deps: `StoreDep`, `DatasetDep`, `SettingsDep`, `FiltersDep`.
* **Always** start from `filtered_messages(ds, f)` / `filtered_reactions(ds, f)` (or the
  ctx methods). They apply thread category/group, system, people and local-date filters
  and add `local_ts`, `local_date`, `hour` (0–23), `weekday` (ISO 1=Mon…7=Sun).
  - `exclude_system` drops `type == "system"` only; use `.filter(is_counted_message())`
    for "messages" (§6.1: excludes system **and** like), `is_text_message()` for text stats.
  - `people` filter = `sender_id ∈ people` (reactions: actor **or** target ∈ people).
  - **Chat scope** (`/api/chats/{chat_id}/...`): a chat the user opened always shows its
    data — the thread-category filters (`include_groups/requests/archived`) are IGNORED;
    date range, tz, `exclude_system` and `people` still apply. Start every chat route with
    `ctx = ctx.for_chat(chat_id)` (404 for an unknown chat; its `filters` =
    `ctx.chat_filters()`, so `ctx.cached` keys are normalized too) and get data via
    `ctx.chat_messages(chat_id, people=True|False)` / `ctx.chat_reactions(chat_id)`.
    `ctx.ensure_chat(id)` / `ctx.thread_row(id)` return the threads row (O(1)).
    Low level: `filtered_messages(ds, f, thread_id=...)`. The `/api/chats` list itself
    keeps applying the category filters.
* Table columns/dtypes: `store/dataset.py` (`*_SCHEMA`, enum tuples `MESSAGE_TYPES`,
  `CATEGORIES`, `SHARE_KINDS`, `CALL_KINDS`, `SYSTEM_EVENTS`, `MEDIA_KINDS`; Literal
  types `MessageType`, … for Pydantic). Read the module docstring for derived-column
  semantics (sessions/turns/reply latency are null for non-counted messages; ids unique
  across the dataset; emojis stored with FE0F stripped, skin tones kept).
* Emoji merge settings at query time: `textproc.normalize_emoji_expr(...)` with
  `ctx.settings.emoji_merge_skin_tones / emoji_merge_gendered`.
* Time buckets: `filters.bucket_start(expr, bucket)`, `bucket_range(start, end, bucket)`.
* Settings that affect derived fields (owner, merges, session gap, reply window) require
  re-derivation + `DatasetStore.replace()`/`bump_version()`.

## Schemas

Compose area models from `schemas/common.py`: `ApiResponse` (base with
`dataset_version`), `PersonRef`, `Tile`, `Series`, `TimeSeries`, `CategoryChart`,
`HeatmapData`, `CalendarData`, `TableData`, `MessageRef`, `MessageQuery` (drilldown to
the Message Browser — attach to every clickable count). Subclass `ApiModel`
(`extra="forbid"`). Units: durations in ms, ratios 0..1, dates ISO in display tz.
Never put real names/text in responses without going through `ctx.person_ref` /
`ctx.text` / `ctx.thread_title` (anonymize mode). Other identifying strings (shared
account names, group names in `system_payload`, file paths) must be masked or dropped
when `ctx.settings.anonymize` — `tests/api/test_anonymize_all.py` scans every GET.

## Errors

Raise `ApiError(code, message, status)` or `NoDataError` (409), `NotFoundError` (404),
`ValidationApiError` (422) from `instagraph.errors`. Every error (incl. FastAPI
validation and unknown routes) is rendered as `{"error": {"code", "message"}}`.

## Cache

`ctx.cached(endpoint, compute, extra=hashable)` — LRU keyed by (dataset_version,
endpoint, filters, settings hash, extra). Cached values are shared: don't mutate them.
Low level: `store.cache.response_cache`, `make_key`.

## Importer contract

Parser emits `importer.types.ParsedThread` / `ParsedMessage` / `ParsedReaction` /
`ParsedMedia`; the pipeline builds DataFrames, passes them through
`Dataset.from_frames(...)` (which `conform`s to the schemas) and installs with
`get_store().replace(dataset)`. Text helpers: `textproc.fix_mojibake`, `extract_urls`,
`tokenize_words`, `word_count`, `grapheme_count`, `extract_emojis`, `normalize_emoji`.
Polars word tokenization must use `textproc.WORD_PATTERN` (+ strip `KEYCAP_PATTERN`) so
every word count agrees (`tests/test_word_consistency.py`).
`testing.derive_conversation_fields` is the reference for session/turn/reply semantics
(the importer's vectorized Polars version must produce identical values).

## Testing

```python
from instagraph.testing import build_dataset, make_client

ds = build_dataset(people=[...], threads=[...], messages=[...], reactions=[...])
with make_client(tmp_path, ds) as client:
    ...
```

Fixtures in `tests/conftest.py`: `small_dataset` (1:1 + group + request thread, 20
messages; see its docstring), `client` (API with it loaded), `empty_client` (no data).

## Commands (run from `backend/`)

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy --strict src
.venv/bin/pytest
```

All four must pass. No network: don't `pip install` anything.

## Security (§10)

`main.py` rejects requests whose `Host` is not localhost/127.0.0.1/::1, the configured
`INSTAGRAPH_HOST` or `INSTAGRAPH_ALLOWED_HOSTS` (400 `invalid_host`, DNS rebinding), and
POST/PUT/PATCH/DELETE with a cross-site `Origin` (not same-origin, not in `cors_origins`) or
`Sec-Fetch-Site: cross-site` (403 `forbidden_origin`, CSRF). Requests without `Origin` pass.
`make_client` adds `testserver` to the allowed hosts; tests building apps directly must too.
