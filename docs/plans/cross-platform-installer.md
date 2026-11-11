# Plan: Cross-platform installer (`install.py`) and desktop icon

**Status:** Implemented on `feat/installer` (steps 1–8). Tested end to end on Linux/WSL; Windows and
macOS are covered by unit tests, two code reviews and the CI workflow, but still need one manual run
(see "Manual checks" at the end).
**Date:** 2026-11-10
**Scope:** New files `install.py`, `run.py`, `assets/icon.*`; README "Getting started"; a CI
workflow. No changes to the backend or frontend code.

## Goal

One command that installs Instagraph on Windows, macOS or Linux:

```bash
python install.py          # Windows: py install.py
```

It checks the requirements, creates a virtual environment, installs the Python packages, sets
up the frontend, and optionally puts an **Instagraph** icon on the desktop. Double-clicking the
icon starts the app and opens it in the browser. Running `install.py` again updates an
existing install.

`make setup` and `./launch` stay as they are for development.

## Decisions

1. **Standard library only.** `install.py` must run before any package is installed, so it
   uses only `venv`, `subprocess`, `urllib`, `zipfile`, `tarfile`, `hashlib`, `shutil`, `platform`.
   It works on any Python 3.12+ and needs no `make` or `bash`.
2. **Editable install (`pip install -e backend`).** `config.py` finds the repo as
   `Path(__file__).parents[3]`, and uses it for `data/` and `frontend/dist/`. With a normal
   install, `__file__` would be inside `site-packages`, so the app would look for its data in
   the wrong place. An editable install keeps the code in the repo. As a safety net, `run.py`
   also sets `INSTAGRAPH_DATA_DIR` and `INSTAGRAPH_FRONTEND_DIST` to absolute paths.
3. **Frontend: always built locally. If Node is missing, the installer downloads a private
   copy.** `frontend/dist/` is git-ignored, so a fresh clone has no UI and has to be built.
   - Node 20.19+ found on `PATH`: use it.
   - Otherwise download the official Node.js build from nodejs.org into
     `backend/.venv/node/`. It is used only by the installer. Nothing is installed system-wide,
     no admin rights are needed, and the system `PATH` is not changed. Details are in
     "Private Node.js" below.
   - The only requirement for users is **Python 3.12+**. There are no pre-built frontend zips,
     so nothing extra has to be published with each release.
   - We don't use system package managers (winget, brew, apt). They need admin or sudo, behave
     differently on each OS, and often have an old Node (Ubuntu 24.04's apt has Node 18, which is
     too old).
4. **A new cross-platform launcher, `run.py`.** `./launch` is bash, so it can't run on
   Windows. `run.py` is what the desktop icon starts. It doesn't rebuild the frontend, since end
   users may not have Node; `./launch` keeps that job for developers.
5. **The icon opens a console window.** It shows the server log and the URL, and closing it stops
   the server. That is simpler and easier to understand than a hidden background process with
   no obvious way to stop it.
6. **Icon design:** a white chat bubble holding the four bars from the favicon, on the same
   indigo (`#6366f1` → `#4338ca`). Drawn as SVG and rendered to PNG/ICO/ICNS (step 1).

## Files

| File | What it is |
|---|---|
| `install.py` | The installer (repo root, next to `launch`) |
| `run.py` | Cross-platform launcher used by the desktop icon |
| `assets/icon.svg` | Icon source (1024×1024 view box) |
| `assets/icon.png` | 512 px PNG, used on Linux |
| `assets/icon.ico` | Windows: 16, 24, 32 (favicon artwork) + 48, 64, 128, 256 |
| `assets/icon.icns` | macOS: 16 to 1024 px |

## `install.py` flow

0. **Make sure Instagraph isn't running** (see "Existing installs", case A). This also runs
   before `--uninstall`.
1. **Check Python.** Stop if older than 3.12 and say how to get a newer one: python.org
   installer, `winget install Python.Python.3.12`, `brew install python@3.12`, or
   `sudo apt install python3.12 python3.12-venv`. macOS's built-in Python is 3.9, so this
   message will come up there.
2. **Create `backend/.venv`** if it doesn't exist (`venv.create(..., with_pip=True)`).
   - Python inside it: `backend/.venv/Scripts/python.exe` on Windows,
     `backend/.venv/bin/python` elsewhere. One helper function picks the right one.
   - If venv creation fails on Debian/Ubuntu because `ensurepip` is missing, show
     `sudo apt install python3-venv` (or `python3.X-venv`) instead of the raw traceback.
   - If the venv was made with an older Python than the one running now, offer to recreate it.
   - If the venv was made on another OS (Windows vs. Linux/WSL), offer to recreate it
     (case B).
3. **Install Python packages:** `python -m pip install --upgrade pip`, then
   `python -m pip install -e "backend"`, or `"backend[ai]"` if the user said yes to the AI
   assistant (asked once; `--ai` / `--no-ai` skip the question). Always call pip as
   `python -m pip` from the venv, never through a `pip` command found on `PATH`.
4. **Build the frontend.** Skip this step if `dist/index.html` is newer than every source file
   (same check as `./launch`), unless `--rebuild`.
   - Find Node: on `PATH` (version 20.19+), else in `backend/.venv/node/`, else download it
     (see "Private Node.js").
   - Run `npm ci` and `npm run build` in `frontend/`. Put the chosen Node's folder first on
     `PATH` for these commands only, so npm's scripts run with the same Node.
   - Also don't skip the step if `node_modules` was installed on another OS (case B). The
     build would fail with rolldown/lightningcss errors.
   - Windows needs `npm.cmd`. With system Node, use the `npm.cmd` next to `node.exe`, then
     `shutil.which("npm.cmd")` (Python 3.12.0's `which("npm")` can return the extensionless
     shell script). With private Node, call `node/npm.cmd` (Windows) or `node/bin/npm` directly.
5. **Desktop icon:** ask "Create a desktop icon? [Y/n]" (`--shortcut` / `--no-shortcut`).
   See "Desktop icon on each OS". If a shortcut already exists, see case C.
6. **Finish** by printing how to start it: the icon, or `python run.py`. If the user agrees
   (`--run` skips the question), start the app.

**Flags:** `--yes` (accept all defaults, no questions, for CI), `--ai`, `--no-ai`, `--shortcut`,
`--no-shortcut`, `--rebuild`, `--private-node` (use the downloaded Node even when a system Node
exists; handy for testing), `--uninstall`.

## Private Node.js

Used when no Node 20.19+ is found on `PATH`.

1. **Ask first:** "Node.js is needed to build the app's interface. Download it (about 30 MB)
   just for Instagraph? It won't change anything else on your computer. [Y/n]". With `--yes`
   it downloads without asking. If the user says no, stop and print where to get Node
   (nodejs.org).
2. **Pick the version:** a pinned major version in `installer/node.py` (`NODE_MAJOR = 24`, the
   current LTS). Read `https://nodejs.org/dist/index.json` and take the newest release of that
   major. Bump `NODE_MAJOR` when the frontend needs a newer Node. Node 24 needs macOS 13.5+,
   so older macOS gets `NODE_MAJOR_OLD_MACOS = 22`.
3. **Pick the archive** from `platform.system()` and `platform.machine()`:

   | OS | CPU | Archive |
   |---|---|---|
   | Windows | x64 / ARM64 | `node-vX-win-x64.zip` / `node-vX-win-arm64.zip` |
   | macOS | Apple Silicon / Intel | `node-vX-darwin-arm64.tar.gz` / `node-vX-darwin-x64.tar.gz` |
   | Linux | x64 / ARM64 | `node-vX-linux-x64.tar.xz` / `node-vX-linux-arm64.tar.xz` |

   Python's `zipfile` and `tarfile` (with `lzma` for `.xz`) can unpack all of them.
   - On Windows ARM64 running x64 Python, `platform.machine()` says `AMD64`. Check
     `PROCESSOR_ARCHITEW6432` / `PROCESSOR_ARCHITECTURE` too.
   - On macOS, an x64 Python running under Rosetta reports `x86_64`. Check
     `sysctl -n hw.optional.arm64` to pick the arm64 build anyway.
4. **Download and verify:** download into a temporary folder, show progress, and check the
   SHA-256 against `https://nodejs.org/dist/vX/SHASUMS256.txt` before unpacking. On a
   mismatch, delete the file and stop. Use HTTPS only. The `urllib` download honours
   `HTTPS_PROXY` for corporate networks.
5. **Unpack safely:** unpack into a temporary folder next to the target. Refuse entries with
   absolute paths or `..` (`tarfile`'s `filter="data"`; check each name for zip). Then rename
   the folder to `backend/.venv/node/`, so a cancelled install never leaves half a Node behind.
   Write `backend/.venv/node/VERSION` so a later run knows what is installed.
6. **Reuse:** re-running `install.py` (for example after `git pull`) uses the existing private
   Node and doesn't download again. Updates then also work offline. It downloads only if
   `NODE_MAJOR` changed or the folder is broken (no `node` executable that runs `--version`).
7. **Not supported:** Alpine and other musl-based Linux, and 32-bit systems. nodejs.org has
   no official builds for them. There the installer asks the user to install Node 20.19+
   themselves (for example `apk add nodejs npm`) and run it again.

Putting Node inside `backend/.venv/` means that deleting or recreating the venv (or
`--uninstall`) removes it too, and it is already git-ignored. Size: about 30 MB download,
about 180 MB unpacked, plus `frontend/node_modules`.

Alternative considered: the `nodeenv` pip package (`nodeenv -p` installs Node into the active
venv). It works, but it adds a dependency for about 80 lines of code we can write ourselves
and control (version choice, checksum, messages).

**Output:** short `==>` step lines, same style as `./launch`. No colors when stdout is not a
terminal, or on old Windows consoles that don't support ANSI codes (check `os.isatty`).
On any error: one clear sentence on what failed and what to do. Show the full traceback only
with `--verbose`.

## `run.py`

- Reads `--port`, `--lan`, `--no-browser` (same meaning as `./launch`).
- Sets `INSTAGRAPH_HOST`, `INSTAGRAPH_DATA_DIR`, `INSTAGRAPH_FRONTEND_DIST`, and for `--lan`
  `INSTAGRAPH_ALLOWED_HOSTS`, exactly as `./launch` does. LAN address discovery uses
  `socket.getaddrinfo(socket.gethostname(), None)` plus a UDP-connect trick, instead of
  `hostname -I` (Linux-only).
- If `backend/.venv` doesn't exist, says "Run install.py first" and waits for Enter before
  exiting, so the console window doesn't close before you can read it.
- If `frontend/dist/index.html` is missing, gives the same kind of message.
- **Already running:** before starting, it requests `http://127.0.0.1:<port>/openapi.json`.
  If the reply has `info.title == "Instagraph"`, it just opens the browser and exits. If
  something else is using the port, it says so and suggests `--port`.
- Starts the server as `<venv python> -m uvicorn instagraph.main:app --host … --port …`, with
  `cwd=backend` so `backend/.env` is still read. It does this with `subprocess` rather than
  `uvicorn.run()` in-process, so `run.py` itself can run on any Python, even the system Python.
- Opens the browser after the server answers on `/openapi.json` (polled for up to 20 s),
  not after a fixed sleep. Under WSL, `webbrowser` usually can't reach the Windows browser,
  so use `wslview` if present, otherwise `cmd.exe /c start "" <url>`.
- Ctrl+C or closing the window stops uvicorn cleanly.
- **Run file:** after the server starts, writes `backend/.venv/instagraph-run.json` with
  `{"pid": <uvicorn pid>, "port": <port>, "started": <ISO time>}`, and deletes it on exit.
  `install.py` uses it to tell whether this copy is running (case A). If the window is killed,
  the file can be left behind, so readers always check that the process is alive.

## Desktop icon on each OS

All three use absolute paths to `<venv python>`, `run.py` and the icon, so they work no matter
which folder they're started from. `--uninstall` removes exactly what was created. The paths
are saved in `backend/.instagraph-install.json` (outside the venv, so it survives recreating the
venv), so uninstall doesn't have to guess.
If a shortcut with the same name already exists, case C decides whether it's updated,
replaced or kept.

### Windows

- A `.lnk` file made through PowerShell's `WScript.Shell` COM object, so pywin32 isn't needed:
  ```powershell
  $s = (New-Object -ComObject WScript.Shell).CreateShortcut('<desktop>\Instagraph.lnk')
  $s.TargetPath = '<repo>\backend\.venv\Scripts\python.exe'
  $s.Arguments = '"<repo>\run.py"'
  $s.WorkingDirectory = '<repo>'
  $s.IconLocation = '<repo>\assets\icon.ico,0'
  $s.Save()
  ```
  Single quotes in paths are doubled before inserting them. The script is passed with
  `-NoProfile -ExecutionPolicy Bypass -Command`.
- Find the desktop with `[Environment]::GetFolderPath('Desktop')`, not `~\Desktop`. OneDrive
  often moves the Desktop folder.
- Also add the same shortcut to the Start menu (`%APPDATA%\Microsoft\Windows\Start Menu\Programs`).
- Use `python.exe`, not `pythonw.exe`, so the console window appears (decision 5).

### macOS

- An `Instagraph.command` file on `~/Desktop`: a small shell script that `cd`s to the repo and
  runs `exec "<venv python>" "<repo>/run.py"`, made executable with `chmod +x`. Double-clicking
  opens Terminal, which matches decision 5.
- Custom icon: set with `osascript -l JavaScript` and
  `NSWorkspace.sharedWorkspace.setIconForFileOptions(NSImage(icon.icns), path, 0)`.
  No extra tools needed. If it fails, the shortcut still works, just with the default icon.
- Later option: a real `Instagraph.app` bundle (`Contents/MacOS/` script, `Info.plist`,
  `Resources/icon.icns`) for a Dock icon. Not in the first version, because a script-only `.app`
  doesn't handle Quit well.

### Linux

- `instagraph.desktop`:
  ```ini
  [Desktop Entry]
  Type=Application
  Name=Instagraph
  Comment=Statistics and charts for your Instagram messages
  Exec="<repo>/backend/.venv/bin/python" "<repo>/run.py"
  Path=<repo>
  Icon=<repo>/assets/icon.png
  Terminal=true
  Categories=Utility;
  ```
- Written to `~/.local/share/applications/` (app menu) and copied to the desktop folder from
  `xdg-user-dir DESKTOP`, falling back to `~/Desktop`. Skip the copy if there's no desktop folder.
- `chmod +x`, then `gio set <file> metadata::trusted true` if `gio` exists. Without that,
  GNOME shows "Untrusted application launcher".
- Paths containing spaces are quoted, and `%` in paths is escaped as `%%`, as the
  Desktop Entry spec requires.

### WSL (stretch goal)

- Detect WSL from `/proc/version`. Then ask whether to create a **Windows** desktop shortcut
  instead of a Linux one.
- The shortcut targets `wsl.exe` with arguments
  `-d <distro> --cd <repo> -- backend/.venv/bin/python run.py`. Read the distro name from
  `$WSL_DISTRO_NAME`.
- The `.ico` has to be on a Windows path: copy it to `%LOCALAPPDATA%\Instagraph\icon.ico`
  first. Run the PowerShell script with `powershell.exe` through WSL interop.

## Existing installs

Running `install.py` again is the normal way to update, so every step works on an existing
install as well as a fresh one:

| Step | Fresh install | Already installed |
|---|---|---|
| Venv | Created | Reused. Recreated only with consent, if made with an older Python or on another OS (case B) |
| Python packages | Installed | `pip install -e` runs again; only new or changed packages are installed. Extras already there, such as `[dev]` from `make setup`, stay |
| AI extra | Asked | Not asked again if `anthropic` is already installed in the venv; kept |
| Node | Found or downloaded | System Node, or the private Node already downloaded. No new download |
| Frontend | Built | Skipped if the build is up to date and `node_modules` matches this OS. Otherwise rebuilt |
| Desktop icon | Created | Updated in place, or case C if it belongs to another copy |
| `data/` | Created on first run | **Never touched** |

The summary at the end says what happened in each step, for example "Python packages: up to
date", "Frontend: rebuilt (src/app/Layout.tsx changed)", or "Desktop icon: updated (it pointed
to /old/path)".

### Case A: Instagraph is running during an update

Why it matters: on Windows, pip can't replace files that a running process has open (`.pyd`
files of polars, pyarrow, orjson). The update would fail halfway and leave the venv broken.
On Linux and macOS it would work, but the running server would keep the old code until it's
restarted.

Check (step 0):

1. If `backend/.venv/instagraph-run.json` exists, check that its `pid` is alive. On POSIX use
   `os.kill(pid, 0)`. On Windows use `OpenProcess` through `ctypes`, or
   `tasklist /FI "PID eq <pid>"`. Also check that its port answers `/openapi.json` with
   `info.title == "Instagraph"`. If both pass, this copy is running. If not, delete the stale
   file.
2. If there is no run file, probe port 8000 and `INSTAGRAPH_PORT` (from the environment or
   `backend/.env`). This catches servers started with `./launch` or `make dev`, which don't
   write a run file.

If Instagraph is running:

- Interactive: "Instagraph is running (port 8000). Close its window, then press Enter to
  continue (Ctrl+C to cancel)." Check again after Enter.
- `--yes`: stop with that message and a non-zero exit code.
- The installer **never kills the process itself**. The user might be in the middle of an
  import.
- With only a port probe (step 2), we can't tell whether the server belongs to this copy or
  another one, so the message says "An Instagraph server is running on port 8000".

Safety net: if pip still fails with a Windows "file in use" error (`WinError 32` / `5`), print
the same advice instead of the raw pip output.

### Case B: one folder used from both Windows and WSL

Example: the repo is on `/mnt/c/...`, and you run `install.py` from WSL and from Windows. A
venv only works on the OS that made it: Windows uses `Scripts\python.exe` and Linux uses
`bin/python`, and the compiled packages differ. `node_modules` also holds OS-specific
rolldown and lightningcss binaries. The built `frontend/dist/` and `data/` work on both.

Venv check:

- Read `pyvenv.cfg`. If `home =` is a Windows path (`C:\...`) while running on Linux, or a
  POSIX path while on Windows, the venv belongs to the other OS. It also does if the
  interpreter for this OS (`Scripts/python.exe` or `bin/python`) is missing or doesn't run.
- Ask: "backend/.venv was created on Windows and doesn't work in WSL. Recreate it? This
  breaks the Windows install of this folder. [y/N]". The default is **No**, because this breaks
  the other install. With `--yes`, stop and explain instead of recreating.
- Recommend the better setup: one clone per OS. For WSL, the clone goes in the Linux file
  system (`~/...`), which is also much faster than `/mnt/c`.

`node_modules` check (step 4):

- Vite 8 pulls in OS-specific packages, so check that a `node_modules/@rolldown/binding-<os>-*`
  folder exists for this OS (for example `binding-win32-x64-msvc` or `binding-linux-x64-gnu`).
  Any CPU is accepted: npm installs bindings for the Node that is used, which can differ from
  the real CPU (x64 Node under Rosetta or on Windows ARM). If it
  doesn't, run `npm ci`, which deletes `node_modules` and reinstalls it for this OS, and
  print why.
- This also covers `node_modules` folders made by `make setup`, without a marker file.

WSL on `/mnt/c` without any Windows install: just print a one-line tip that `~/...` is much
faster.

### Case C: a shortcut called "Instagraph" already exists

Before writing a shortcut, read where an existing one with the same name points:

| OS | How to read the target |
|---|---|
| Windows | PowerShell `CreateShortcut(<path>)` and read `.Arguments` (the path to `run.py`) |
| macOS | Parse the `.command` script for the repo path |
| Linux | Parse `Path=` from the `.desktop` file |

| It points to | What happens |
|---|---|
| This folder | Overwrite silently: "Desktop icon: updated" |
| A folder that no longer exists, or a broken shortcut | Replace it: "Desktop icon: replaced (it pointed to /old/path, which no longer exists)" |
| Another folder that still has Instagraph (`run.py` exists) | Ask: "A desktop icon for another Instagraph copy exists (/other/path). [K]eep both / [r]eplace it?" Keep both creates "Instagraph (\<folder name\>)" next to it. `--yes` always keeps both, because it's the non-destructive choice |
| Something that isn't Instagraph | Keep both, as "Instagraph (\<folder name\>)" |

On Linux, the second copy's file is named `instagraph-<short hash of repo path>.desktop`, so
both copies can be in the app menu. `--uninstall` removes only the shortcuts in its own
install record, never another copy's.

Two copies running at once: both use port 8000 by default, so the second copy's icon would just
open the first copy (`run.py`'s already-running check only sees "Instagraph"). It says on
screen which port it's opening. If you keep two copies, start one with `run.py --port 9000`.
Fixing this properly would need the server to report which folder it runs from, which is a
backend change outside this plan.

## `--uninstall`

Removes the shortcuts listed in `backend/.instagraph-install.json`, that file, and `backend/.venv`, including the
private Node in it. Asks before
deleting `frontend/dist` and `frontend/node_modules`. It **never deletes `data/`**: it prints
where the data is and says to delete it with Settings → Clear all data, or by hand.

## Steps

1. **Icon (done).** `assets/icon.svg`, rendered with the headless Chromium from Playwright,
   then turned into `icon.png` (512), `icon.ico` and `icon.icns` with Pillow. The `.ico` uses the
   simpler `frontend/public/favicon.svg` artwork at 16, 24 and 32 px, because the bubble is too
   small to read at those sizes. To regenerate: render `icon.svg` at 1024 px and the favicon at
   16/24/32 px, then save with Pillow (`Image.save(..., sizes=…, append_images=…)`).
2. **`run.py`**, then check it on Linux next to `./launch`: same URLs, LAN mode, already-running
   check, run file written and removed (also after Ctrl+C).
3. **`install.py` core:** Python check, venv, pip, frontend build with system Node, flags,
   and the "Existing installs" table. Test by running it twice in a row: the second run should
   change nothing and say so. Also run it on this machine's existing `make setup` install.
4. **Running-instance check (case A)** and **other-OS detection (case B)**. Test B by
   creating a venv in a folder on `/mnt/c` from WSL, then running `install.py` from Windows.
5. **Private Node.js:** download, checksum, safe unpack, reuse. Test it on Linux with
   `--private-node`.
6. **Desktop icons**, including existing shortcuts (case C): Linux first (testable here),
   then Windows and macOS. Test with two clones: run the installer in both, then uninstall one.
   The other copy's icon must survive.
7. **`--uninstall`** and the install record file.
8. **Docs and CI:**
   - README "Getting started": `python install.py` becomes the main way to install, and
     running it again updates. The only requirement is Python 3.12+; Node is downloaded if
     it's missing. Add the one-clone-per-OS tip for WSL users. `make setup` moves to
     Development.
   - A CI matrix job (ubuntu, macos, windows) runs
     `python install.py --yes --no-shortcut --private-node`, then `python run.py --no-browser`,
     and checks that `/openapi.json` answers. `--private-node` makes CI test the download path,
     because the runners already have Node installed.
   - The same job then runs `install.py --yes --no-shortcut` again while the server is still
     running. It must exit with the "Instagraph is running" message and a non-zero code
     (case A). Then it stops the server, runs the installer a third time, and expects success
     with nothing changed.

## Risks and things to check

- **Windows has never been tested.** The README only names Linux, macOS and WSL. The import
  pool already uses `spawn`, which is good, but also check:
  - paths with backslashes and non-ASCII user names (`C:\Users\Dušan`);
  - "Clear all data" while Parquet files are open (Windows locks open files);
  - paths longer than 260 characters inside unpacked exports.

  The CI job in step 8 catches install problems, but an import with a real export should be
  tried by hand once.
- **Wheels:** polars, pyarrow and orjson have wheels for Windows x64/ARM64, macOS
  (Intel and Apple Silicon) and manylinux. Very old Linux (glibc < 2.17) or 32-bit Windows
  would need compiling and isn't supported. Say so in the README.
- **Antivirus / SmartScreen:** a `.lnk` that starts `python.exe` is normal and shouldn't
  trigger warnings. Don't generate `.bat` or `.vbs` files, which are flagged more often.
- **Privacy note:** the installer downloads Python packages from PyPI, npm packages from the
  npm registry, and maybe Node.js from nodejs.org. The app itself still makes no network
  requests. The README should keep those two apart.
- **nodejs.org changes:** the download relies on `index.json`, `SHASUMS256.txt` and the archive
  names staying the same. They have been stable for years. If a download fails, the error
  message should still tell users they can install Node themselves.

## Manual checks (not possible in CI)

CI installs and starts the app on all three systems, but can't click icons. Once per system:

1. **Windows:** `py install.py`, answer yes to the icon. Check that the desktop (also a
   OneDrive desktop) and the Start menu show Instagraph with its icon, that double-clicking
   opens a console and the browser, and that closing the console stops the app. Run the
   installer again (expect "updated"), then `py install.py --uninstall`.
2. **macOS:** `python3 install.py` with Python from python.org, without running "Install
   Certificates.command" first (the Node download must still work if Node isn't installed).
   `Instagraph.command` on the desktop should have the icon and open Terminal.
3. **Linux desktop (GNOME/KDE):** the app menu entry appears; the desktop icon starts without
   the "untrusted launcher" warning.
4. **WSL:** answer yes to the Windows icon. The desktop and Start menu `.lnk` start Instagraph
   in the right distro; `%LOCALAPPDATA%\Instagraph\icon-<hash>.ico` exists; uninstall removes them.
