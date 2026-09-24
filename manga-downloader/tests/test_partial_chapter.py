from types import SimpleNamespace
from unittest.mock import Mock, patch
from zipfile import ZipFile

from src.api.comix import ChapterImageFetchReport
from src.core.downloader import ChapterDownloader, ImageDownloader, reset_downloads
from src.core.models import Chapter, DownloadConfig, MangaInfo, OutputFormat
from tests.test_books import image_data, data_url


def setup_download(tmp_path):
    reset_downloads()
    config = DownloadConfig(download_path=str(tmp_path / "out"), temp_path=str(tmp_path / "temp"),
                            output_format=OutputFormat.CBZ, keep_images=False)
    manga = MangaInfo(title="Partial", source="comix", hash_id="partial")
    chapter = Chapter("chapter", "1")
    urls = [data_url(image_data(color)) for color in ("red", "green", "blue")]
    partial = ChapterImageFetchReport([urls[0], urls[2]], 3, [], [2], [1, 3])
    return config, manga, chapter, urls, partial


def test_sparse_extraction_retries_only_missing_page_and_keeps_order(tmp_path):
    config, manga, chapter, urls, partial = setup_download(tmp_path)
    reader = SimpleNamespace(fetch=Mock(side_effect=[partial,
        ChapterImageFetchReport([urls[1]], 3, [], [], [2])]))
    downloader = ChapterDownloader(config, manga, reader_service=reader)
    progress = []
    with patch.object(downloader.image_downloader, "download_image",
                      wraps=downloader.image_downloader.download_image) as fetch:
        success, message = downloader.download_chapter(chapter,
            on_image_progress=lambda current, total: progress.append((current, total)))
    assert success, message
    assert sorted(call.args[1] for call in fetch.call_args_list) == [1, 2, 3]
    assert reader.fetch.call_args.kwargs == {"only_pages": [2]}
    assert (2, 3) in progress and progress[-1] == (3, 3)
    with ZipFile(next((tmp_path / "out").rglob("*.cbz"))) as archive:
        names = sorted(name for name in archive.namelist() if name.endswith(".png"))
        assert [archive.read(name) for name in names] == [image_data(color) for color in ("red", "green", "blue")]
    assert not list((tmp_path / "temp").rglob("*.page"))


def test_failed_chapter_retains_pages_even_when_keep_images_is_off(tmp_path):
    config, manga, chapter, urls, partial = setup_download(tmp_path)
    reader = SimpleNamespace(fetch=Mock(return_value=partial))
    first = ChapterDownloader(config, manga, reader_service=reader)
    success, _ = first.download_chapter(chapter)
    assert not success
    assert len(list((tmp_path / "temp").rglob("*.page"))) == 2
    assert not list((tmp_path / "out").rglob("*.cbz"))
    # A new downloader has no in-memory state; it must reuse the retained files.
    reader = SimpleNamespace(fetch=Mock(return_value=ChapterImageFetchReport(urls, 3, [], [], [1, 2, 3])))
    resumed = ChapterDownloader(config, manga, reader_service=reader)
    with patch.object(resumed.image_downloader, "download_image", wraps=resumed.image_downloader.download_image) as fetch:
        success, message = resumed.download_chapter(chapter)
    assert success, message
    assert [call.args[1] for call in fetch.call_args_list] == [2]
    assert reader.fetch.call_count == 1


def test_corrupt_retained_page_is_downloaded_again(tmp_path):
    config, manga, chapter, urls, partial = setup_download(tmp_path)
    first = ChapterDownloader(config, manga, reader_service=SimpleNamespace(fetch=Mock(return_value=partial)))
    first.download_chapter(chapter)
    next((tmp_path / "temp").rglob("000001.page")).write_bytes(b"broken")
    resumed = ChapterDownloader(config, manga, reader_service=SimpleNamespace(
        fetch=Mock(return_value=ChapterImageFetchReport(urls, 3, [], [], [1, 2, 3]))))
    with patch.object(resumed.image_downloader, "download_image", wraps=resumed.image_downloader.download_image) as fetch:
        success, message = resumed.download_chapter(chapter)
    assert success, message
    assert sorted(call.args[1] for call in fetch.call_args_list) == [1, 2]
