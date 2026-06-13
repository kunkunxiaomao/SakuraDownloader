from __future__ import annotations

import unittest
from pathlib import Path

from pixiv_app.core.plugin.manager import PluginManager


class PluginManagerTests(unittest.TestCase):
    def test_manager_init(self) -> None:
        """PluginManager should initialize and discover plugin roots."""
        mgr = PluginManager([])
        mgr.load_all()
        self.assertEqual(len(mgr.plugins), 0)

    def test_discover_empty_directory(self) -> None:
        """Discovering an empty directory should return no plugins."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            mgr = PluginManager([Path(tmp)])
            paths = mgr.discover()
            self.assertEqual(len(paths), 0)


if __name__ == "__main__":
    unittest.main()
