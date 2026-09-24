import io
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image

from src.api.mangadex import MangaDexAPI
from src.api.providers import resolve_title_url
from src.core.models import Chapter, DownloadConfig, MangaInfo, OutputFormat
from src.core.downloader import ChapterDownloader, reset_downloads

TITLE = "00000000-0000-4000-8000-000000000001"


@pytest.mark.parametrize("source,expected_success,attempts", [("mangadex", True, 2), ("comix", False, 1)])
def test_transient_image_404_retries_only_for_mangadex(source, expected_success, attempts):
    import requests
    from src.core.downloader import ImageDownloader
    reset_downloads()
    stream = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(stream, "PNG")
    missing, success = MagicMock(), MagicMock()
    missing.status_code = 404
    missing.raise_for_status.side_effect = requests.HTTPError("404 Not Found", response=missing)
    success.iter_content.return_value = [stream.getvalue()]
    for response in (missing, success):
        response.__enter__.return_value = response
    with patch("src.core.downloader.get_session") as session, patch("src.core.downloader.time.sleep"):
        session.return_value.get.side_effect = [missing, success]
        _, data, error = ImageDownloader(DownloadConfig(retry_delay=0), source=source).download_image(
            "https://node.mangadex.network/data/hash/1.png", 1)
    assert (data is not None) == expected_success
    assert session.return_value.get.call_count == attempts
    if expected_success:
        assert error is None
        for call in session.return_value.get.call_args_list:
            assert call.kwargs["headers"]["Referer"] == "https://mangadex.org/"
            assert call.kwargs["headers"]["User-Agent"] == "MangaDownloader/1.0"
        assert session.return_value.get.call_args.kwargs["headers"]["Connection"] == "close"


def test_url_routing():
    assert resolve_title_url(f"mangadex.org/title/{TITLE}/example") == ("mangadex", TITLE)
    assert resolve_title_url("https://comix.to/title/abc-example") == ("comix", "abc")
    for url in [f"https://mangadex.org.evil.test/title/{TITLE}",
                f"https://evil.test/mangadex.org/title/{TITLE}",
                "https://mangadex.org/title/not-a-uuid", f"https://mangadex.org/chapter/{TITLE}"]:
        with pytest.raises(ValueError):
            resolve_title_url(url)


def chapter_row(identifier, **attrs):
    return {"id": identifier, "attributes": {"chapter": "1", "volume": "2", "pages": 3,
            "translatedLanguage": "pt-br", **attrs},
            "relationships": [{"type": "scanlation_group", "attributes": {"name": "Group"}}]}


def test_feed_pagination_language_and_external_filter():
    api = MangaDexAPI()
    with patch.object(api, "_get", side_effect=[
        {"data": [chapter_row("a"), chapter_row("external", externalUrl="https://example.org")], "total": 4},
        {"data": [chapter_row("a"), chapter_row("b", chapter=None)], "total": 4},
    ]) as get:
        chapters = api.get_all_chapters(TITLE)
    assert [ch.chapter_id for ch in chapters] == ["a", "b"]
    assert chapters[1].number == "Oneshot"
    assert get.call_args_list[1].args[1]["offset"] == 2
    assert get.call_args_list[0].args[1]["translatedLanguage[]"] == ["pt-br"]


def test_metadata_and_search_pagination():
    api = MangaDexAPI()
    row = {"id": TITLE, "attributes": {"title": {"en": "English", "pt-br": "Português"},
           "description": {"en": "Description"}, "tags": []},
           "relationships": [{"type": "cover_art", "attributes": {"fileName": "cover.jpg"}}]}
    with patch.object(api, "_get", return_value={"data": [row], "total": 21}):
        page = api.search_manga("test")
    assert page.has_next and page.last_page == 2
    assert page.items[0].title == "Português"
    assert page.items[0].canonical_url.endswith(TITLE)
    assert "uploads.mangadex.org" in page.items[0].poster_url


def test_author_defaults_are_requested_and_preserved():
    api = MangaDexAPI()
    row = {"id": TITLE, "attributes": {"title": {"en": "Title"}}, "relationships": [
        {"type": "author", "attributes": {"name": "Author One"}},
        {"type": "author", "attributes": {"name": "Author Two"}},
        {"type": "artist", "attributes": {"name": "Artist"}}]}
    with patch.object(api, "_get", return_value={"data": row}) as get:
        manga = api.get_manga_info(TITLE)
    assert "author" in get.call_args.args[1]["includes[]"]
    assert manga.author == "Author One, Author Two"


@pytest.mark.parametrize("compressed,route", [(False, "data"), (True, "data-saver")])
def test_at_home_page_order(compressed, route):
    api = MangaDexAPI(data_saver=compressed)
    with patch.object(api, "_get", return_value={"baseUrl": "https://example.org", "chapter": {
        "hash": "hash", "data": ["02.png", "01.png"], "dataSaver": ["02.jpg", "01.jpg"]}}):
        report = api.get_chapter_image_report("chapter")
    assert report.expected_image_count == 2
    assert f"/{route}/hash/02." in report.image_urls[0]
    assert report.page_numbers == [1, 2]


@pytest.mark.parametrize("format_type,suffix", [(OutputFormat.CBZ, ".cbz"), (OutputFormat.PDF, ".pdf"),
                                                 (OutputFormat.IMAGES, ".png")])
def test_mangadex_shared_download_pipeline(tmp_path, format_type, suffix):
    reset_downloads()
    config = DownloadConfig(download_path=str(tmp_path), output_format=format_type)
    manga = MangaInfo(title="Test", hash_id=TITLE, source="mangadex")
    chapter = Chapter("chapter-id", "1", language="pt-br", volume="2")
    stream = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(stream, "PNG")
    with patch.object(MangaDexAPI, "_get", return_value={"baseUrl": "https://example.org", "chapter": {
        "hash": "hash", "data": ["1.png"]}}), \
        patch("src.core.downloader.ImageDownloader.download_image", return_value=(1, stream.getvalue(), None)), \
        patch("src.api.comix.ComixAPI.get_chapter_image_report", side_effect=AssertionError("Wrong provider")):
        success, message = ChapterDownloader(config, manga).download_chapter(chapter)
    assert success, message
    assert list(tmp_path.rglob("*" + suffix))


def test_mangadex_fetch_worker_keeps_source_and_uuid():
    from gui.bridge.manga_bridge import FetchWorker
    from PyQt6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    worker = FetchWorker(f"https://mangadex.org/title/{TITLE}")
    manga_results, chapter_results = [], []
    worker.finished.connect(manga_results.append)
    worker.chaptersLoaded.connect(chapter_results.append)
    with patch.object(MangaDexAPI, "get_manga_info", return_value=MangaInfo(manga_id=TITLE, source="mangadex")), \
        patch.object(MangaDexAPI, "get_all_chapters", return_value=[Chapter(TITLE, "1", language="pt-br")]):
        worker.run()
    assert manga_results[0]["source"] == "mangadex"
    assert chapter_results[0][0]["chapter_id"] == TITLE
    assert chapter_results[0][0]["language"] == "pt-br"
