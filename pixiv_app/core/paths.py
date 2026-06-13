from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "SakuraDownloader"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS")).resolve()
    return Path(__file__).resolve().parents[2]


def executable_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return resource_root()


def data_root() -> Path:
    override = os.environ.get("SAKURA_APP_DATA_DIR")
    if override:
        root = Path(override)
    elif is_frozen():
        local_appdata = os.environ.get("LOCALAPPDATA")
        root = Path(local_appdata) / APP_NAME if local_appdata else executable_dir() / "data"
    else:
        root = resource_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def runtime_path(*parts: str) -> Path:
    path = data_root() / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path.joinpath(*parts)


def profiles_root() -> Path:
    path = data_root() / "profiles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def plugins_root() -> Path:
    return resource_root() / "plugins"


def user_plugins_root() -> Path:
    path = data_root() / "plugins"
    path.mkdir(parents=True, exist_ok=True)
    return path


def plugin_roots() -> list[Path]:
    roots = [plugins_root(), user_plugins_root()]
    result: list[Path] = []
    for root in roots:
        if root not in result:
            result.append(root)
    return result


def webui_root() -> Path:
    return resource_root() / "pixiv_app" / "webui"


def app_session_file() -> Path:
    return data_root() / "sakura_session.json"


def downloads_root() -> Path:
    path = executable_dir() / "Sakura_Downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path
