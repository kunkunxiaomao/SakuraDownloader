from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pixiv_app.core.plugin.manager import PluginManager


class PluginManagerTests(unittest.TestCase):
    def test_discover_and_load_pixiv(self) -> None:
        root = Path(__file__).resolve().parents[1]
        plugins_dir = root / "plugins"
        plugin_py = plugins_dir / "pixiv" / "plugin.py"
        if not plugin_py.is_file():
            self.skipTest("bundled plugins/pixiv not present")

        if str(root) not in sys.path:
            sys.path.insert(0, str(root))

        mgr = PluginManager([plugins_dir])
        paths = mgr.discover()
        self.assertTrue(any(p.parent.name == "pixiv" for p in paths))

        plugin = mgr.load_plugin(plugin_py)
        self.assertIsNotNone(plugin)
        assert plugin is not None
        self.assertTrue(plugin.can_handle("https://www.pixiv.net/artworks/12345"))
        mgr.unload_plugin(plugin.name)


if __name__ == "__main__":
    unittest.main()
