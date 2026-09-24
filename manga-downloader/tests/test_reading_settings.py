from io import BytesIO
from unittest.mock import patch

from PIL import Image
import pytest

from src.api.mangadex import MangaDexAPI
from src.core.models import DownloadConfig, MangaInfo, Chapter, OutputFormat
from src.core.downloader import ChapterDownloader, reset_downloads
from src.formats.pdf import _pdf_pages, create_pdf_from_bytes
from src.utils.config import ConfigManager


def test_webtoon_joins_panels_in_order_and_normalizes_width():
    images = [(1, Image.new("RGB", (10, 20), "red")),
              (2, Image.new("RGB", (20, 20), "blue"))]
    pages = _pdf_pages(images, "webcomic", 0)
    image, width, height = next(pages)
    assert (width, height) == (20, 60)
    assert image.getpixel((0, 39)) == (255, 0, 0)
    assert image.getpixel((0, 40)) == (0, 0, 255)
    pages.close()


def test_webtoon_splits_long_panels_without_losing_pixels():
    image = Image.new("RGB", (10, 190), "red")
    image.putpixel((0, 189), (0, 0, 255))
    heights = []
    for strip, width, height in _pdf_pages([(1, image)], "webcomic", 0):
        heights.append(height)
        if len(heights) == 3:
            assert strip.getpixel((0, 29)) == (0, 0, 255)
    assert heights == [80, 80, 30]


def test_manga_pages_remain_separate_with_uniform_width():
    images = [(1, Image.new("RGB", (10, 20))), (2, Image.new("RGB", (20, 30)))]
    pages = list(_pdf_pages(images, "pages", 0))
    assert [(w, h) for _, w, h in pages] == [(20, 40), (20, 30)]


def test_native_folder_dialog_saves_only_confirmed_selection(tmp_path):
    from gui.bridge.settings_bridge import SettingsBridge
    config = ConfigManager(tmp_path / "config.json")
    bridge = SettingsBridge(config_manager=config)
    chosen = tmp_path / "Minha pasta"
    chosen.mkdir()
    with patch("gui.bridge.settings_bridge.QFileDialog.getExistingDirectory", return_value=str(chosen)) as dialog:
        bridge.chooseDownloadFolder()
    assert bridge.downloadPath == str(chosen)
    assert ConfigManager(config.config_path).get_download_config().download_path == str(chosen)
    assert dialog.call_args.args[0] is None
    with patch("gui.bridge.settings_bridge.QFileDialog.getExistingDirectory", return_value=""):
        bridge.chooseDownloadFolder()
    assert bridge.downloadPath == str(chosen)


def test_settings_validation_persistence_and_reset(tmp_path):
    from gui.bridge.settings_bridge import SettingsBridge
    config = ConfigManager(tmp_path / "config.json")
    bridge = SettingsBridge(config_manager=config)
    errors = []
    bridge.errorOccurred.connect(errors.append)
    for key, value in {"pdf_layout": "webcomic", "pdf_page_width": 1200,
                       "use_compressed_image": True, "fallback_english": True}.items():
        bridge.setValue(key, value)
    bridge.setValue("pdf_page_width", 100)
    bridge.setValue("pdf_layout", "invalid")
    assert len(errors) == 2
    reloaded = ConfigManager(config.config_path).get_download_config()
    assert reloaded.pdf_page_width == 1200 and reloaded.pdf_layout == "webcomic"
    assert reloaded.use_compressed_image and reloaded.fallback_english
    bridge.resetToDefaults()
    assert bridge.options["pdf_layout"] == "pages"
    assert bridge.options["pdf_page_width"] == 0
    assert bridge.options["fallback_english"] is False


def test_english_fallback_respects_volume_and_preferred_translation():
    def row(identifier, volume, number, language):
        return {"id": identifier, "attributes": {"volume": volume, "chapter": number,
                "pages": 1, "translatedLanguage": language}, "relationships": []}
    rows = [row("br", "1", "1", "pt-br"), row("en-duplicate", "1", "1", "en"),
            row("en-missing", "1", "2", "en"), row("en-volume2", "2", "1", "en")]
    api = MangaDexAPI(fallback_english=True)
    with patch.object(api, "_get", side_effect=[{"data": rows[:2], "total": 4},
                                               {"data": rows[2:], "total": 4}]) as get:
        result = api.get_all_chapters("manga")
    assert [c.chapter_id for c in result] == ["br", "en-missing", "en-volume2"]
    assert get.call_args.args[1]["translatedLanguage[]"] == ["pt-br", "en"]


@pytest.mark.parametrize("keep_images", [True, False])
def test_download_uses_compressed_sources_and_webtoon_pdf(tmp_path, keep_images):
    reset_downloads()
    image = BytesIO()
    Image.new("RGB", (20, 40), "red").save(image, "PNG")
    config = DownloadConfig(download_path=str(tmp_path), temp_path=str(tmp_path / "scratch"), output_format=OutputFormat.PDF,
                            keep_images=keep_images, use_compressed_image=True,
                            pdf_layout="webcomic", pdf_page_width=600)
    manga = MangaInfo(source="mangadex", hash_id="test", title="Test")
    seen = []

    def download(url, index):
        seen.append(url)
        return index, image.getvalue(), None

    with patch.object(MangaDexAPI, "_get", return_value={"baseUrl": "https://example.org", "chapter": {
         "hash": "hash", "data": ["original.png"], "dataSaver": ["small.png"]}}), \
         patch("src.core.downloader.ImageDownloader.download_image", side_effect=download):
        success, message = ChapterDownloader(config, manga).download_chapter(Chapter("ch", "1"))
    assert success, message
    assert seen == ["https://example.org/data-saver/hash/small.png"]
    assert len(list(tmp_path.rglob("*.pdf"))) == 1
    assert bool(list(tmp_path.rglob("*.png"))) is keep_images
