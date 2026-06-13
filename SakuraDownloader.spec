# -*- mode: python ; coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path


block_cipher = None
root = Path.cwd()


def tree_datas(source: str, dest: str, suffixes: tuple[str, ...] | None = None):
    base = root / source
    items = []
    if not base.exists():
        return items
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        parts = set(path.parts)
        if "__pycache__" in parts:
            continue
        if path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        if suffixes and path.suffix.lower() not in suffixes:
            continue
        rel_parent = path.parent.relative_to(base)
        items.append((str(path), str(Path(dest) / rel_parent)))
    return items


datas = []
datas += tree_datas("pixiv_app/webui", "pixiv_app/webui", (".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico"))


hiddenimports = [
    "PIL",
    "PIL.Image",
    "customtkinter",
    "requests",
]


a = Analysis(
    ["main.py"],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "unittest",
        "tkinter.test",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="sakuradownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(root / "build_assets" / "app_icon.ico"),
)
