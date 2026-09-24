import io
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from src.api.mangadex import MangaDexAPI
from src.core.downloader import ChapterDownloader, cancel_downloads, reset_downloads
from src.core.models import DownloadConfig, MangaInfo, Chapter, OutputFormat
from src.utils.page_cache import PageCache, clear_pages, page_session, register


@pytest.mark.parametrize("interrupted", [True, False])
def test_restart_reuses_complete_pages_and_retries_missing(tmp_path, interrupted):
    reset_downloads()
    stream = io.BytesIO()
    Image.new("RGB", (20, 30)).save(stream, "PNG")
    cfg = DownloadConfig(download_path=str(tmp_path / "out"), temp_path=str(tmp_path / "temp"),
                         keep_images=True, output_format=OutputFormat.PDF, max_image_workers=1)
    manga = MangaInfo(source="mangadex", hash_id="manga")
    chapter = Chapter("chapter", "1")
    requested = []

    def first(url, index):
        if index == 1:
            if interrupted:
                cancel_downloads()
            return index, stream.getvalue(), None
        return index, None, "Interrupted"

    response = {"baseUrl": "https://example.org", "chapter": {"hash": "hash", "data": ["1.png", "2.png"]}}
    with patch.object(MangaDexAPI, "_get", return_value=response), patch(
            "src.core.downloader.ImageDownloader.download_image", side_effect=first):
        assert not ChapterDownloader(cfg, manga).download_chapter(chapter)[0]
    assert list((tmp_path / "temp").rglob("*.page"))
    assert not list((tmp_path / "temp").glob("chapter-*"))
    reset_downloads()

    def second(url, index):
        requested.append(index)
        return index, stream.getvalue(), None

    with patch.object(MangaDexAPI, "_get", return_value=response), patch(
            "src.core.downloader.ImageDownloader.download_image", side_effect=second):
        assert ChapterDownloader(cfg, manga).download_chapter(chapter)[0]
    assert requested == [2]
    pdf = next((tmp_path / "out").rglob("*.pdf"))
    unrelated = tmp_path / "temp" / "personal.png"
    unrelated.write_bytes(b"Keep")
    assert clear_pages() == 4  # Two cached pages and two exported copies.
    assert pdf.exists() and unrelated.exists()
    assert not list((tmp_path / "out").rglob("*.png"))
    assert not list((tmp_path / "temp").rglob("*.page"))


def test_cache_validates_pages_identity_and_quality(tmp_path):
    cfg = DownloadConfig(temp_path=str(tmp_path))
    manga = MangaInfo(source="mangadex", hash_id="manga")
    cache = PageCache(cfg, manga, Chapter("one", "1"), ["https://cdn/a.png?token=old"])
    cache.put(1, b"corrupted")
    assert cache.get(1) is None
    assert PageCache(cfg, manga, Chapter("one", "1"), ["https://new/a.png?token=new"]).root == cache.root
    assert PageCache(cfg, manga, Chapter("two", "1"), ["https://cdn/a.png"]).root != cache.root
    cfg.use_compressed_image = True
    assert PageCache(cfg, manga, Chapter("one", "1"), ["https://cdn/a.png"]).root != cache.root


def test_cleanup_button_blocks_active_download_and_clears_previous_locations(tmp_path):
    from gui.bridge.settings_bridge import SettingsBridge
    from src.utils.config import ConfigManager
    bridge = SettingsBridge(config_manager=ConfigManager(tmp_path / "config.json"))
    errors, messages = [], []
    bridge.errorOccurred.connect(errors.append)
    bridge.pagesCleared.connect(messages.append)
    paths = [tmp_path / "previous" / "page.png", tmp_path / "current" / "page.png"]
    for path in paths:
        path.parent.mkdir()
        path.write_bytes(b"page")
        register(path)
    with page_session():
        bridge.clearDownloadedPages()
        assert all(path.exists() for path in paths)
    assert errors
    bridge.clearDownloadedPages()
    assert messages and not any(path.exists() for path in paths)
