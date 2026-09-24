import re
from unittest.mock import patch

import pytest

from gui.bridge.download_bridge import DownloadBridge
from src.api.comix import ChapterImageFetchReport
from src.utils.config import ConfigManager
from tests.test_books import app, chapters, data_url, image_data, manga, wait_for_job


def test_stalled_final_browser_attempt_times_out_and_still_publishes_pdf(tmp_path, app, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from src.api import comix
    from gui.bridge import download_bridge
    monkeypatch.setattr(download_bridge, "FINAL_CHAPTER_SECONDS", 0.05)
    monkeypatch.setattr(download_bridge, "FINAL_RETRY_SECONDS", 0.1)
    config = ConfigManager(tmp_path / "config.json")
    config.set("download_path", str(tmp_path / "output"))
    config.set("temp_path", str(tmp_path / "scratch"))
    bridge = DownloadBridge(config_manager=config)
    calls, cancelled = [], []

    async def extract(*args, **kwargs):
        calls.append(1)
        if len(calls) <= 2:
            return ChapterImageFetchReport([data_url(image_data("red"))], 2, [], [2], [1])
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    with patch.object(comix, "_start_comix_browser", AsyncMock(return_value=object())), \
         patch.object(comix, "_close_comix_browser", AsyncMock()), \
         patch.object(comix.ComixAPI, "_get_chapter_images_async", side_effect=extract):
        wait_for_job(bridge, lambda: bridge.startDownload(dict(manga(), source="comix"), chapters()[:1], "pdf", "Any"))
    assert cancelled == [True]
    assert len(calls) == 3
    assert bridge.jobs[0]["status"] == "partial"
    assert bridge.jobs[0]["failed_pages"] == 1
    assert len(list((tmp_path / "output").glob("*.pdf"))) == 1


@pytest.mark.parametrize("recover", [True, False])
def test_pdf_retries_failed_chapter_at_end_then_publishes_available_pages(tmp_path, app, recover):
    config = ConfigManager(tmp_path / "config.json")
    config.set("download_path", str(tmp_path / "output"))
    config.set("temp_path", str(tmp_path / "scratch"))
    config.set("keep_images", False)
    bridge = DownloadBridge(config_manager=config)
    metadata = dict(manga(), source="comix", selected_language="en")
    rows = [dict(row, language="en", pages_count=0) for row in chapters()]
    urls = [data_url(image_data(color)) for color in ("red", "blue", "yellow")]
    calls = []
    b_calls = 0

    def report(identifier, *args, **kwargs):
        nonlocal b_calls
        calls.append(identifier)
        if identifier == "a":
            return ChapterImageFetchReport([data_url(image_data("black"))], 1, [], [], [1])
        b_calls += 1
        if recover and b_calls > 2:
            return ChapterImageFetchReport(urls, 3, [], [], [1, 2, 3])
        return ChapterImageFetchReport([urls[0], urls[2]], 3, [], [2], [1, 3])

    with patch("src.api.comix.ChapterReaderService.fetch", side_effect=report):
        wait_for_job(bridge, lambda: bridge.startDownload(metadata, rows, "pdf", "Any"))
    job = bridge.jobs[0]
    assert calls.count("a") == 1
    assert b_calls == (3 if recover else 4)  # extraction recovery plus final round
    assert job["status"] == ("complete" if recover else "partial")
    assert job["failed_pages"] == (0 if recover else 1)
    assert job["unknown_page_chapters"] == 0
    assert job["successful"] == (2 if recover else 1)
    pdfs = list((tmp_path / "output").glob("*.pdf"))
    assert len(pdfs) == 1
    assert len(re.findall(rb"/Type\s*/Page\b", pdfs[0].read_bytes())) == (5 if recover else 4)
    if not recover:
        assert "1 páginas falharam" in job["message"]
        restored = DownloadBridge(config_manager=config)
        with patch("src.api.comix.ChapterReaderService.fetch", return_value=ChapterImageFetchReport(urls, 3, [], [], [1, 2, 3])) as fetch:
            wait_for_job(restored, lambda: restored.resumeJob(job["id"]))
        assert [call.args[0] for call in fetch.call_args_list] == ["b"]
        assert restored.jobs[0]["status"] == "complete"
        assert restored.jobs[0]["failed_pages"] == 0
        assert len(re.findall(rb"/Type\s*/Page\b", pdfs[0].read_bytes())) == 5


def test_pdf_reports_unknown_chapter_size_separately(tmp_path, app):
    config = ConfigManager(tmp_path / "config.json")
    config.set("download_path", str(tmp_path / "output"))
    config.set("temp_path", str(tmp_path / "scratch"))
    bridge = DownloadBridge(config_manager=config)

    def report(identifier, **kwargs):
        if identifier == "b":
            raise RuntimeError("Chapter unavailable")
        return ChapterImageFetchReport([data_url(image_data("red"))], 1, [], [], [1])

    rows = [dict(row, pages_count=0) for row in chapters()]
    with patch("src.api.mangadex.MangaDexAPI.get_chapter_image_report", side_effect=report) as fetch:
        wait_for_job(bridge, lambda: bridge.startDownload(manga(), rows, "pdf", "Any"))
    job = bridge.jobs[0]
    assert job["status"] == "partial"
    assert job["failed_pages"] == 0
    assert job["unknown_page_chapters"] == 1
    assert "sem total de páginas identificado" in job["message"]
    assert [call.args[0] for call in fetch.call_args_list].count("b") == 2


def test_block_does_not_trigger_final_network_retry_but_allows_partial_pdf(tmp_path, app, monkeypatch):
    from src.utils.comix_limits import comix_limits
    monkeypatch.setattr(comix_limits, "reason", "")
    monkeypatch.setattr(comix_limits, "resume_at", 0)
    config = ConfigManager(tmp_path / "config.json")
    config.set("download_path", str(tmp_path / "output"))
    config.set("temp_path", str(tmp_path / "scratch"))
    bridge = DownloadBridge(config_manager=config)
    calls = []
    def report(identifier, *args, **kwargs):
        calls.append(identifier)
        if len(calls) > 1:
            raise comix_limits.block("HTTP 429", "120")
        return ChapterImageFetchReport([data_url(image_data("red"))], 2, [], [2], [1])
    with patch("src.api.comix.ChapterReaderService.fetch", side_effect=report):
        wait_for_job(bridge, lambda: bridge.startDownload(dict(manga(), source="comix"), chapters()[:1], "pdf", "Any"))
    assert len(calls) == 2
    assert bridge.jobs[0]["status"] == "partial"
    assert bridge.jobs[0]["failed_pages"] == 1
    assert len(list((tmp_path / "output").glob("*.pdf"))) == 1
