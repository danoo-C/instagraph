<div align="center">

# Instagraph

**Turn your Instagram message history into detailed statistics and interactive charts. Everything runs on your own computer.**

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Polars](https://img.shields.io/badge/Polars-CD792C)
![React](https://img.shields.io/badge/React_18-20232A?logo=react&logoColor=61DAFB)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white)
![ECharts](https://img.shields.io/badge/ECharts-AA344D?logo=apacheecharts&logoColor=white)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

![Overview dashboard](images/overview.png)

</div>

Instagraph reads the **"Download Your Information"** export from Instagram (JSON format) and
analyses every direct message: who you talk to, when, how fast you reply, which words and
emojis you use, and how each chat changed over the years. It covers all chats together, each
chat on its own, and each person in it.

> **Private by design.** The backend listens on `127.0.0.1` only, makes no outbound network
> requests, and the frontend loads no third-party resources. There is no analytics.
> Your messages never leave your computer. (Only the installer goes online, to download the
> packages the app needs.)

*Screenshots show the built-in sample data (fake people and messages).*

---

## Features

- **Overview dashboard:** totals, sent vs. received, top contacts, activity over time,
  emojis, reactions, media, calls, streaks and busiest days across all chats.
- **Chat explorer:** a sortable, searchable table of every chat, with reply times, trends
  (rising / fading) and activity sparklines.
- **Chat detail** with 12 tabs: Summary, Timeline, When, Dynamics, Words, Emojis, Reactions,
  Media & Links, Calls, Group, Awards and Messages.
- **Conversation dynamics:** who starts conversations, who replies faster, who double-texts,
  who has the last word, session lengths and unanswered messages.
- **Text analysis:** top words and phrases per person, a term tracker for any word over
  time, emoji usage and laughter patterns. Stop-word lists for English, Slovak and Czech.
- **People and Compare:** stats for each person across all shared chats, and 2 to 4 chats
  or people side by side.
- **Wrapped:** a story-style yearly recap, for all chats or a single one. Every slide can
  be saved as a 1080×1920 PNG.
- **Message browser:** full-text search (plain, whole word, regex, accent-insensitive),
  "on this day", and a jump into the conversation around any message. Click any number in
  the app to see the messages behind it.
- **Export:** every chart as PNG, every table as CSV, plus a print / PDF report of a chat.
- **Correct data:** fixes Instagram's broken text encoding (mojibake), detects system
  messages in several languages, and handles renamed users, deleted accounts and
  multi-file threads.
- **Fast on large exports:** built for 1,000+ chats and 2,000,000+ messages
  (Polars + Parquet).
- **Anonymize mode:** replaces all names with "Person A/B/C…" for safe screenshots.
- **Optional AI assistant (off by default):** ask Claude about your chats in plain language,
  or run ready-made reports. It uses the app's own statistics, cites messages you can open,
  reads only the chats you allow, and can hide names behind placeholders. Needs your own
  Anthropic API key and the AI extra (`install.py --ai`, or `pip install 'instagraph[ai]'`).
- Light and dark theme, keyboard accessible, and every chart can be shown as a table.

## Screenshots

<table>
  <tr>
    <td width="50%"><img src="images/chats.png" alt="Chats list"><br><sub><b>Chats</b>: every conversation, sortable and searchable</sub></td>
    <td width="50%"><img src="images/chat-summary.png" alt="Chat summary"><br><sub><b>Chat summary</b>: headline numbers and per-person breakdown</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="images/chat-when.png" alt="When tab"><br><sub><b>When</b>: hour × weekday heatmap, night owls and early birds</sub></td>
    <td width="50%"><img src="images/chat-dynamics.png" alt="Dynamics tab"><br><sub><b>Dynamics</b>: sessions, who starts and who ends conversations</sub></td>
  </tr>
  <tr>
    <td colspan="2"><img src="images/wrapped.png" alt="Wrapped"><br><sub><b>Wrapped</b>: your year in messages, story style</sub></td>
  </tr>
</table>

---

## Getting started

### Requirements

The only thing you need is **Python 3.12 or newer**.

| System | How to get Python 3.12+ |
| --- | --- |
| Windows | The installer from [python.org](https://www.python.org/downloads/), or `winget install Python.Python.3.12` |
| macOS | The installer from [python.org](https://www.python.org/downloads/), or `brew install python@3.12`. The `python3` that comes with macOS is too old |
| Linux | Ubuntu 24.04+, Debian 13+ and Fedora already have it. Otherwise `sudo apt install python3.12 python3.12-venv`. On Debian and Ubuntu, also install `python3-venv` |

**Node.js is optional.** It's needed once, to build the app's interface. If Node.js 20.19+ is
already installed, the installer uses it. If not, it offers to download a private copy just
for Instagraph (about 30 MB). Nothing is installed system-wide, no admin rights are needed,
and nothing else on your computer changes.

**Supported systems:**

- Windows 10 and 11 (x64 and ARM64)
- macOS 12 or newer (Apple Silicon and Intel)
- Linux on x64 or ARM64 with glibc (most distributions)

Alpine and other musl-based Linux, and 32-bit systems, have no official Node.js downloads.
There, install Node.js 20.19+ yourself first (for example `apk add nodejs npm`).

### Install

1. Get the code: `git clone https://github.com/danoo-C/instagraph`, or on GitHub click
   **Code → Download ZIP** and unpack it.
2. Open a terminal in the `instagraph` folder and run:

   ```bash
   python3 install.py     # Windows: py install.py
   ```

   If `python3 --version` shows an older version, use the full name, e.g.
   `python3.12 install.py`.

The installer:

- checks your Python version and tells you what to do if it's too old;
- creates a virtual environment in `backend/.venv` and installs the Python packages into it;
- builds the app's interface, with your Node.js or a private copy (see above);
- asks whether you want the optional AI assistant;
- asks whether to put an **Instagraph** icon on your desktop (on Windows also in the Start
  menu, on Linux also in the app menu).

**To update**, get the new code (`git pull`, or unpack a new ZIP over the old folder) and run
the installer again. It only redoes what changed, and your data stays where it is. Close
Instagraph first; the installer reminds you if it's still running.

| Option | What it does |
| --- | --- |
| `--yes` | Accept all defaults and ask no questions |
| `--ai` | Also install the optional AI assistant (`--no-ai` to skip it) |
| `--no-shortcut` | Don't create a desktop icon |
| `--uninstall` | Remove Instagraph's icon and installed files (see [Uninstall](#uninstall)) |

`python3 install.py --help` lists all options.

The installer is the only part of Instagraph that goes online. It downloads Python packages
from PyPI, the interface's packages from the npm registry and, if needed, Node.js from
nodejs.org. The app itself makes no network requests (unless you turn on the optional AI
assistant).

> [!NOTE]
> **WSL users:** keep the clone in the Linux file system (`~/...`), not under `/mnt/c`. It is
> much faster. Use one clone per system: an install made from Windows doesn't work from WSL,
> and the other way round. If you want Instagraph on both, clone it twice.

### Run

Double-click the **Instagraph** icon. Or run this in the `instagraph` folder:

```bash
python3 run.py     # Windows: py run.py
```

Any Python 3.12+ works here: `run.py` starts the server from `backend/.venv` by itself.

A window opens with the server log, and the app opens in your browser at
**<http://127.0.0.1:8000>**. Closing the window (or pressing Ctrl+C in it) stops the app. If
Instagraph is already running, starting it again just opens the browser.

| Option | What it does |
| --- | --- |
| `run.py` | Serve on this computer only (`127.0.0.1:8000`) |
| `run.py --lan` | Also serve on your local network, e.g. to open it on your phone. Prints the URLs to use |
| `run.py --port 9000` | Use another port |
| `run.py --no-browser` | Don't open the browser |

> [!WARNING]
> With `--lan`, anyone on your network who can reach your computer can open the app and read
> your messages. Only use it on networks you trust.

> [!NOTE]
> **WSL users:** in the default NAT networking mode, other devices can't reach WSL. For
> `--lan`, set `networkingMode=mirrored` in `%UserProfile%\.wslconfig`, run `wsl --shutdown`,
> and allow the port in Windows Firewall.

### Uninstall

```bash
python3 install.py --uninstall     # Windows: py install.py --uninstall
```

This removes the desktop icon and `backend/.venv` (including the private Node.js, if one was
downloaded). It asks before deleting the built interface. It **never deletes your data**.
To remove that too, use **Settings → Clear all data** in the app first, or delete the `data/`
folder yourself. Deleting the whole `instagraph` folder removes everything.

### Get your Instagram export

1. Open Instagram, then **Settings → Accounts Center → Your information and permissions →
   Download your information**.
2. Choose **Messages** (or everything).
3. Set **Format: JSON** (not HTML), and **Date range: All time**.
4. When Instagram emails you, download the zip file(s).

In Instagraph, open **Import** and either drag in the zip file(s) or enter the path to the
zip or unpacked folder on disk. To try the app first, click **Load sample data** to generate
a fake dataset.

---

## Your data

- Imported data is stored in `data/` (set `INSTAGRAPH_DATA_DIR` to change it) and is kept
  between restarts.
- `data/` is git-ignored twice: in the root `.gitignore`, and by its own `.gitignore`
  containing `*`. Export zips and unpacked export folders are ignored too, so private
  messages can't be committed by accident.
- **Settings → Clear all data** deletes everything Instagraph stored.
- **Optional AI tab:** the only feature that sends data off your computer, and it's off by
  default. When you turn it on and send a message, the chats you allowed are sent to
  Anthropic's API using your own API key (names hidden by default). Your chats include other
  people's messages, so share only what you're comfortable with. Details:
  `docs/ai-assistant.md`.

## Configuration

Environment variables (or a `.env` file in `backend/`):

| Variable | Default | Description |
| --- | --- | --- |
| `INSTAGRAPH_DATA_DIR` | `<repo>/data` | Where imported data is stored |
| `INSTAGRAPH_HOST` | `127.0.0.1` | Bind address (a warning is logged for anything else) |
| `INSTAGRAPH_PORT` | `8000` | Port |
| `INSTAGRAPH_WORKERS` | CPU count − 1 | Number of processes used to parse the export |
| `INSTAGRAPH_ALLOWED_HOSTS` | (empty) | Extra hostnames accepted in the `Host` header (`--lan` sets this) |
| `INSTAGRAPH_CORS_ORIGINS` | `http://localhost:5173` | Allowed CORS origins (dev server only) |
| `ANTHROPIC_API_KEY` | (empty) | Optional: API key for the AI tab (otherwise set it in Settings → AI) |

Analysis options such as time zone, session gap, reply window, stop words, merging people
and anonymize mode are in the app's **Settings** page.

---

## Development

For development you also need **Node.js 20.19+** with npm, and `make` and `bash` (Linux,
macOS, or WSL on Windows).

```bash
git clone https://github.com/danoo-C/instagraph
cd instagraph
make setup      # creates backend/.venv, installs Python (with dev tools) and npm dependencies
./launch        # rebuilds the frontend if anything changed, then starts the server
```

`make setup` and `install.py` use the same `backend/.venv`, so you can mix them. Running
`install.py` later keeps the dev tools.

| Command | What it does |
| --- | --- |
| `./launch` | Serve on this computer only (`127.0.0.1:8000`) |
| `./launch --lan` | Also serve on your local network. Prints the URLs to use |
| `./launch --port 9000` | Use another port (`PORT=9000 ./launch` works too) |
| `./launch --rebuild` | Force a frontend rebuild |

```bash
make dev        # backend on :8000 + Vite dev server on :5173, both with hot reload
```

| Command | What it does |
| --- | --- |
| `make test` | Backend (pytest), installer (pytest) and frontend (Vitest) tests |
| `make e2e` | Browser end-to-end test with Playwright (needs Chromium) |
| `make lint` | Ruff + mypy `--strict` (backend and installer), ESLint + TypeScript |
| `make format` | Auto-format the backend and the installer |
| `make gen-types` | Regenerate the frontend API types from the FastAPI OpenAPI schema |

API docs are available at <http://127.0.0.1:8000/docs> while the server runs.

### Project structure

```
instagraph/
├── install.py          # cross-platform installer (install, update, uninstall)
├── run.py              # cross-platform launcher, started by the desktop icon
├── installer/          # code behind install.py and run.py (standard library only)
├── assets/             # app icon (SVG, PNG, ICO, ICNS)
├── launch              # build-if-needed + run script for developers
├── Makefile            # setup, dev, build, test, lint, gen-types
├── spec.md             # full specification
├── backend/            # Python 3.12, FastAPI, Polars, Parquet
│   └── src/instagraph/
│       ├── importer/   # export discovery, parsing, encoding fixes, system messages
│       ├── analysis/   # one module per metric family
│       ├── api/        # REST routes
│       ├── schemas/    # Pydantic request/response models
│       ├── store/      # Parquet dataset + caching
│       └── sample/     # fake data generator
└── frontend/           # React 18, TypeScript, Vite, ECharts, Tailwind
    └── src/
        ├── pages/      # Overview, Chats, ChatDetail, People, Compare, Wrapped, …
        ├── charts/     # ECharts wrappers with PNG/CSV export
        └── api/        # typed API client (generated from OpenAPI)
```

Further reading: [`spec.md`](spec.md) (full specification and metric definitions),
[`backend/CONTRACTS.md`](backend/CONTRACTS.md) and
[`frontend/CONTRACTS.md`](frontend/CONTRACTS.md) (interfaces for contributors).

## License

Released under the [MIT License](LICENSE).

## Disclaimer

Instagraph is an independent project. It is not affiliated with, endorsed by, or
connected to Instagram or Meta. It only reads the export file you download yourself, and
never logs in to or talks to Instagram.
