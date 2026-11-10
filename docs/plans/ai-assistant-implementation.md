# Plan: AI assistant (public API provider + private Claude Code provider)

**Status:** In progress. M1-M4 done; M5 (Reports mode, polish, README) next.
**Date:** 2026-11-09
**Builds on:** `docs/ai-assistant.md` (the spec; "A5" etc. point to its sections). This plan only
describes what changes or gets decided on top of it. Everything not mentioned here follows the spec.

## Decisions in this plan

1. **Two providers behind one interface.**
   - **Anthropic API** (public, committed): the user's own API key, as in the spec.
   - **Local Claude Code** (private, gitignored, never committed): runs the installed, logged-in
     `claude` program so usage counts against the owner's own Claude subscription. Personal use only.
2. **Name hiding is an on/off option.** A global default in Settings (on by default) plus a choice
   for each new conversation. Once a conversation starts, its setting is fixed (see "Name hiding" below).
3. **Everything else stays as in the spec**: read-only tools, chat allowlist (empty by default),
   citations, the "what was sent" preview, AI off by default, no outbound requests while it's off.

## Architecture

```
                  ┌───────────────────────── backend ─────────────────────────┐
 AI page ─POST──▶ │ routes/ai.py ──▶ ai/loop.py ──▶ Provider                  │
 (fetch stream)   │                     │            ├─ AnthropicProvider ────┼──▶ api.anthropic.com
       ◀── SSE ── │                     │            └─ ClaudeCodeProvider* ──┼──▶ `claude -p` ──▶ Anthropic
                  │                     ▼                     │                │      (subscription)
                  │   tools.py (registry) ◀── MCP bridge* ◀───┘                │
                  │      │                                                     │
                  │   AiContext (allowlist) + Pseudonymizer (if names hidden)  │
                  └────────────────────────────────────────────────────────────┘
                                                     * private, gitignored file
```

- **Tools are written once** (`ai/tools.py`): name, JSON schema, handler `(AiContext, args) -> dict`.
  Each result goes through scope checks, then the size cap (A5.3), then the pseudonymizer
  (only when names are hidden).
- **The Anthropic provider** runs the tool loop itself and calls the tool handlers in process.
- **The Claude Code provider** lets `claude` run the loop. It reaches the same tool handlers
  through a small MCP server that lives only for the length of one answer.
- **Back-translation of names** (A6.4) happens in `loop.py` on the text events, so it works
  the same for both providers.

## Provider interface (`ai/providers/base.py`)

```python
class Provider(Protocol):
    id: str                      # "anthropic", "claude_code_local"
    label: str                   # shown in Settings
    def status(self) -> ProviderStatus: ...        # available? why not? (no key / claude not found / not logged in)
    async def run_turn(self, turn: TurnRequest) -> AsyncIterator[ProviderEvent]: ...
    async def cancel(self, conversation_id: str) -> None: ...
```

- `TurnRequest`: conversation history (as stored), new user message, system prompt, tool
  registry bound to this conversation's `AiContext` + pseudonymizer, model, effort, limits.
- `ProviderEvent`: `text_delta`, `tool_start`, `tool_end`, `usage`, `error`, `done`. These map
  1:1 to the SSE events in A8.5.
- **Discovery** works like route auto-include: `ai/providers/__init__.py` imports every module in
  `ai/providers/` that exposes `provider: Provider`. The private file is just one more module
  there, so the public code contains no reference to it.
- `GET /api/ai/status` lists the discovered providers and their status. Settings shows a provider
  picker with only those. Without the private file there is just "Anthropic API".

## Name hiding (on/off)

- **Settings:** `ai_hide_names: bool = True` (spec A8.6), shown as a switch "Hide names from the AI"
  with the honest one-line caveat (A6.5).
- **Per conversation:** the "New conversation" form shows the same switch, defaulting to the setting.
  The value is stored on the conversation and shown as a chip in the header ("Names: hidden 🔒" or
  "Names: visible" in a warning color).
- **Fixed after the first message.** To change it, start a new conversation (the chip says so).
  - Why: the Anthropic provider resends the whole history every turn, and conversations are stored
    as sent. Turning hiding *on* midway would keep re-sending earlier real names. Turning it *off*
    midway would mix placeholders and names in one context and confuse the model.
  - The Claude Code provider keeps its own session history, so the same rule applies there.
- **Off means off:** no outgoing replacement and no back-translation. The pseudonymizer is simply
  not in the pipeline. The "what was sent" preview then shows real names.
- When hiding is off **and** the provider is Claude Code, the setup screen also reminds you to
  check the "Help improve Claude" setting in your Claude account. Data then goes through a consumer
  account, where that setting decides whether chats can be used for training.

## Anthropic provider (public)

Follows spec A8.1–A8.3. Points that need care with the current models:

- `anthropic` Python SDK in a new optional extra: `pip install instagraph[ai]`. If it's missing,
  the provider status says how to install it.
- Model list in one constant: `claude-opus-5-5` (default), `claude-sonnet-5-5`, `claude-haiku-5-5`.
  Settings shows rough per-answer cost hints, because the owner pays per token here. Haiku costs
  fractions of a cent per question, Opus about 10–25 cents (estimates).
- **Manual tool loop with streaming**, so text and tool activity can go to the UI as separate events.
  - `thinking: {type: "adaptive"}`; `output_config.effort` from settings, always set explicitly
    (Opus 5.5 defaults to `medium`). Thinking can't be turned off on Opus 5.5.
  - Tools with `strict: true`. No forced `tool_choice` (Opus/Sonnet 5.5 reject `any`/`tool`).
  - Check `stop_reason` before reading content (`refusal`, `max_tokens`, `pause_turn`).
  - Server-side refusal fallback on (`fallbacks: "default"` with its beta flag) for Opus/Sonnet 5.5.
- **Prompt caching:** tools and system prompt first and never changing (no dates or ids in them).
  The date and scope summary go in the first user turn. Check `usage.cache_read_input_tokens`
  in tests with a recorded response.
- Key storage per spec A8.2 (`ANTHROPIC_API_KEY` or `data/ai/api_key`, mode `0600`, never returned).

## Claude Code provider (private, gitignored)

**File:** `backend/src/instagraph/ai/providers/claude_code_local.py`.
**`.gitignore`:** `backend/src/instagraph/ai/providers/*_local.py` (and `*_local/`).
Hatch respects `.gitignore` when building, so the file can't end up in a wheel either.

### How a turn runs
1. Start an MCP server on `127.0.0.1` on a random port, with a random bearer token. It serves only
   this conversation's tool registry: same scope and same pseudonymizer as the API provider.
   Uses the `mcp` Python package (installed by hand in the private setup, not added to `pyproject.toml`).
2. Write a temporary MCP config that points at it (URL + `Authorization` header).
3. Run `claude` as a subprocess:
   ```
   claude -p "<user message>"
     --output-format stream-json --verbose --include-partial-messages
     --tools ""                                  # no built-in tools: no file, shell or web access
     --mcp-config <tmp.json> --strict-mcp-config # only Instagraph's tools
     --safe-mode --setting-sources ""            # ignore personal CLAUDE.md, hooks, plugins, MCP servers
     --system-prompt-file <instagraph prompt>
     --model <opus|sonnet|haiku>
     [--resume <claude session id>]              # follow-up turns
   ```
   - Working directory: one fixed, empty folder per conversation,
     `data/ai/claude_code/<conversation id>/`. Never the repo, and never the dataset folder.
     It must stay the same across turns, because Claude Code looks sessions up by folder
     (`--resume` would not find the session from a new folder).
   - **Environment: remove `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN`.** Otherwise `claude`
     would use the API key (paid API billing) instead of the subscription.
   - Don't use `--bare`: it only accepts API keys.
   - Check every flag against the installed version (2.1.321 when this was written) and fail
     with a clear status if one is missing.
4. Parse the stream-json lines into `ProviderEvent`s (text deltas, tool use/result, usage, result).
   Store the `session_id` on the conversation for `--resume`.
5. Shut down the MCP server and delete the temp files when the turn ends or is cancelled
   (cancel = terminate the subprocess).

### Status checks
- `claude` on `PATH`; `claude --version` works.
- Logged in: run `claude auth status` (or the equivalent) when it exists; never read `~/.claude`
  files, and never touch credentials.
- Status messages: "Claude Code not found", "not logged in (run `claude` and log in yourself)",
  "flags not supported by this version".

### Rules this file must keep (from the legal notes we checked)
- Run the real, unmodified `claude` program only. No token reading, no impersonating Claude Code.
- Personal use only. Not committed, not shared, normal volume.
- Usage shares the subscription's limits with claude.ai and Claude Code.
- Claude Code saves its session history (with tool results, names hidden or not) under `~/.claude`.

### Tests
- Its own tests live next to it as `tests/ai/test_claude_code_local.py`, also gitignored.
  They use a fake `claude` script that prints recorded stream-json. The public test suite must
  pass without the private file.

## Backend work (public)

New package `backend/src/instagraph/ai/` as in spec A8.1, plus `providers/`:

| File | Work |
|---|---|
| `ai/context.py` | `AiContext`: wraps `AnalysisContext`, restricts `messages()`/`reactions()` to allowed threads (spec A5.5; done here because `api/deps.py` is frozen) |
| `ai/tools.py` | Tool registry (A5.2), size cap, "not allowed" errors, strict schemas |
| `ai/pseudonym.py` | Placeholder mapping, text matcher with SK/CS endings, stream buffer, back-translation (A6) |
| `ai/loop.py` | Provider-agnostic turn runner: limits (max tool calls, token budget), names pipeline on/off, persistence, event fan-out |
| `ai/providers/base.py`, `__init__.py` | Interface + auto-discovery |
| `ai/providers/anthropic_api.py` | Anthropic provider |
| `ai/providers/fake.py` | Scripted provider for tests (only registered under tests) |
| `ai/store.py` | Conversations in `data/ai/conversations/<id>.json` (A8.7) + `provider`, `hide_names`, `claude_session_id` fields |
| `ai/keys.py` | API key storage (A8.2) |
| `ai/prompts.py` | System prompts; the placeholder rules section is included only when names are hidden |
| `api/routes/ai.py`, `schemas/ai.py` | Endpoints from A8.5; `POST .../messages` streams SSE via `sse-starlette` |

**Settings** (`AppSettings`, a frozen file, so agree on this change first per `backend/CONTRACTS.md`):
the spec's A8.6 fields plus `ai_provider: str = "anthropic"`. All `ai_*` fields go in
`UI_ONLY_FIELDS` (no cache bust). Name-bearing ones (`ai_person_aliases`, `ai_default_scope`)
go in `REDACTED_FIELDS` in `api/routes/settings.py`.

## Frontend work

As in spec A4/A9, plus:
- **Streaming client:** `fetch` + `ReadableStream` SSE parser in `api/`. The import page's
  `EventSource` only supports GET; this endpoint is a POST.
- **Settings → AI:** provider picker (from `/api/ai/status`), API key field (Anthropic only),
  model + effort, "Hide names from the AI" switch, default scope, aliases.
- **New conversation form:** provider (prefilled), names switch (prefilled), scope.
- **Header chips:** scope, names (locked), provider. The "What was sent" button for the Anthropic
  provider shows the exact request. For Claude Code it shows the prompt plus the tool calls and
  results as they passed through the MCP bridge, since the full request is built inside `claude`.
- i18n EN + SK.

## Milestones

| # | Scope | Usable when done |
|---|---|---|
| M1 | Backend core: settings fields, `AiContext`, tools, pseudonymizer + on/off, `loop.py`, store, fake provider, tests | Tests only |
| M2 | AI page UI against the fake provider: chat, streaming, scope picker, names switch, citations, tool activity | UI clickable with scripted answers |
| M3 | **Claude Code provider (private)** + MCP bridge | Real answers on your subscription |
| M4 | Anthropic provider + key setup + test key + cost hints | Public version complete |
| M5 | Reports mode, polish, README / spec §10 update | Done |

M3 comes before M4 because you don't have an API account yet, so the private provider is the one
you'll actually use first. The public code is still finished and tested without it.

## Progress notes

- **M1 done:** settings, scope (scoped dataset copy with its own cache version), 11 tools,
  pseudonymizer, turn runner, conversation store, fake provider. The media tab is left out of
  the tools for now: its route can start a background audio-duration job in the data folder.
- **M2 done:** `/api/ai/*` endpoints with SSE; AI page (setup card, conversation list, new
  conversation with chat picker and names switch, streaming chat, tool activity, citations,
  allowed-chats dialog, "What was sent" view); Settings → AI section. `INSTAGRAPH_AI_FAKE=1`
  turns on a scripted fake provider for trying the UI without a real one.
- **M3 done (private, git-ignored):** `claude_code_local.py` + its tests. Deviations from the
  plan: the MCP server is a ~100-line standard-library implementation (no `mcp` package to
  install); `--safe-mode` isn't used because it may also switch off `--mcp-config` servers, so
  isolation is `--tools ""`, `--restricted`, `--strict-mcp-config`, `--disable-slash-commands`,
  `--permission-prompts none` with only Instagraph's tools allowed. The provider aborts at
  Claude Code's startup message if Instagraph's tools didn't connect. Live-tested once with the
  real `claude` (Claude Code 2.1.321, zero chats allowed, so no chat data was sent): flags
  accepted, tools connected, streaming, usage and session resume worked. The live run also
  found that generic words in display names ("Instagram User" -> "user") were hidden as names;
  fixed (name parts now come only from the name before `|`, `(`, ` - `, and skip generic words).
- **M4 done:** `ai/providers/anthropic_api.py` (anthropic SDK 2.7, optional `[ai]` extra, also in
  `dev`), `ai/keys.py`, `PUT/DELETE /api/ai/key`, `POST /api/ai/key/test` (free `models.list`
  call), key field in the setup card and Settings. Request: adaptive thinking, explicit effort,
  strict tools with `eager_input_streaming`, automatic prompt caching, `fallbacks: "default"` on
  Opus/Sonnet 5.5 (not Haiku). History is append-only; an unfinished turn (error, refusal, Stop,
  cut off) is rolled back. After a mid-output fallback only text before the last `fallback`
  block is echoed. Tested with a fake SDK client; not yet live-tested (needs an API key).
- **Deferred:** Settings editors for the default chats and per-person aliases (the fields and
  API exist); Slovak strings beyond the sidebar label (the app's pages are English-only today);
  Reports mode (M5).

## Verification

- **Public suite without the private file:** `pytest`, `ruff`, `mypy`, `npm test`, `npm run e2e`
  all pass, and `/api/ai/status` lists only "Anthropic API".
- **Private file never tracked:** `git check-ignore -v backend/src/instagraph/ai/providers/claude_code_local.py`
  prints the ignore rule. `git status` doesn't list it. Built wheel: `unzip -l dist/*.whl | grep _local` is empty.
- **Names on:** the last-request preview for the fixture dataset contains no display names, name parts,
  aliases or usernames (including SK/CS forms) (spec A12). **Names off:** it does contain them, and the
  answer has no placeholders.
- **Scope:** no tool returns data from a chat outside the allowlist, for either provider (the MCP bridge
  reuses the same registry; add one test that calls the bridge directly).
- **Claude Code isolation:** with the fake `claude`, assert the exact flags, an empty working directory,
  and that `ANTHROPIC_API_KEY` is absent from the subprocess environment.
- **Offline guarantee:** with `ai_enabled = false`, no provider client or subprocess is created.

## Spec updates to make alongside (`docs/ai-assistant.md`)

- A1 / A8.1: providers are pluggable; only the Anthropic provider ships publicly.
- A6.6: name hiding is chosen per conversation at creation and is then fixed; the global default is on.
- A8.6: add `ai_provider`.
- A13: add AD6 (two providers, private one gitignored and personal-use only) and AD7 (names fixed per
  conversation, and why).
- A14: drop the local-model question for now (no Ollama in this plan; the interface allows adding it later).
