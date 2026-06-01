# Build Instructions

## Recommended: PowerShell Build

```powershell
.\scripts\build_exe.ps1
```

The executable will be generated at:

```text
dist\sakuradownloader.exe
```

The PyInstaller spec includes:

- `pixiv_app/webui/`
- bundled `plugins/`
- `build_assets/app_icon.ico`

The build intentionally does not bundle sensitive or user-generated data:

- `runtime/`
- `profiles/`
- `Sakura_Downloads/`
- `pixiv_app_session.json`
- `pixiv_app_library.db*`
- cookie txt/json exports

When running the packaged exe, cookies/session/profile runtime data are stored under:

```text
%LOCALAPPDATA%\SakuraDownloader
```

## Playwright Chromium For Users Without Python

Ship these files together if users need X / Xiaohongshu browser features:

```text
sakuradownloader.exe
scripts\install_playwright_chromium.bat
scripts\install_playwright_chromium.ps1
```

The user can double-click `install_playwright_chromium.bat`. It downloads the Chromium build required by Playwright into:

```text
%LOCALAPPDATA%\ms-playwright
```

No Python installation is required for this installer script.

## Manual Build

```powershell
pip install -r requirements.txt
pip install pyinstaller
python -m PyInstaller --clean --noconfirm SakuraDownloader.spec
```
