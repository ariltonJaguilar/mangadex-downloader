import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from PyQt6.QtCore import QCoreApplication
    from gui.bridge.discovery_bridge import DiscoveryBridge
    from gui.bridge.download_bridge import DownloadBridge
    from gui.bridge.manga_bridge import FetchWorker, MangaBridge
    from gui.bridge.settings_bridge import SettingsBridge
except ImportError:  # pragma: no cover - exercised only in minimal environments
    QCoreApplication = None
    DiscoveryBridge = None
    DownloadBridge = None
    FetchWorker = None
    MangaBridge = None
    SettingsBridge = None

from src.api.comix import ComixAPI
from src.core.models import Chapter, MangaInfo
from src.utils.config import ConfigManager


@unittest.skipUnless(QCoreApplication is not None, "PyQt6 is not installed")
class GuiHeadlessConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QCoreApplication.instance() or QCoreApplication([])

    def _config(self, directory: str, headless: bool = False) -> ConfigManager:
        config_path = Path(directory) / "config.json"
        config_path.write_text(json.dumps({"headless": headless}), encoding="utf-8")
        return ConfigManager(config_path)

    def test_settings_toggle_updates_every_gui_bridge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_manager = self._config(tmpdir, headless=False)
            manga_bridge = MangaBridge(config_manager=config_manager)
            download_bridge = DownloadBridge(config_manager=config_manager)
            settings_bridge = SettingsBridge(config_manager=config_manager)

            self.assertIs(manga_bridge._config_manager, config_manager)
            self.assertIs(download_bridge._config_manager, config_manager)
            self.assertIs(settings_bridge._config_manager, config_manager)
            self.assertFalse(download_bridge._config_manager.get_download_config().headless)

            settings_bridge.setValue("headless", True)

            self.assertTrue(config_manager.get_download_config().headless)
            self.assertTrue(manga_bridge._config_manager.get_download_config().headless)
            self.assertTrue(download_bridge._config_manager.get_download_config().headless)

    def test_manga_bridge_snapshots_current_headless_setting(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_manager = self._config(tmpdir, headless=False)
            bridge = MangaBridge(config_manager=config_manager)

            with patch("gui.bridge.manga_bridge.FetchWorker") as worker_factory:
                worker_factory.return_value.isRunning.return_value = False
                bridge.fetchManga("https://comix.to/title/example")
                self.assertFalse(worker_factory.call_args.kwargs["headless"])

                config_manager.set("headless", True)
                bridge.fetchManga("https://comix.to/title/example")
                self.assertTrue(worker_factory.call_args.kwargs["headless"])

    def test_fetch_worker_passes_one_snapshot_to_both_api_calls(self):
        manga = MangaInfo(hash_id="abc", slug="example", title="Example")
        chapters = [Chapter(chapter_id=1, number="1")]
        worker = FetchWorker("https://comix.to/title/example", headless=True)

        with patch.object(ComixAPI, "get_manga_info", return_value=manga) as manga_info, \
                patch.object(ComixAPI, "get_all_chapters", return_value=chapters) as all_chapters:
            worker.run()

        self.assertTrue(manga_info.call_args.kwargs["headless"])
        self.assertTrue(all_chapters.call_args.kwargs["headless"])

    def test_fetch_worker_preserves_poster_url_and_adds_provider_source(self):
        poster_url = "https://static.comix.to/40e8/i/9/fa/cover.jpg"
        manga = MangaInfo(hash_id="abc", slug="example", title="Example", poster_url=poster_url)
        worker = FetchWorker("https://comix.to/title/example", headless=True)
        loaded = []
        worker.finished.connect(loaded.append)

        with patch.object(ComixAPI, "get_manga_info", return_value=manga), \
                patch.object(ComixAPI, "get_all_chapters", return_value=[]):
            worker.run()

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["poster_url"], poster_url)
        self.assertTrue(loaded[0]["poster_source"].startswith("image://comix-cover/"))

    def test_discovery_bridge_uses_shared_config_manager(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_manager = self._config(tmpdir, headless=False)
            bridge = DiscoveryBridge(config_manager=config_manager)

            self.assertIs(bridge._config_manager, config_manager)
            with patch("gui.bridge.discovery_bridge.DiscoveryWorker") as worker_factory:
                bridge.loadHighlights()
                self.assertFalse(worker_factory.call_args.kwargs["headless"])

    def test_discovery_bridge_snapshots_headless_setting_for_search(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_manager = self._config(tmpdir, headless=False)
            bridge = DiscoveryBridge(config_manager=config_manager)

            with patch("gui.bridge.discovery_bridge.DiscoveryWorker") as worker_factory:
                bridge.search("hero", 2)
                self.assertEqual(worker_factory.call_args.args[:3], ("search", "hero", 2))
                self.assertFalse(worker_factory.call_args.kwargs["headless"])


if __name__ == "__main__":
    unittest.main()
