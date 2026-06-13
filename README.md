# SakuraDownloader

A plugin-driven local media download framework. Import Python plugins to add site support — no rebuild required.

## Features

- Plugin system with auto-discovery and hot reload
- Import custom Python plugins from the GUI
- Plugin template generator (Jinja2)
- Cookie txt/json import
- Local gallery service with thumbnail wall
- SQLite-backed local library
- Tag / search / favorite / artist views
- Legacy download directory scan
- Proxy pool with Quake API integration
- Distributed crawler scaffold (Redis-based)

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
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

## Plugin Development

Create a plugin by subclassing `BasePlugin` from `pixiv_app.core.plugin.base`:

```python
from pixiv_app.core.plugin.base import BasePlugin, Resource

class MyPlugin(BasePlugin):
    name = "MySite"
    domain = "example.com"

    def can_handle(self, url: str) -> bool:
        return "example.com" in url

    def parse(self, url: str) -> list[Resource]:
        ...

    def download(self, resource: Resource, save_path: Path) -> list[Path]:
        ...

    def get_headers(self) -> dict[str, str]:
        return {}
```

Use the GUI's **插件管理** → **生成模板** to scaffold a new plugin directory.

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
