import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.utils import comix_limits as limits_module
from src.utils.comix_limits import ComixBlockedError, ComixLimits, comix_limits, retry_after_seconds
from src.core.downloader import ImageDownloader, reset_downloads
from src.core.models import DownloadConfig


@pytest.fixture(autouse=True)
def isolated_limits(monkeypatch):
    monkeypatch.setattr(comix_limits, "reason", "")
    monkeypatch.setattr(comix_limits, "resume_at", 0.0)
    monkeypatch.setattr(comix_limits, "_next", {})
    reset_downloads()


def test_parallel_images_share_one_pacing_clock(monkeypatch):
    monkeypatch.setattr(limits_module.time, "monotonic", lambda: 100)
    guard = ComixLimits()
    with ThreadPoolExecutor(max_workers=8) as pool:
        delays = list(pool.map(lambda _: guard.delay("image", 0.5), range(8)))
    assert delays.count(0) == 1
    assert all(delay == 0.1 for delay in delays if delay)
    monkeypatch.setattr(limits_module.time, "monotonic", lambda: 100.5)
    assert guard.delay("image", 0.5) == 0


def test_pause_between_completed_chapters_and_cancel_during_wait(monkeypatch):
    monkeypatch.setattr(limits_module.time, "monotonic", lambda: 100)
    guard = ComixLimits()
    guard.chapter_finished()
    assert guard._next["chapter"] == 105
    with pytest.raises(InterruptedError):
        asyncio.run(guard.wait_chapter(lambda: True))
    with pytest.raises(InterruptedError):
        guard.wait_image(lambda: True)


@pytest.mark.parametrize("status", [403, 429])
def test_refusal_stops_other_images_without_retry_and_honors_retry_after(status, monkeypatch):
    monkeypatch.setattr(limits_module.time, "monotonic", lambda: 100)
    response = SimpleNamespace(status_code=status, headers={"Retry-After": "120"})
    from unittest.mock import MagicMock
    session = MagicMock()
    session.get.return_value.__enter__.return_value = response
    downloader = ImageDownloader(DownloadConfig(retry_count=3), source="comix")
    with patch("src.core.downloader.get_session", return_value=session):
        assert f"HTTP {status}" in downloader.download_image("https://cdn.example/1", 1)[2]
        assert f"HTTP {status}" in downloader.download_image("https://cdn.example/2", 2)[2]
    assert session.get.call_count == 1
    assert comix_limits.resume_at == 220
    with pytest.raises(ComixBlockedError):
        comix_limits.resume()
    monkeypatch.setattr(limits_module.time, "monotonic", lambda: 221)
    # Expiration alone must not restart downloads.
    with pytest.raises(ComixBlockedError):
        comix_limits.delay("image", 0.5)
    comix_limits.resume()
    assert not comix_limits.reason


def test_retry_after_accepts_http_date_and_bad_header():
    value = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120), usegmt=True)
    assert 118 <= retry_after_seconds(value) <= 120
    assert retry_after_seconds("not a date") == 0
    assert retry_after_seconds(None) == 0


def test_cooldown_restores_after_process_restart(monkeypatch):
    monkeypatch.setattr(limits_module.time, "monotonic", lambda: 100)
    monkeypatch.setattr(limits_module.time, "time", lambda: 1000)
    first = ComixLimits()
    first.block("HTTP 429", "120")
    timestamp = first.resume_timestamp()
    monkeypatch.setattr(limits_module.time, "monotonic", lambda: 10)
    monkeypatch.setattr(limits_module.time, "time", lambda: 1030)
    restarted = ComixLimits()
    restarted.restore_pause("HTTP 429", timestamp)
    assert restarted.resume_at == 100
    with pytest.raises(ComixBlockedError):
        restarted.resume()


def test_block_page_does_not_open_captcha_window():
    from src.api import comix
    async def evaluate(script):
        return "Sorry, you have been blocked. Why have I been blocked?" if "#cf-error-details" in script else True
    page = SimpleNamespace(evaluate=evaluate)
    with patch.object(comix, "verify_headless_session", AsyncMock()) as verify:
        with pytest.raises(ComixBlockedError):
            asyncio.run(comix._wait_for_comix_page(page, "ready", headless=True, browser=object(), operation="reader"))
    verify.assert_not_awaited()
    assert comix_limits.reason


def test_visible_verification_stops_on_block_page():
    from src.api.comix_verification import _wait_for_human
    page = SimpleNamespace(evaluate=AsyncMock(return_value="Why have I been blocked?"))
    with pytest.raises(ComixBlockedError):
        asyncio.run(_wait_for_human(SimpleNamespace(stopped=False), page, "ready", None, 1))


def test_block_pauses_active_and_queued_comix_preserving_completed_pages(tmp_path):
    from PyQt6.QtCore import QCoreApplication
    from gui.bridge.download_bridge import DownloadBridge
    from src.utils.config import ConfigManager
    from tests.test_history_queue import FakeWorker
    app = QCoreApplication.instance() or QCoreApplication([])
    bridge = DownloadBridge(config_manager=ConfigManager(tmp_path / "config.json"))
    chapters = [{"chapter_id": "a", "number": "1"}, {"chapter_id": "b", "number": "2"}]
    FakeWorker.instances = []
    with patch("gui.bridge.download_bridge.DownloadWorker", FakeWorker):
        bridge.startDownload({"title": "One", "source": "comix"}, chapters, "pdf", "Any")
        bridge.startDownload({"title": "Two", "source": "comix"}, chapters, "pdf", "Any")
        first = FakeWorker.instances[0]
        first.detailComplete.emit("a", True, "OK")
        comix_limits.block("HTTP 429", "120")
        first.detailComplete.emit("b", False, "HTTP 429")
        first.finished.emit()
        app.processEvents()
        assert [job["status"] for job in bridge.jobs] == ["paused", "paused"]
        assert bridge.jobs[0]["details"][0]["status"] == "complete"
        assert len(FakeWorker.instances) == 1
        bridge.resumeJob(bridge.jobs[0]["id"])
        assert len(FakeWorker.instances) == 1
        comix_limits.resume_at = 0
        bridge.resumeJob(bridge.jobs[0]["id"])
        second = FakeWorker.instances[-1]
        assert [chapter["chapter_id"] for chapter in second.chapters] == ["b"]
        second.detailComplete.emit("b", True, "OK")
        second.finished.emit()
        app.processEvents()
        assert bridge.jobs[0]["status"] == "complete"
        assert bridge.jobs[1]["status"] == "paused"
