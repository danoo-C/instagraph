# Instagraph — Specification

**Instagraph** is an app that reads an Instagram "Download Your Information" export (JSON format) and turns your direct messages into detailed statistics, charts and analysis. It covers all your chats together, each chat on its own, and each person in a chat.

It is made of two parts that run **on your own computer**:
- **Backend:** Python + **FastAPI**, run with **uvicorn** inside a **venv**. It imports the export, stores the parsed data, and computes all stats.
- **Frontend:** **React + TypeScript** (Vite). It shows the dashboards and charts and gets all data from the backend API.

---

## 1. Goals and non-goals

### Goals
1. **Many stats.** Every metric that can reasonably be worked out from the export should be available: counts, rates, time patterns, conversation dynamics, text analysis, emojis, reactions, media, calls, group dynamics, and fun "awards".
2. **Detailed, interactive graphs.** Every chart supports hovering, zooming (for time series), filtering, and exporting as PNG and CSV.
3. **Private by design.** Everything runs locally. The backend listens on `127.0.0.1` only, makes no outbound network requests, and the frontend loads no third-party resources. No analytics.
4. **Correct data.** Handle Instagram's broken text encoding (mojibake), system messages, renamed users, deleted accounts, and multi-file threads, so the numbers can be trusted.
5. **Fast on big exports.** Stays usable with 1,000+ threads and 2,000,000+ messages.
6. **Clean codebase.** Strict typing on both sides, **absolute imports only** (no relative imports) in both Python and TypeScript, enforced by linters (§8.6).

### Non-goals (v1)
- Instagram's HTML export format (only JSON is supported; the importer detects HTML and tells the user to request JSON).
- Analysing anything other than messages (posts, likes, comments, followers). The architecture should allow this later (see §16).
- Sending, editing or syncing with Instagram in any way.
- Multi-user accounts, hosting on a public server, or cloud storage.

---

## 2. Users and main use cases

| # | As a user I want to… |
|---|---|
| U1 | drop my export (zip or folder) into the app, or point it to a folder on disk, and see my stats quickly |
| U2 | see a global dashboard: total messages, sent vs received, top contacts, activity over the years |
| U3 | open any chat and see who sent how many messages, words, emojis, reactions, media, calls |
| U4 | see when we talk (hour, weekday, calendar heatmap) and how that changed over time |
| U5 | see conversation dynamics: who texts first, who replies faster, who double-texts, who leaves people on read |
| U6 | see the words, phrases and emojis that are typical for each person |
| U7 | track any word or phrase over time (e.g. "love", "haha", a nickname) |
| U8 | compare two chats or two people side by side |
| U9 | get a "Wrapped"-style story for a year, for all chats or for one chat |
| U10 | export any chart as an image and any table as CSV |
| U11 | browse and search the raw messages behind any stat (click a bar → see those messages) |

---

## 3. Input: the Instagram export

### 3.1 How the user gets it
Instagram → Settings → Accounts Center → Your information and permissions → Download your information → choose **Messages** (or everything) → **Format: JSON** → Media quality: any → Date range: All time.

The app's import screen shows these steps, with a note that **JSON** must be picked, not HTML.

### 3.2 Folder structure (must support both layouts)

```
<export root>/
├── your_instagram_activity/          # newer exports (2024+)
│   └── messages/
│       ├── inbox/
│       │   └── <threadname>_<id>/
│       │       ├── message_1.json
│       │       ├── message_2.json   # big threads are split across files
│       │       ├── photos/  videos/  audio/  gifs/
│       ├── message_requests/
│       │   └── <threadname>_<id>/message_1.json
│       └── (other folders, ignored)
├── messages/                          # older exports: same contents at the root
│   ├── inbox/…
│   └── message_requests/…
└── personal_information/
    └── personal_information/personal_information.json   # used to find the owner's name
```

The importer **must not hard-code** these paths. It finds every file matching `**/messages/{inbox,message_requests,archived_threads}/*/message_*.json` (wherever the `messages` folder is). A thread's category (inbox / request / archived) comes from its parent folder.

The user can supply (§5.2):
- one or **several** `.zip` files (Instagram splits big exports into parts; merge them), uploaded through the browser, **or**
- a **local path** to an unzipped folder or to zip file(s) on disk, typed into the import screen. This is faster for big exports because nothing is copied: the backend reads the files directly.

### 3.3 Thread file schema (observed; every field except `participants` and `messages` is optional)

```jsonc
{
  "participants": [{ "name": "Jana Nováková" }, { "name": "Daniel" }],
  "messages": [                        // NEWEST FIRST within each file
    {
      "sender_name": "Jana Nováková",
      "timestamp_ms": 1699999999999,   // UTC epoch, milliseconds
      "content": "ahoj 😂",            // may be missing
      "reactions": [{ "reaction": "❤", "actor": "Daniel", "timestamp": 1700000000 }], // timestamp in seconds, may be missing
      "photos":      [{ "uri": "your_instagram_activity/messages/inbox/…/photos/123.jpg", "creation_timestamp": 1700000000 }],
      "videos":      [{ "uri": "…", "creation_timestamp": 0 }],
      "audio_files": [{ "uri": "…", "creation_timestamp": 0 }],
      "gifs":        [{ "uri": "…" }],
      "sticker":     { "uri": "…" },
      "share": {
        "link": "https://www.instagram.com/reel/…",
        "share_text": "caption…",
        "original_content_owner": "some_account"
      },
      "call_duration": 312,            // seconds; present on call messages
      "is_unsent": true,
      "is_geoblocked_for_viewer": false,
      "is_unsent_image_by_messenger_kid_parent": false
    }
  ],
  "title": "Jana Nováková",             // group name for group chats
  "is_still_participant": true,
  "thread_path": "inbox/jananovakova_123456789",
  "magic_words": [],
  "joinable_mode": { "mode": 1, "link": "" }
}
```

Raw files are validated with **Pydantic v2** models configured with `extra="allow"`. The parser must **ignore unknown fields** and must not crash when known fields are missing. Every unknown key it sees is counted and shown on the "Import report" screen (§5.4), so new export formats are easy to spot.

### 3.4 Known data problems the parser must handle

| Problem | Required handling |
|---|---|
| **Mojibake.** Instagram writes UTF-8 bytes as Latin-1 escaped code points (`"Ã¡"` instead of `"á"`, `"ð\u009f\u0098\u0082"` instead of `"😂"`). | Fix **every** string (names, content, titles, reactions, share text). In Python: `s.encode("latin-1").decode("utf-8")`. If that raises `UnicodeEncodeError` (the string is already correct) or `UnicodeDecodeError`, keep the original. Test with Slovak/Czech text (`čšťžýáíéúäôň`), emoji, ZWJ sequences, flags and skin tones. |
| **Thread split across files** (`message_1.json`, `message_2.json`, …) | Merge all files of a thread, then sort all messages ascending by `timestamp_ms` (stable sort; tie-break by file index and position). Remove exact duplicates (same sender + timestamp + content + attachment URIs). |
| **Same person, multiple threads** (e.g. an old thread and a new one, or the same thread in two zip parts) | Group threads by folder name prefix + the set of participants. Merge them by default; the user can split them again in Settings. |
| **Owner identification** | 1) Name from `personal_information.json` if present; 2) otherwise the participant who appears in the most threads; 3) the user can always override it in Settings. All "me vs them" stats depend on this. |
| **Renamed people** (display name changes over time) | Treat `sender_name` as the identity within a thread. In Settings the user can merge identities ("Jana" + "Jana Nováková" = same person). |
| **Deleted / deactivated accounts** (`"Instagram User"`, empty names) | Keep them as separate pseudo-participants per thread (`Instagram User (thread X)`); don't merge them across threads. |
| **System and action messages** stored as plain `content` | Classify with a locale-aware pattern table (§4.3). Exclude them from text stats (words, length, emoji counts), but keep them for event stats (likes, calls, renames, joins/leaves). |
| **Reactions stored as text** in older exports (`"Reacted 😂 to your message"`) | Parse into reaction events. Don't count them as text messages. |
| **Messages with both text and an attachment** | Count once as a message; count the text in text stats and the attachment in media stats. |
| **Empty messages** (no content, no attachment) | Count as `type = "unknown"`. Show in the import report. |
| **End-to-end encrypted chats** may be missing from the export | Show a note on the import screen: "Chats that used end-to-end encryption may not be part of this export." |
| **Timestamps** are UTC | Convert to the chosen display time zone (default: the system time zone, sent by the browser; changeable in Settings) using `zoneinfo` / Polars `convert_time_zone`, so the offset is correct for each timestamp (DST aware) when bucketing by hour/day. |
| **HTML export** supplied by mistake | Detect `message_1.html` and show a clear error with re-export instructions. |

---

## 4. Normalized data model (backend)

All raw files become the tables below. Every stat is computed from these, never from raw JSON.

### 4.1 Tables

Data is held in **Polars** DataFrames (columnar, fast, low memory) and saved as **Parquet** files in the data directory (§8.4).

**`people`**

| column | type | notes |
|---|---|---|
| `person_id` | UInt32 | |
| `display_name` | Utf8 | fixed encoding |
| `aliases` | List[Utf8] | merged names |
| `is_owner` | Boolean | |
| `is_deleted_account` | Boolean | |

**`threads`**

| column | type | notes |
|---|---|---|
| `thread_id` | UInt32 | |
| `title` | Utf8 | |
| `folder` | Utf8 | e.g. `jananovakova_123456789` |
| `category` | Enum(`inbox`, `request`, `archived`) | |
| `is_group` | Boolean | participants (incl. owner) > 2 |
| `participants` | List[UInt32] | person ids |
| `owner_still_participant` | Boolean | |
| `first_ts`, `last_ts` | Datetime(ms, UTC) | |
| `message_count` | UInt32 | |

**`messages`**

| column | type | notes |
|---|---|---|
| `message_id` | UInt32 | stable, ascending by time |
| `thread_id` | UInt32 | |
| `sender_id` | UInt32 | |
| `ts` | Datetime(ms, UTC) | |
| `type` | Enum | `text, photo, video, audio, gif, sticker, share, link, call, like, unsent, system, unknown` (priority rule below) |
| `text` | Utf8 (nullable) | fixed encoding; null for pure system messages |
| `n_photos`, `n_videos`, `n_audios`, `n_gifs`, `n_stickers` | UInt16 | |
| `share_link`, `share_text`, `share_owner` | Utf8 (nullable) | |
| `share_kind` | Enum (nullable) | `reel, post, story, profile, external, other` |
| `links` | List[Utf8] | URLs found in text |
| `call_duration_s` | UInt32 (nullable) | |
| `call_kind` | Enum (nullable) | `audio, video, unknown` |
| `call_missed` | Boolean | |
| `system_event` | Enum (nullable) | rename, join, leave, removed, photo change, theme change, nickname, … |
| `system_payload` | Utf8 (nullable) | e.g. the new group name |
| **derived at import** (computed once, §8.3): | | |
| `word_count` | UInt32 | |
| `char_count` | UInt32 | grapheme clusters, not code points |
| `emojis` | List[Utf8] | grapheme clusters |
| `session_id` | UInt32 | see §6.4 |
| `is_session_start` | Boolean | |
| `turn_id` | UInt32 | see §6.4 |
| `reply_latency_ms` | Int64 (nullable) | see §6.4 |

**`reactions`**

| column | type |
|---|---|
| `message_id` | UInt32 |
| `actor_id` | UInt32 |
| `emoji` | Utf8 (normalized, §6.6) |
| `ts` | Datetime (nullable) |

**`media`** (one row per attachment file): `media_id`, `message_id`, `kind`, `uri`, `source` (folder path or zip path + member name), `exists` (bool).

**Type priority** when a message has several parts: `call > unsent > system > like > share > video > photo > audio > gif > sticker > link > text`. Counts for each part are always recorded no matter which main type wins.

### 4.2 API schemas
Every API response is a **Pydantic v2** model. The OpenAPI schema produced by FastAPI is used to **generate the TypeScript types** for the frontend (`openapi-typescript`), so both sides always agree (§8.5).

### 4.3 System message classification
A pattern table, one JSON file per language in `backend/src/instagraph/importer/locales/`. **English, Slovak and Czech are required in v1** (decision D1); German, Spanish and others can be added as data files (no code change). Each pattern is a regex mapping to a `system_event` or message type.

**Locale auto-detection:** during import, every locale's patterns run over a sample of short messages. Each locale with ≥ 20 matches or ≥ 0.1% of messages is turned on, and several can be on at once (people change app language over the years). The detected locales are shown in the import report and can be overridden in Settings.

| Event | Example (EN) | Example (SK) |
|---|---|---|
| like | `Liked a message` | `Páči sa mu/jej správa` (to be checked against real exports) |
| reaction-as-text | `Reacted 😂 to your message` | `Reagoval(a) 😂 na vašu správu` |
| attachment placeholder | `X sent an attachment.` | `X poslal(a) prílohu.` |
| audio call start/end | `Audio call ended`, `X started an audio call` | … |
| video call start/end | `Video chat ended`, `X started a video chat` | … |
| missed call | `You missed an audio call`, `missed a video chat` | … |
| unsent | `X unsent a message` | … |
| group rename | `X named the group Y` | … |
| member added / left / removed | `X added Y to the group`, `X left the group` | … |
| group photo changed | `X changed the group photo` | … |
| theme / emoji / nickname changed | `X changed the theme…`, `X set the nickname for Y to Z` | … |
| story reply / mention | `Replied to your story`, `Mentioned you in their story` | … |

Exact Slovak and Czech strings **must be checked against real exports in those languages** before release (decision D1). Unmatched short messages that look like system text (start with a participant name + verb, no emoji) are listed in the import report so new patterns can be added.

---

## 5. Application structure (screens)

### 5.1 Global layout
- **Left sidebar:** Overview · Chats · People · Compare · Wrapped · Search · Settings.
- **Top bar (applies everywhere):**
  - Date range picker (presets: all time, each year, last 12 months, last 30 days, custom),
  - Chat filter: include/exclude group chats, message requests (**excluded by default**), archived threads (**included by default**) — decision D4. When requests are excluded, the Overview shows a small note like "+ 37 message requests not included",
  - "Exclude system messages" toggle (default on for text stats),
  - Time zone indicator.
- Filters live in the URL query string (shareable, back button works) and are sent to the backend as query parameters (§8.2).
- Every chart card has: title, short "what this means" tooltip (ⓘ), PNG export, CSV export, fullscreen.
- Every count in the app is **clickable** and opens the Message Browser (§5.9) filtered to the messages behind it.
- Light and dark theme. Responsive down to phone width (charts stack into one column).
- Loading skeletons for each card while its API request is running; a clear error card if a request fails.

### 5.2 Import screen
- **Option A — Upload:** drag and drop zone for one or more zip files, plus a "Choose files" button. Files are streamed to the backend in chunks (multi-GB uploads must work).
- **Option B — Local path:** a text field for an absolute path on disk (a folder, a zip, or a folder containing several zips). The backend checks that the path exists and is readable before starting.
- Step-by-step "How to get your export" guide with the JSON warning.
- Privacy statement: "Your data stays on this computer."
- Progress (live, via Server-Sent Events): files found → threads parsed → messages parsed → stats prepared, with a progress bar and a running counter. The import can be cancelled.
- "Load sample data" button that generates a fake dataset on the backend (for demos and testing).
- If data was imported before, the app opens straight to the Overview (data is kept between restarts, §8.4). A "Re-import" button replaces it.

### 5.3 Overview (all chats)
See §7.1 for the full list of stats and charts.

### 5.4 Import report
- Number of files, threads, messages, people found, export layouts detected.
- Owner name detected (and how), with a "Change" button.
- System-message languages detected (with match counts per locale).
- Encoding fixes applied (count), duplicates removed, threads merged.
- Unknown JSON fields (key + count), unmatched system-like messages (top 50), empty messages.
- Earliest and latest message.
- Import duration per stage.

### 5.5 Chats list
A sortable, searchable table of all threads (virtualized, sorted and paginated on the backend). Columns:
title · type (1:1/group/request) · participants · total messages · my share % · messages last 30 days · first message · last message · duration · avg messages/day · median reply time (me / them) · emoji count · calls · **sparkline of monthly activity** · trend arrow (rising/fading, §6.9).

### 5.6 Chat detail (the main screen)
Header: title, participants (avatars = colored initials), date span, total messages, my share, "Compare with…" button.

Tabs (each detailed in §7.2; each tab = one API endpoint, loaded only when opened):
1. **Summary** — headline numbers + per-person breakdown table
2. **Timeline** — activity over time
3. **When** — hour / weekday / calendar patterns
4. **Dynamics** — initiations, replies, response times, streaks, ghosting
5. **Words** — vocabulary, top words, phrases, term tracker
6. **Emojis** — emoji stats per person
7. **Reactions** — given/received, most reacted messages
8. **Media & Links** — photos, videos, voice notes, reels, links
9. **Calls**
10. **Group** (group chats only) — members, interaction network, events
11. **Awards** — fun badges for this chat
12. **Messages** — the Message Browser scoped to this chat

### 5.7 People
One page per person across **all** threads they appear in (1:1 + shared groups): total messages exchanged, threads in common, activity over time, their stats vs mine. The owner also has a "Me" page: my habits across all chats.

### 5.8 Compare
Pick 2–4 chats **or** 2–4 people. Shows the same metrics side by side (table + overlaid charts with normalized axes, e.g. messages per day, hour distribution as %, emoji rate).

### 5.9 Message Browser / Search
- Virtualized list of messages styled like a chat (bubbles, sender, time, reactions, attachment icons; media shown if the export contains the files). Loaded page by page with cursor pagination.
- Filters: thread, sender, date range, type, has reactions, contains emoji X, text search (plain, whole word, case-sensitive, accent-insensitive, regex).
- "Jump to context" opens the message in its thread around that point.
- Search results come with a **timeline of matches** chart and per-person counts.
- "On this day": messages from the same date in earlier years.
- "Random message" button.

### 5.10 Wrapped
A full-screen, story-style slideshow (tap/arrow to advance) for a chosen **year** and scope (all chats or one chat): total messages, top 5 people, top emoji, busiest day, night owl %, longest streak, top word, fastest replier, most reacted message, longest call, "your personality" summary built from awards. Each slide can be saved as a PNG (9:16 for stories).

### 5.11 Settings
Stored on the backend (`settings.json` in the data directory) so they survive restarts.
- Owner identity override.
- Merge / split people (aliases) and merge / split threads.
- Time zone.
- Session gap threshold (default 60 min, §6.4) and "reply window" threshold (default 12 h) — decision D3.
- System-message languages: auto-detected (decision D1), with manual on/off per locale.
- Media: "Delete uploaded zips" (keeps all stats, removes media viewing for zip imports) — decision D2.
- Stop-word lists per language (built-in EN + SK + CZ, editable), custom ignored words.
- Laughter patterns, swear-word list (editable, off by default), custom terms to track.
- Emoji options: merge skin tones (default on), merge gendered variants (default off).
- Anonymize mode: replace all names with "Person A/B/C…" and hide message text — for screenshots. Applied on the backend, so no real names reach the frontend while it is on.
- Theme, language of the UI (EN required; SK nice to have).
- Clear all data (deletes the data directory contents).

Changing a setting that affects derived fields (owner, merges, time zone, session gap, reply window) re-runs only the affected derivation steps, with a progress indicator.

---

## 6. Metric definitions

Precise definitions so every number is reproducible. "Message" means a non-system message unless stated otherwise. All metrics respect the active filters. Each metric family lives in its own backend module (§8.3).

### 6.1 Basic counts
- **Messages** = count of messages with `type ∉ {system, like}`. Likes and system events are counted separately.
- **Words** = tokens matched by the Unicode-aware regex `[\p{L}\p{M}\p{N}]+(?:['’][\p{L}\p{M}]+)*` (Python `regex` module). URLs are removed first and counted separately.
- **Characters** = grapheme clusters (`regex.findall(r"\X", text)`), excluding leading/trailing whitespace.
- **Share %** = person's messages / all messages in scope.
- **Avg / median / p90 message length** in words and characters (text messages only).

### 6.2 Time bucketing
- All buckets use the display time zone.
- **Active day** = a calendar day with ≥1 message.
- **Messages per day** (two versions): over the full span (first → last message) and over active days only.
- **Rolling averages**: 7-day and 30-day.

### 6.3 Streaks and gaps
- **Daily streak** = consecutive calendar days with ≥1 message from anyone. Report longest streak (dates), current streak.
- **Mutual streak** = consecutive days where *every* participant (1:1) sent ≥1 message.
- **Longest silence** = largest gap between two consecutive messages (with dates and what was said before/after).

### 6.4 Sessions, initiations, replies
- **Session (conversation)**: messages split wherever the gap between consecutive messages is > `session_gap` (default 60 min, adjustable).
  - Count, average and median length (messages and minutes), longest session.
- **Initiator** = sender of the first message in a session. **Initiation rate** per person = sessions started / total sessions.
- **Last word** = sender of the last message in a session.
- **Turn** = maximal run of consecutive messages from the same sender.
  - **Avg messages per turn** per person (double-texting tendency). **Longest turn** (with link).
  - **Double / triple text count** = turns of length 2 / ≥3 where the gap between the messages is > 1 min (to exclude normal multi-line typing).
- **Reply** = the first message of a new turn by person B directly after a turn by A, if the gap is ≤ `reply_window` (default 12 h).
  - **Reply latency** = time between A's **last** message in the turn and B's first message.
  - Report per person: median, mean, p25/p75/p90, fastest, and distribution.
  - **Reply rate** = turns by A that got a reply within `reply_window` / all turns by A.
- **Left on read / unanswered** = turns by A with no reply from B within `reply_window`. Report count per person and the longest wait that eventually got a reply.

### 6.5 Text analysis
- **Tokenization**: lowercase, remove URLs, mentions and emojis, keep diacritics for display; for matching, also offer accent-insensitive mode (`ahoj` = `áhoj`, via `unicodedata.normalize("NFKD")` + stripping combining marks) — important for Slovak where people often type without diacritics.
- **Stop-words** removed for top-word lists (EN + SK + CZ built-in, as data files).
- **Top words / bigrams / trigrams** per person and overall.
- **Distinctive words** per person = weighted log-odds ratio with an informative Dirichlet prior (Monroe et al.) comparing that person against everyone else in the chat. Show the top 25 with scores. Fallback: TF-IDF across people.
- **Vocabulary size** = unique words. **Lexical diversity** = MATTR (moving-average type-token ratio, window 500) so it's fair across people with different message counts.
- **Question rate** = messages containing `?` / messages. **Exclamation rate**, **ALL CAPS rate** (≥3 letters, all uppercase), **ellipsis rate** (`...`/`…`).
- **Laughter**: matches of configurable patterns (`haha+`, `hehe+`, `xd+`, `lol`, `lmao`, `😂`, `🤣`, `💀`, …). Report count per person, laughter per 100 messages, and **"who makes whom laugh"**: laughter messages from B within 2 minutes after a message from A, credited to A.
- **Links, @mentions, #hashtags** extracted and counted.
- **Most repeated messages** (exact match, e.g. "ok", "dobre", "haha").
- **Longest messages** (top 10 per person, clickable).
- **Sentiment** (optional, v1.1): emoji-based sentiment for all languages (Emoji Sentiment Ranking lexicon) + word lexicon for English (AFINN). Show sentiment over time per person. Clearly marked as rough.

### 6.6 Emojis
- Extract emojis with the **`emoji`** Python package (`emoji.emoji_list`), which treats ZWJ sequences, flags and keycaps as **one** emoji.
- Optionally normalize skin tones (strip U+1F3FB–U+1F3FF) and the variation selector U+FE0F (so `❤` = `❤️`).
- Also count text emoticons (`:)`, `:D`, `<3`, `:P`, `xD`) separately.
- Metrics per person: total emojis, emojis per message, % of messages with ≥1 emoji, unique emojis, top 20 emojis with counts, **signature emoji** (highest log-odds vs others), emoji-only messages, longest emoji chain, emoji usage over time, first time each top emoji was used.
- Emoji categories (smileys, hearts, animals, food, …) from the Unicode emoji data shipped with the backend.

### 6.7 Reactions
- Per person: reactions **given**, reactions **received**, reactions per 100 messages received, top reaction emojis given/received.
- **Reaction matrix** (who reacts to whom, groups).
- **Most reacted messages** (top 20, with text and reactors).
- **Reaction reciprocity** = min(given A→B, given B→A) / max(…).
- **Self-reactions** count.
- Likes (`Liked a message`) are counted together with ❤ reactions in a "hearts" total, but also shown separately.

### 6.8 Media, shares and calls
- Counts per person: photos, videos, voice notes, GIFs, stickers, shared posts, shared reels, shared stories, shared profiles, external links.
- **Top shared accounts** (`share_owner`), **top link domains**.
- **Reels exchange balance** = reels sent by me vs them, over time.
- **Media share of messages** over time (stacked: text / photo / video / audio / share / other).
- **Voice notes**: count; if the audio files exist in the export, read their durations in a background job (`mutagen`) → total and average voice note length.
- **Calls**: count (audio / video), total duration, average, median, longest call (date), missed calls per person, who calls whom more, calls by hour and weekday, call minutes per month.
- **Unsent messages** per person.

### 6.9 Trend and relationship metrics
- **Trend** of a chat = slope of a linear fit on monthly message counts over the last 6 months, normalized by the mean → "rising", "stable", "fading". Also show "peak month".
- **Balance index** (1:1) = 1 − |my_share − 0.5| × 2 (1 = perfectly balanced, 0 = one-sided). Computed over the whole period and per month (chart).
- **Effort index** per person = combined z-score of share of messages, initiation rate, reply speed and average length. Shown with its parts, never as a single number alone.
- **Friendship lifecycle**: first message, last message, total span, peak month, number of active months, longest break and comeback.

---

## 7. Stats and charts catalogue

Chart type suggestions in *italics*. "Per person" means one series/color per participant (consistent colors across the whole app for each person). The backend returns chart-ready data (already bucketed and aggregated); the frontend only renders it.

### 7.1 Overview (all chats)

**Headline tiles**
- Total messages · sent by me · received · my share %
- Total chats (1:1 / group / requests) · people
- Total words · characters · emojis · reactions given/received
- Photos · videos · voice notes · reels/posts shared · links
- Calls · total call time
- First message ever (date + with whom) · days since · active days · avg messages per active day
- Busiest day ever (date, count, top chat that day) · longest daily streak

**Charts**
1. Messages over time, stacked by top 10 contacts + "others" — *stacked area, day/week/month toggle, zoomable*
2. Sent vs received over time — *mirrored bar chart*
3. Top contacts leaderboard — *horizontal bar, toggle metric: messages / words / emojis / calls / reactions*
4. Top contacts over time — *bump chart (rank per month) and stream graph*
5. Calendar heatmap of all messages — *GitHub-style, one row per year*
6. Hour × weekday heatmap — *punch card*
7. Hour of day distribution — *radial 24-hour clock, me vs others*
8. Day of week distribution — *bar*
9. Cumulative messages — *line*
10. New conversations started per month (first message with a new person) — *bar*
11. Number of active chats per month — *line*
12. Message type mix over time — *100% stacked area*
13. Top emojis overall and top emojis I use — *emoji bar / grid*
14. Distribution of chats by size (messages per chat, log scale) — *histogram*
15. My median reply time over time — *line*
16. Social graph: people connected by shared group chats — *force-directed network, node size = messages*
17. Year-over-year comparison table — messages, people, top contact, top emoji per year

### 7.2 Chat detail

**Summary tab**
- Tiles: total messages, words, emojis, reactions, media, calls, first/last message, span, active days, avg per day, longest streak, longest silence.
- **Per-person table** (sortable): messages, share %, words, avg words/msg, characters, emojis, emojis/msg, reactions given/received, photos, videos, voice notes, reels, links, calls started, unsent, median reply time, initiations %.
- Messages per person — *donut*; words per person — *donut*.
- First message ever in this chat (quoted) and the most recent one.

**Timeline tab**
1. Messages over time per person — *stacked bars, day/week/month/year toggle, brushing to zoom*
2. Share over time (who carries the chat) — *100% stacked area by month*
3. Balance index per month — *line around 0.5 line*
4. Cumulative messages per person — *line (shows who's "ahead")*
5. Rolling 7/30-day average — *line*
6. Words per message over time per person — *line*
7. Activity calendar heatmap — *calendar*
8. Annotated timeline of events: first message, peaks, longest silences, group renames, member changes, first call, first "custom term" occurrence — *timeline with markers*

**When tab**
1. Hour × weekday heatmap (per person selector, or difference me−them) — *heatmap*
2. Hour of day per person — *radial or grouped bar*
3. Day of week per person — *grouped bar*
4. Month of year (seasonality) — *bar*
5. Weekday vs weekend share — *bar*
6. Night owl % (00:00–05:00) and early bird % (05:00–08:00) per person — *tiles*
7. How the hourly pattern changed over the years — *small multiples, one per year*
8. Busiest days top 10 (clickable) — *table*
9. Busiest hour ever (single hour with most messages)

**Dynamics tab**
1. Sessions: count, avg/median length, longest session (date, duration, messages)
2. Session length distribution — *histogram (log)*
3. Who starts conversations — *donut + over time line*
4. Who has the last word — *donut*
5. Reply latency per person — *box plot + histogram with log time axis (1 s … 12 h)*
6. Median reply time over time per person — *line by month*
7. Reply time by hour of day — *line* (are replies faster in the evening?)
8. Reply rate and left-on-read count per person — *tiles*
9. Longest wait that got a reply (with messages before/after)
10. Messages per turn per person, double/triple texts — *bar*
11. Longest monologue (turn) — *quote card*
12. Conversation starters: most common first messages of sessions — *list*
13. Effort index breakdown — *radar chart, one shape per person*

**Words tab**
1. Vocabulary size and lexical diversity (MATTR) per person — *tiles*
2. Top 50 words per person — *bar + word cloud toggle*
3. Distinctive words per person — *diverging bar (log-odds)*
4. Top bigrams / trigrams — *table*
5. Message length distribution per person (words) — *overlaid histograms*
6. Question / exclamation / caps / ellipsis rates — *grouped bar*
7. Laughter stats + "who makes whom laugh" — *tiles + bar*
8. Most repeated messages — *table*
9. Longest messages — *list with links*
10. **Term tracker**: enter one or more words/phrases/regex → usage over time per person (*line*), total per person, first and last use, all occurrences in the browser. Saved terms are kept per chat (in settings).
11. Swear word count per person (only if enabled)
12. Sentiment over time (v1.1) — *line with band*

**Emojis tab**
1. Totals per person: emojis, emojis/msg, % messages with emoji, unique emojis — *tiles*
2. Top emojis per person — *side-by-side emoji bar charts*
3. Signature emoji per person — *large card*
4. Emoji usage over time per person — *line*
5. Top emojis over time — *stream graph of top 10*
6. Emoji-only messages and longest emoji chain
7. Emoticons (`:)`, `<3`, `xD`) per person
8. Emoji categories (smileys, hearts, animals, food, …) per person — *stacked bar*
9. Heart emojis specifically (❤️🧡💛💚💙💜🖤🤍🤎💕💖…) per person over time — *line*

**Reactions tab**
1. Reactions given / received per person — *grouped bar*
2. Top reaction emojis per person — *bar*
3. Reaction matrix (groups) — *heatmap*
4. Most reacted messages — *list*
5. Reactions over time — *line*
6. Reaction reciprocity, self-reactions, likes — *tiles*

**Media & Links tab**
1. Media counts per person per type — *grouped bar*
2. Message type mix over time — *100% stacked area*
3. Reels/posts sent by each person over time — *line* ("reel exchange")
4. Top shared accounts — *bar*
5. Top link domains — *bar*
6. Voice notes: count, total/average duration (if available) per person
7. Photo/video gallery (if media files are in the export) — *virtualized grid, filter by sender and year; thumbnails generated by the backend with Pillow and cached*

**Calls tab**
1. Calls count (audio/video), total time, average, median, longest — *tiles*
2. Calls per month and minutes per month — *combo bar + line*
3. Who starts calls — *donut*
4. Missed calls per person — *bar*
5. Call duration distribution — *histogram*
6. Calls by hour × weekday — *heatmap*
7. Longest calls top 10 — *table*

**Group tab** (group chats only)
1. Member leaderboard (messages, words, reactions received, media) — *bar*
2. Lurkers: members with < 1% of messages — *list*
3. **Who replies to whom** (A's turn followed by B's within reply window) — *chord diagram + matrix*
4. Who reacts to whom — *heatmap*
5. Members' activity over time — *stream graph*
6. Membership timeline: joins, leaves, removals — *Gantt-style bars per member*
7. Group name history and photo/theme changes — *timeline*
8. Most mentioned members (@name) — *bar*

**Awards tab** — badges given to participants of this chat (and globally on Overview), each with the number that earned it:

| Award | Rule |
|---|---|
| 📚 The Novelist | highest avg words per message |
| ⚡ Speed Demon | lowest median reply time |
| 🦉 Night Owl | highest share of messages 00:00–05:00 |
| 🐓 Early Bird | highest share 05:00–08:00 |
| 🎤 Conversation Starter | highest initiation rate |
| 🔁 Double Texter | most messages per turn |
| 👻 The Ghost | most unanswered turns |
| 😂 Comedian | causes the most laughter |
| 🤡 Laugher | laughs the most |
| 🎞️ Reel Dealer | most reels/posts shared |
| 🎙️ Podcaster | most voice notes |
| 💖 Heart Giver | most heart reactions given |
| 🌟 Crowd Favorite | most reactions received per message |
| 🧠 Wordsmith | highest lexical diversity |
| 😎 Emoji Overlord | most emojis per message |
| 📞 Caller | most calls started |
| ❓ The Curious | highest question rate |
| 🗑️ Second Thoughts | most unsent messages |

---

## 8. Architecture

### 8.1 Tech stack

**Backend**

| Concern | Choice | Why |
|---|---|---|
| Language | **Python 3.12+** | |
| Environment | **venv** (`python -m venv .venv`) | isolated dependencies |
| Packaging | `pyproject.toml`, **src layout**, installed editable (`pip install -e ".[dev]"`) | makes `instagraph` an importable package, so **absolute imports** work everywhere (app, scripts, tests) |
| Web framework | **FastAPI** | typed endpoints, automatic OpenAPI |
| Server | **uvicorn** (`uvicorn[standard]`) | |
| Validation / schemas | **Pydantic v2**, `pydantic-settings` for config | |
| JSON parsing | **orjson** | fast parsing of large message files |
| Data processing | **Polars** | columnar, multi-threaded, handles millions of rows; time zone-aware datetime ops |
| Storage | **Parquet** files (via Polars) + `settings.json` | no database server needed |
| Text | `regex` (Unicode classes, graphemes), `emoji` | |
| Media | `Pillow` (thumbnails), `mutagen` (audio durations) | |
| Uploads / streaming | `python-multipart`, `sse-starlette` (Server-Sent Events) | |
| Tests | `pytest`, `httpx` (FastAPI `TestClient`), `hypothesis` | |
| Lint / types | **ruff** (lint + format), **mypy --strict** | |

**Frontend**

| Concern | Choice | Why |
|---|---|---|
| Build | **Vite + TypeScript** (strict) | fast dev, static output |
| UI | **React 18** + **Tailwind CSS** + Radix UI primitives | accessible components |
| Data fetching | **TanStack Query** | caching, loading/error states, cancellation |
| API types | **openapi-typescript** (generated from FastAPI's OpenAPI) + `openapi-fetch` | one source of truth for types |
| Charts | **Apache ECharts** (`echarts-for-react`) + `echarts-wordcloud` | handles large data (canvas, `lttb` sampling) and every chart type needed: heatmap, calendar, chord/graph, radar, box plot, stream (themeRiver), dataZoom |
| Tables / lists | TanStack Table + TanStack Virtual | huge lists |
| Routing | React Router (filters kept in URL query) | |
| UI state | Zustand | small |
| Tests | Vitest + React Testing Library, Playwright (end-to-end) | |
| Lint | ESLint + Prettier | |

### 8.2 API design

Base path `/api`. JSON responses (via `ORJSONResponse`). All analysis endpoints accept the same **filter query parameters**:

`start`, `end` (ISO dates) · `tz` (IANA name) · `include_groups` · `include_requests` · `include_archived` · `exclude_system` · `people` (comma-separated ids)

| Method & path | Purpose |
|---|---|
| `POST /api/import/upload` | multipart upload of one or more zips (streamed to disk) → `{job_id}` |
| `POST /api/import/path` | `{path}` of a local folder/zip → `{job_id}` |
| `POST /api/import/sample` | generate sample data → `{job_id}` |
| `GET /api/import/jobs/{job_id}/events` | SSE progress stream (stage, done, total, message) |
| `DELETE /api/import/jobs/{job_id}` | cancel |
| `GET /api/import/report` | import report (§5.4) |
| `GET /api/status` | is data loaded, dataset version, counts |
| `DELETE /api/data` | clear all data |
| `GET /api/overview` | headline tiles |
| `GET /api/overview/{section}` | `timeline`, `contacts`, `heatmap`, `calendar`, `hours`, `weekdays`, `types`, `emojis`, `reply-times`, `graph`, `years`, … |
| `GET /api/chats` | chat list (sort, search, pagination) |
| `GET /api/chats/{id}` | chat header |
| `GET /api/chats/{id}/{tab}` | `summary`, `timeline`, `when`, `dynamics`, `words`, `emojis`, `reactions`, `media`, `calls`, `group`, `awards` |
| `GET /api/chats/{id}/terms?q=…` | term tracker |
| `GET /api/people`, `GET /api/people/{id}` | people list / person page |
| `GET /api/compare?chats=…` or `?people=…` | comparison data |
| `GET /api/wrapped?year=…&chat=…` | Wrapped slides data |
| `GET /api/messages` | Message Browser search, cursor pagination (`cursor`, `limit`), text/regex/type/sender filters |
| `GET /api/messages/{id}/context` | messages around one message |
| `GET /api/messages/random`, `GET /api/messages/on-this-day` | |
| `GET /api/media/{media_id}` | media file (from folder or read straight from the zip) |
| `GET /api/media/{media_id}/thumb` | cached thumbnail |
| `GET /api/settings`, `PUT /api/settings` | settings |
| `GET /api/export/{scope}.json` | full stats summary export |

Rules:
- Every endpoint has typed Pydantic request/response models.
- Errors use one shape: `{ "error": { "code": "...", "message": "..." } }` with correct HTTP status codes (e.g. `409` if no data is imported yet).
- Responses for analysis endpoints include `dataset_version` so the frontend can invalidate its cache after a re-import or settings change.

### 8.3 Processing pipeline (backend)

```
zip upload / local path
        │
        ▼
 [Discovery]   find message_*.json (both layouts, inside zips or folders), personal_information.json, media files
        │
        ▼
 [Parse]       ProcessPoolExecutor: orjson load → fix encoding → classify system messages → normalized rows per thread
        │
        ▼
 [Merge]       owner detection, people & thread identity resolution, sort, dedupe
        │
        ▼
 [Derive]      Polars: word/char counts, emojis, sessions, turns, reply latencies (vectorized, per thread)
        │
        ▼
 [Persist]     Parquet files + import report in the data directory; dataset_version += 1
        │
        ▼
 [Stats]       on request: filter → aggregate with Polars lazy queries → Pydantic response
               results cached (LRU) by (dataset_version, endpoint, filters, settings hash)
```

- Import runs as a background job (in a process pool so the API stays responsive); progress is published to the job's SSE stream. Only one import at a time.
- Derived fields are computed **once** at import; filters only re-aggregate.
- On startup, the backend loads existing Parquet files from the data directory (lazily via `pl.scan_parquet`), so data survives restarts.
- CPU-heavy endpoints run in a thread pool (`run_in_threadpool`) so the event loop is never blocked.

### 8.4 Configuration and data directory
Config via environment variables (prefix `INSTAGRAPH_`, loaded with `pydantic-settings`, optional `.env` file):

| Variable | Default | |
|---|---|---|
| `INSTAGRAPH_DATA_DIR` | `<repo>/data` | Parquet files, kept uploads, thumbnails, settings. Gitignored at the root and by its own `.gitignore` with `*` (decision D5) |
| `INSTAGRAPH_HOST` | `127.0.0.1` | the app warns loudly at startup if set to anything other than localhost |
| `INSTAGRAPH_PORT` | `8000` | |
| `INSTAGRAPH_WORKERS` | CPU count − 1 | parse process pool size |
| `INSTAGRAPH_CORS_ORIGINS` | `http://localhost:5173` | only used in development |

### 8.5 Project layout

```
instagraph/
├── spec.md
├── README.md
├── Makefile                     # setup, dev, build, test, lint, gen-types
├── data/                        # runtime data (gitignored)
├── backend/
│   ├── pyproject.toml           # deps, ruff, mypy, pytest config
│   ├── .venv/                   # gitignored
│   ├── src/
│   │   └── instagraph/
│   │       ├── __init__.py
│   │       ├── main.py          # create_app(): FastAPI app, routers, static frontend, middleware
│   │       ├── config.py        # Settings (pydantic-settings)
│   │       ├── api/
│   │       │   ├── deps.py      # shared dependencies (filters, dataset access)
│   │       │   ├── filters.py   # common filter query model
│   │       │   └── routes/      # import_.py, overview.py, chats.py, people.py, compare.py,
│   │       │                    # wrapped.py, messages.py, media.py, settings.py, export.py
│   │       ├── schemas/         # Pydantic response/request models, one module per area
│   │       ├── importer/
│   │       │   ├── discovery.py
│   │       │   ├── encoding.py
│   │       │   ├── raw_models.py
│   │       │   ├── parser.py
│   │       │   ├── system_messages.py
│   │       │   ├── identity.py  # owner detection, people/thread merging
│   │       │   ├── jobs.py      # background job + progress events
│   │       │   └── locales/     # en.json, sk.json, cs.json
│   │       ├── store/
│   │       │   ├── dataset.py   # load/save Parquet, dataset_version
│   │       │   └── cache.py
│   │       ├── analysis/        # one module per metric family:
│   │       │                    # counts.py, time.py, streaks.py, sessions.py, replies.py, text.py,
│   │       │                    # emoji.py, reactions.py, media.py, calls.py, group.py, trends.py,
│   │       │                    # awards.py, wrapped.py, search.py
│   │       ├── media/           # file access (folder or zip), thumbnails, audio durations
│   │       ├── resources/       # stop-words, emoji categories, emoji sentiment, laughter patterns
│   │       └── sample/          # sample data generator
│   └── tests/
│       ├── fixtures/            # small fake exports (EN + SK + CZ + mixed-language, mojibake, multi-file, group, deleted user, both layouts)
│       ├── importer/
│       ├── analysis/
│       └── api/
└── frontend/
    ├── package.json
    ├── vite.config.ts           # "@" alias → src, /api proxy → 127.0.0.1:8000
    ├── tsconfig.json            # baseUrl + paths: { "@/*": ["src/*"] }
    └── src/
        ├── main.tsx
        ├── app/                 # router, layout, providers
        ├── api/                 # generated schema.d.ts + typed client + query hooks
        ├── pages/               # Import, Overview, Chats, ChatDetail/<tab>, People, Compare, Wrapped, Search, Settings
        ├── charts/              # thin ECharts wrappers with shared theme, PNG/CSV export
        ├── components/          # tiles, tables, filters, message bubble, …
        ├── state/               # Zustand stores
        ├── i18n/                # en, sk
        └── lib/                 # formatting, colors, utils
```

### 8.6 Absolute imports (required)

**Python**
- Every import uses the full package path: `from instagraph.analysis.sessions import compute_sessions`. Relative imports (`from .sessions import …`, `from ..schemas import …`) are **not allowed**.
- Works because the package is installed into the venv in editable mode (`pip install -e .`). Tests import the same way. No `sys.path` hacks.
- Enforced by ruff:
  ```toml
  [tool.ruff.lint]
  select = ["E", "F", "I", "UP", "B", "TID", "SIM", "RUF"]

  [tool.ruff.lint.flake8-tidy-imports]
  ban-relative-imports = "all"

  [tool.ruff.lint.isort]
  known-first-party = ["instagraph"]
  ```

**TypeScript**
- Every internal import uses the `@/` alias from `src`: `import { ChartCard } from "@/charts/ChartCard"`. Relative imports (`./x`, `../x`) are **not allowed**.
- Configured in `tsconfig.json` (`"baseUrl": "."`, `"paths": { "@/*": ["src/*"] }`) and `vite.config.ts` (`resolve.alias`).
- Enforced by ESLint:
  ```js
  "no-restricted-imports": ["error", { patterns: [{ group: ["./*", "../*"], message: "Use absolute '@/…' imports." }] }]
  ```

### 8.7 Running the app

**First-time setup**
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

cd ../frontend
npm install
```

**Development** (two terminals, hot reload on both sides)
```bash
# terminal 1 — backend
cd backend && source .venv/bin/activate
uvicorn instagraph.main:app --reload --host 127.0.0.1 --port 8000

# terminal 2 — frontend (proxies /api to :8000)
cd frontend && npm run dev          # http://localhost:5173
```

**Normal use** (one process): build the frontend once, then FastAPI serves it as static files at `/` next to the API.
```bash
cd frontend && npm run build        # outputs frontend/dist
cd ../backend && source .venv/bin/activate
uvicorn instagraph.main:app --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000
```

**Makefile targets**: `make setup`, `make dev`, `make build`, `make run`, `make test`, `make lint`, `make gen-types` (starts from FastAPI's `app.openapi()` and regenerates `frontend/src/api/schema.d.ts`; CI fails if the generated file is out of date).

API docs are available at `http://127.0.0.1:8000/docs` (FastAPI Swagger UI).

---

## 9. Performance requirements

| Scenario | Target |
|---|---|
| Import 100k messages on a mid-range laptop | < 10 s to Overview |
| Import 2M messages | < 2 min, backend memory < 2 GB, progress updates ≥ 2/s |
| Backend restart with existing data | < 3 s until the API is ready |
| Chat tab endpoint | < 300 ms for chats with < 100k messages; < 1.5 s for the largest chat (uncached) |
| Overview endpoints after a global filter change | < 1 s |
| Cached response | < 50 ms |
| Frontend charts | time series with > 5k points use ECharts `lttb` sampling; the backend never sends more than ~20k points per series (it buckets coarser instead); no chart renders > 50k DOM nodes |
| Message Browser | pages of 100 messages, < 200 ms per page |

---

## 10. Privacy and security
- The backend binds to `127.0.0.1` by default and prints a clear warning if bound to another address.
- No outbound network requests from the backend, except the optional AI tab (off by default; see `docs/ai-assistant.md`): when the user turns it on and sends a message, the chats they allowed are sent to Anthropic's API from the backend with their own API key. The frontend is served with a strict Content-Security-Policy (`default-src 'self'; connect-src 'self'; img-src 'self' blob: data:`), all fonts and assets bundled, no CDNs, no analytics.
- CORS is enabled only for the dev frontend origin.
- **Local path import**: the path is resolved (`Path.resolve()`), must exist, and only `.json` and media files under it are read. Symlinks pointing outside it are ignored.
- **Zip safety**: reject absolute paths and `..` in member names (zip slip), cap total uncompressed size and file count (zip bomb protection), read members by streaming.
- **Media endpoint**: serves only files registered in the `media` table by id — never a path from the request — so path traversal is impossible.
- Message text is always rendered as text in React (no `dangerouslySetInnerHTML`) to prevent injection from message content.
- Data lives only in the data directory. "Clear all data" deletes it. Uploaded zips are **kept by default** so media can be shown straight from them (decision D2); "Delete uploaded zips" in Settings removes them and keeps the stats.
- The data directory gets its own `.gitignore` containing `*` when it's created, besides the root `.gitignore` entry, so private messages can't be committed by accident (decision D5).
- Anonymize mode (§5.11) for safe screenshots.

---

## 11. Exports
- **PNG** of any chart (2× resolution, themed background, includes title and date range) — done in the frontend by ECharts.
- **CSV** of the data behind any chart or table — done in the frontend from the API response.
- **JSON** of the full stats summary for a chat or for everything (`/api/export/…`; no message text unless the user ticks "include messages").
- **Wrapped slides** as PNG (1080×1920).
- **Print / PDF report** of a chat (print stylesheet that lays out the Summary, Timeline, When, Dynamics, Words, Emojis tabs).

---

## 12. Accessibility and i18n
- WCAG 2.1 AA: keyboard navigation, focus states, color contrast, chart colors that work for color-blind users (with patterns/labels, not color alone), ARIA labels on charts with a "view as table" toggle for every chart.
- UI translations: English (required), Slovak (nice to have).
- Numbers and dates formatted with the browser locale (`Intl.NumberFormat`, `Intl.DateTimeFormat`).

---

## 13. Testing and quality
**Backend (pytest)**
- Unit tests for: encoding fix (Slovak, emoji, ZWJ, flags, already-correct strings stay unchanged), file discovery for both layouts (folders and zips, multi-part zips), multi-file merge and dedupe, owner detection, system message classification (EN + SK + CZ) and locale auto-detection incl. mixed-language exports, zip slip / zip bomb protection, every metric in §6 with hand-calculated expected values on small fixtures.
- **Property tests** (`hypothesis`) for sessions/turns/replies (e.g. sum of per-person messages = total; every session has exactly one initiator; reply latency ≥ 0 and ≤ reply window).
- **API tests** with FastAPI `TestClient`: every endpoint returns a valid response model for the fixture dataset, filters change results as expected, `409` before import.
- `ruff check`, `ruff format --check`, `mypy --strict` must pass.

**Frontend**
- Vitest + React Testing Library for components and formatting helpers.
- ESLint (incl. the absolute-imports rule) and `tsc --noEmit` must pass.

**End-to-end (Playwright)**
- Start uvicorn with a temporary data dir → import fixture zip through the UI → Overview shows expected totals → open a chat → every tab renders without errors → CSV export matches expected values.
- The test fails on any network request to a host other than `127.0.0.1`.

**Performance**
- Sample generator (`python -m instagraph.sample --messages 2000000 --out …`) produces realistic fake exports (multiple languages, groups, calls, reactions, media placeholders).
- Nightly benchmark asserts the import time and memory budget from §9.

---

## 14. Milestones

| Phase | Scope |
|---|---|
| **M0 — Skeleton** | repo layout, venv + editable install, FastAPI app factory, uvicorn run, Vite + React + TS app, `/api` proxy, `@/` alias, ruff/mypy/ESLint with absolute-import rules, OpenAPI → TS type generation, Makefile, CI |
| **M1 — Import & core** | upload + local path import, discovery, encoding fix, parser, system messages (EN + SK + CZ, auto-detected), identity resolution, Parquet store, SSE progress, import report |
| **M2 — Overview & chat summary** | Overview tiles + charts 1–9, Chats list, Chat Summary + Timeline + When tabs, global filters, PNG/CSV export |
| **M3 — Dynamics, words, emojis** | sessions, turns, replies, Dynamics tab, Words tab (incl. term tracker), Emojis tab, Reactions tab |
| **M4 — Media, calls, groups** | Media & Links (incl. media serving + thumbnails), Calls, Group tab, social graph, Message Browser & search |
| **M5 — Fun & polish** | Awards, Wrapped, Compare, People pages, anonymize mode, print report, accessibility pass, Slovak UI |
| **v1.1** | sentiment, voice-note durations, more system-message locales |

---

## 15. Acceptance criteria (v1)
1. `make setup` then `uvicorn instagraph.main:app` (inside the venv) starts the app on `http://127.0.0.1:8000` with the built frontend.
2. A real Instagram JSON export (zip, single or multi-part, or an unzipped folder path) imports without errors; text with Slovak diacritics and emojis displays correctly everywhere.
3. Total messages on Overview equals the sum over all chats, and the sum over people in each chat (verified by tests).
4. Every stat listed in §7 is present, respects the global filters, and can be clicked through to its messages.
5. Every chart can be exported as PNG and CSV.
6. No relative imports exist in the Python or TypeScript code (ruff and ESLint enforce this in CI).
7. No network request leaves the machine (verified in the E2E test).
8. Performance targets in §9 are met on the sample datasets.

---

## 16. Future ideas (out of scope for v1)
- Analyse other parts of the export: likes, comments, story interactions, followers/following changes, login activity, ads interests.
- Facebook Messenger and WhatsApp export importers (same normalized model).
- Local ML: topic clustering of conversations, better multilingual sentiment, language detection per message.
- "Relationship timeline" storytelling with automatically chosen highlight messages.
- Package as a single executable (PyInstaller) or desktop app.

---

## 17. Decisions

These started as open questions. Each has a recommendation, and that recommendation is the decision the rest of the spec follows. Any of them can be changed later. The settings-based ones (D3, D4) can be changed in the app without code changes.

### D1 — Which language(s) must system-message detection support?
**Question:** System messages ("Liked a message", call notices, group renames) are stored in the language the Instagram account was set to *at the time*. Which languages must the app recognise?

**Recommendation:** Ship pattern files for **English, Slovak and Czech**, and **detect the language automatically**, without asking the user. During import, run every locale's patterns over a sample of short messages and turn on each locale with a meaningful number of matches (≥ 20 matches or ≥ 0.1% of messages). More than one can be on, because people switch app language over the years. Before release, check the Slovak and Czech strings against real exports. Unmatched system-like messages stay visible in the import report so missing patterns are easy to add.
- *Why:* The user is Slovak, so a mixed EN/SK export (and CZ from friends' group chats or older settings) is likely. Auto-detection means nothing to configure. Getting this wrong quietly skews word, emoji and message counts, so the import report must show it.

**Answer:** EN + SK + CZ pattern files, auto-detected per export (several at once allowed), with a manual override in Settings. → see §4.3, §5.4, §5.11.

### D2 — Show media files, or only count them?
**Question:** Should photos, videos and voice notes from the export be viewable in the app, or only counted?

**Recommendation:** **Show them**, loaded lazily. The backend generates cached thumbnails for photos and video poster frames, and plays voice notes inline. Exports downloaded without media still work: media is counted from the JSON and missing files show a placeholder. For **zip uploads**, keep the uploaded zips in the data directory by default (media is read straight from the zip, no full extraction), with a "Delete uploaded zips" button that keeps the stats and drops the media. For **local path imports**, nothing is copied and files are read from the original folder.
- *Why:* Seeing the photo behind "most reacted message" or a gallery per year adds a lot. Reading from the zip avoids doubling disk usage.

**Answer:** Media is shown (lazy, thumbnails cached). Uploaded zips are kept by default and can be deleted in Settings. → see §5.11, §7.2 Media tab, §10.

### D3 — Default session gap and reply window
**Question:** How long a silence starts a new conversation (session gap), and how long after a message does an answer still count as a "reply" (reply window)?

**Recommendation:** **Session gap = 60 minutes**, **reply window = 12 hours**, both adjustable in Settings. Also show the reply-time histogram up to the full window, so the user can see if a different cut-off would fit them better.
- *Why:* In direct messages, a pause of more than an hour usually means a new conversation, while 30 minutes splits real conversations too often (people step away briefly). 12 hours covers "replied the next morning" without counting a reply days later as a reply. Reply times are skewed, so the app reports medians (not averages) first, which keeps the window choice from distorting the main number.

**Answer:** 60 min session gap, 12 h reply window, both configurable. → see §6.4, §5.11.

### D4 — Include message requests and archived chats in totals?
**Question:** Should message requests (chats you never accepted) and archived chats count in the totals by default?

**Recommendation:** **Message requests: excluded by default.** They're mostly spam or one-off messages and would inflate "people" and "chats" counts. **Archived chats: included by default.** They're real conversations the user chose to hide from the inbox, often with a long history. Both have toggles in the top bar. The Overview shows a small note like "+ 37 message requests not included".

**Answer:** Requests off, archived on, both toggleable. → see §5.1.

### D5 — Where is data stored?
**Question:** Where should parsed data, settings, kept zips and thumbnails live?

**Recommendation:** **`<repo>/data`**, listed in `.gitignore` from the very first commit (M0), and configurable with `INSTAGRAPH_DATA_DIR`. As an extra guard against committing private messages, the backend writes a `.gitignore` containing `*` inside the data directory when it creates it, so the data stays ignored even if the folder is moved or the root `.gitignore` changes.
- *Why:* Keeping it next to the project makes it easy to find, inspect and delete. The double `.gitignore` protects against the main risk of keeping it there.

**Answer:** `<repo>/data` (double-gitignored), configurable with `INSTAGRAPH_DATA_DIR`. → see §8.4.
