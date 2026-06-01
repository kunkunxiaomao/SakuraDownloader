# SakuraDownloader

SakuraDownloader is a local media downloader and gallery tool with bundled plugins for Pixiv, X / Twitter, and Xiaohongshu. It also supports user-imported Python plugins so new site integrations can be added without rebuilding the executable.

## Features

- Bundled plugins: Pixiv, X / Twitter, Xiaohongshu
- Import custom Python plugins from the GUI
- Cookie txt/json import
- Local gallery service with thumbnail wall
- SQLite-backed local library
- Tag/search/favorite/artist views
- Legacy download directory scan
- Optional Playwright browser workflows

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

## Run

```powershell
python main.py
```

## Build EXE

```powershell
python -m PyInstaller --clean --noconfirm SakuraDownloader.spec
```

The generated executable is:

```text
dist/sakuradownloader.exe
```

## Sensitive Data

Runtime data is intentionally not included in this repository:

- cookies
- sessions
- browser profiles
- local database files
- downloaded media
- packaged exe/zip artifacts

When packaged, user data is stored under:

```text
%LOCALAPPDATA%\SakuraDownloader
```

## Docs

More Chinese documentation is available in `docs/`.
