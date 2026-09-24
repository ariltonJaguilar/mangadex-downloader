import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal
from gui.bridge.download_bridge import DownloadBridge
from gui.bridge.history_bridge import HistoryBridge
from src.utils.config import ConfigManager
from src.utils.state import write_json


@pytest.fixture
def qt_app():
    return QCoreApplication.instance() or QCoreApplication([])


def test_preferences_persist_across_processes_and_working_directories(tmp_path):
    env = dict(os.environ, APPDATA=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path))
    env["PYTHONPATH"] = str(Path(__file__).parents[1])
    values = {"platform": "mangadex", "mangadex_language": "en", "headless": False,
              "pdf_layout": "webcomic", "pdf_page_width": 1200, "use_compressed_image": True,
              "fallback_english": True, "download_path": str(tmp_path / "Meus Mangás"),
              "temp_path": str(tmp_path / "Temporary pages"),
              "output_format": "cbz", "keep_images": True, "max_chapter_workers": 7,
              "max_image_workers": 11, "enable_logs": True}
    write_script = "from src.utils.config import ConfigManager; c=ConfigManager(); " + "; ".join(
        f"c.set({key!r}, {value!r})" for key, value in values.items())
    subprocess.run([sys.executable, "-c", write_script], cwd=tmp_path, env=env, check=True)
    result = subprocess.run([sys.executable, "-c",
        "import json; from src.utils.config import ConfigManager; print(json.dumps(ConfigManager().all_settings))"],
        cwd=Path(__file__).parents[1] / "gui", env=env, capture_output=True, text=True, check=True)
    saved = json.loads(result.stdout)
    assert all(saved[key] == value for key, value in values.items())


def test_history_limit_deduplication_title_and_restart(tmp_path, qt_app):
    config = ConfigManager(tmp_path / "config.json")
    history = HistoryBridge(config)
    for index in range(55):
        history.remember(f"Manga {index}", "comix", "")
    assert len(history.items) == 50
    history.remember("manga 20", "comix", "")
    assert len(history.items) == 50 and history.items[0]["value"] == "manga 20"
    link = "https://mangadex.org/title/00000000-0000-4000-8000-000000000001/a-title"
    history.remember(link, "comix", "")
    history.remember(link, "mangadex", "Title")
    again = HistoryBridge(config)
    assert len(again.items) == 50
    assert again.items[0]["title"] == "Title"
    assert again.items[0]["platform"] == "mangadex"
    assert again.items[0]["kind"] == "title"


class FakeWorker(QObject):
    detailProgress = pyqtSignal(str, int, int)
    detailComplete = pyqtSignal(str, bool, str)
    resultReady = pyqtSignal(int, int)
    error = pyqtSignal(str)
    finished = pyqtSignal()
    instances = []

    def __init__(self, manga, chapters, config):
        super().__init__()
        self.manga, self.chapters, self.config = manga, chapters, config
        self.instances.append(self)

    def start(self):
        pass


def enqueue(bridge, title="One", chapters=None):
    bridge.startDownload({"title": title, "source": "mangadex"},
                         chapters or [{"chapter_id": "a", "number": "1", "pages_count": 3}], "pdf", "Any")


@pytest.mark.parametrize("preference, expected", [
    ("Any", ["a1", "a2", "b3"]),
    ("B", ["b1", "b2", "b3"]),
])
def test_download_uses_source_priority_with_chapter_fallback(tmp_path, qt_app, preference, expected):
    bridge = DownloadBridge(config_manager=ConfigManager(tmp_path / "config.json"))
    rows = [
        {"chapter_id": "a1", "number": "1", "group_name": "A"},
        {"chapter_id": "b1", "number": "1", "group_name": "B"},
        {"chapter_id": "b2", "number": "2", "group_name": "B"},
        {"chapter_id": "a2", "number": "2", "group_name": "A"},
        {"chapter_id": "b3", "number": "3", "group_name": "B"},
    ]
    with patch.object(bridge, "_enqueue_download") as queue:
        bridge.startDownload({"title": "Manga", "source": "comix"}, rows, "pdf", preference)
    assert [row["chapter_id"] for row in queue.call_args.args[1]] == expected


def test_fifo_snapshots_and_waits_for_thread_to_exit(tmp_path, qt_app):
    bridge = DownloadBridge(config_manager=ConfigManager(tmp_path / "config.json"))
    bridge._config_manager.set("temp_path", str(tmp_path / "first-temp"))
    FakeWorker.instances = []
    with patch("gui.bridge.download_bridge.DownloadWorker", FakeWorker):
        enqueue(bridge)
        bridge._config_manager.set("pdf_layout", "webcomic")
        bridge._config_manager.set("temp_path", str(tmp_path / "second-temp"))
        enqueue(bridge, "Two")
        bridge._config_manager.set("temp_path", str(tmp_path / "third-temp"))
        assert len(FakeWorker.instances) == 1
        first = FakeWorker.instances[0]
        assert first.config.pdf_layout == "pages"
        assert first.config.temp_path == str((tmp_path / "first-temp").resolve())
        first.detailComplete.emit("a", True, "OK")
        first.resultReady.emit(1, 0)
        qt_app.processEvents()
        assert len(FakeWorker.instances) == 1
        first.finished.emit()
        qt_app.processEvents()
        assert len(FakeWorker.instances) == 2
        second = FakeWorker.instances[1]
        assert second.manga["title"] == "Two"
        assert second.config.pdf_layout == "webcomic"
        assert second.config.temp_path == str((tmp_path / "second-temp").resolve())
        second.detailComplete.emit("a", True, "OK")
        second.finished.emit()
        assert [j["status"] for j in bridge.jobs] == ["complete", "complete"]
        assert DownloadBridge(config_manager=bridge._config_manager).jobs[0]["successful"] == 1


def test_queued_cancel_and_retry_skips_successful_chapters(tmp_path, qt_app):
    bridge = DownloadBridge(config_manager=ConfigManager(tmp_path / "config.json"))
    FakeWorker.instances = []
    with patch("gui.bridge.download_bridge.DownloadWorker", FakeWorker):
        enqueue(bridge, chapters=[{"chapter_id": "a", "number": "1"}, {"chapter_id": "b", "number": "2"}])
        enqueue(bridge, "Cancel me")
        bridge.cancelJob(bridge.jobs[1]["id"])
        first = FakeWorker.instances[0]
        first.detailComplete.emit("a", True, "OK")
        first.detailComplete.emit("b", False, "Network error")
        first.finished.emit()
        bridge.resumeJob(bridge.jobs[0]["id"])
        assert [ch["chapter_id"] for ch in FakeWorker.instances[-1].chapters] == ["b"]
        FakeWorker.instances[-1].detailComplete.emit("b", True, "OK")
        FakeWorker.instances[-1].finished.emit()
        assert bridge.jobs[0]["status"] == "complete"
        assert bridge.jobs[1]["status"] == "cancelled"
        bridge.clearFinished()
        assert bridge.jobs == []


def test_restart_preserves_queue_and_marks_interrupted_job(tmp_path, qt_app):
    config = ConfigManager(tmp_path / "config.json")
    bridge = DownloadBridge(config_manager=config)
    with patch("gui.bridge.download_bridge.DownloadWorker", FakeWorker):
        enqueue(bridge)
        enqueue(bridge, "Pending")
        restored = DownloadBridge(config_manager=config)
        assert [j["status"] for j in restored.jobs] == ["paused", "queued"]
        bridge._worker.detailComplete.emit("a", True, "OK")
        bridge._worker.finished.emit()
        bridge._closing = True


def test_atomic_write_leaves_previous_state_on_replace_failure(tmp_path):
    path = tmp_path / "state.json"
    write_json(path, {"before": True})
    with patch("src.utils.state.os.replace", side_effect=OSError("disk failure")):
        with pytest.raises(OSError):
            write_json(path, {"after": True})
    assert json.loads(path.read_text()) == {"before": True}
    assert not list(tmp_path.glob("*.tmp"))


def test_close_pauses_current_job_and_does_not_start_the_next(tmp_path, qt_app):
    bridge = DownloadBridge(config_manager=ConfigManager(tmp_path / "config.json"))
    FakeWorker.instances = []
    closed = []
    bridge.shutdownReady.connect(lambda: closed.append(True))
    with patch("gui.bridge.download_bridge.DownloadWorker", FakeWorker):
        enqueue(bridge)
        enqueue(bridge, "Next")
        bridge.prepareShutdown()
        assert bridge.jobs[0]["status"] == "cancelling"
        assert not closed
        bridge._worker.finished.emit()
        qt_app.processEvents()
        assert closed == [True]
        assert len(FakeWorker.instances) == 1
        assert [job["status"] for job in bridge.jobs] == ["paused", "queued"]
        restored = DownloadBridge(config_manager=bridge._config_manager)
        assert [job["status"] for job in restored.jobs] == ["paused", "queued"]


def test_worker_error_finishes_current_job_and_advances_queue(tmp_path, qt_app):
    bridge = DownloadBridge(config_manager=ConfigManager(tmp_path / "config.json"))
    FakeWorker.instances = []
    with patch("gui.bridge.download_bridge.DownloadWorker", FakeWorker):
        enqueue(bridge)
        enqueue(bridge, "Next")
        first = FakeWorker.instances[0]
        first.error.emit("Browser failed")
        first.finished.emit()
        qt_app.processEvents()
        assert bridge.jobs[0]["status"] == "failed"
        assert bridge.jobs[0]["message"] == "Browser failed"
        assert bridge.jobs[1]["status"] == "running"
        bridge._worker.detailComplete.emit("a", True, "OK")
        bridge._worker.finished.emit()
