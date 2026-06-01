"""Install Python deps and Chromium for the X plugin."""

from __future__ import annotations

import subprocess
import sys


def main() -> None:
    deps = ["playwright", "httpx"]
    for dep in deps:
        subprocess.check_call([sys.executable, "-m", "pip", "install", dep])
    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    print("Done. Restart the app and reload plugins.")


if __name__ == "__main__":
    main()
