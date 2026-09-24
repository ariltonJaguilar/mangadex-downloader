import base64
import importlib
import json
import os
import re
import tempfile
import unittest
import types
import zipfile
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import typer
from PIL import Image

from src.api.comix import ChapterImageFetchReport, ComixAPI
from src.core import downloader as downloader_module
from src.core.downloader import ChapterDownloader, ImageDownloader
from src.core.models import Chapter, DownloadConfig, MangaInfo, OutputFormat
from src.formats.cbz import create_cbz_from_bytes
from src.formats.pdf import create_pdf_from_bytes
from src.utils.config import ConfigManager


def image_bytes(color=(32, 64, 128), image_format="PNG"):
    buffer = BytesIO()
    Image.new("RGB", (48, 64), color).save(buffer, format=image_format)
    return buffer.getvalue()


def data_url(data, mime="image/png"):
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


@contextmanager
def temp_cwd(path: Path):
    old_cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old_cwd)


class FormatWriterTests(unittest.TestCase):
    def test_pdf_and_cbz_create_valid_outputs_from_bytes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            images = [
                (1, image_bytes((200, 20, 20), "PNG")),
                (2, image_bytes((20, 20, 200), "JPEG")),
            ]

            pdf_path = create_pdf_from_bytes(images, tmp / "chapter.pdf", "Chapter")
            self.assertTrue(pdf_path.exists())
            self.assertGreater(pdf_path.stat().st_size, 1000)
            self.assertTrue(pdf_path.read_bytes().startswith(b"%PDF"))
            self.assertGreaterEqual(len(re.findall(rb"/Type\s*/Page\b", pdf_path.read_bytes())), 1)

            cbz_path = create_cbz_from_bytes(images, tmp / "chapter.cbz")
            with zipfile.ZipFile(cbz_path) as cbz:
                self.assertIsNone(cbz.testzip())
                self.assertEqual(cbz.namelist(), ["001.png", "002.jpg"])

    def test_writers_reject_invalid_images_without_final_or_part_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            bad_images = [(1, image_bytes()), (2, b"<html>challenge</html>")]

            for creator, suffix in (
                (create_pdf_from_bytes, "pdf"),
                (create_cbz_from_bytes, "cbz"),
            ):
                output = tmp / f"bad.{suffix}"
                with self.assertRaises(Exception):
                    creator(bad_images, output)

                self.assertFalse(output.exists())
                self.assertFalse(output.with_name(f"{output.name}.part").exists())

    def test_empty_inputs_raise_without_creating_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            pdf = tmp / "empty.pdf"
            cbz = tmp / "empty.cbz"

            with self.assertRaises(ValueError):
                create_pdf_from_bytes([], pdf)
            with self.assertRaises(ValueError):
                create_cbz_from_bytes([], cbz)

            self.assertFalse(pdf.exists())
            self.assertFalse(cbz.exists())


class ChapterDownloaderTests(unittest.TestCase):
    def setUp(self):
        self._original_report = ComixAPI.get_chapter_image_report

    def tearDown(self):
        ComixAPI.get_chapter_image_report = self._original_report

    def _download(self, tmpdir, output_format):
        manga = MangaInfo(hash_id="abc", slug="abc-title", title="Downloader Manga")
        chapter = Chapter(chapter_id=10, number="1", title="Start")
        config = DownloadConfig(
            output_format=output_format,
            download_path=str(tmpdir),
            max_image_workers=2,
            max_chapter_workers=1,
        )
        return ChapterDownloader(config, manga).download_chapter(chapter), manga, chapter

    def test_pdf_and_cbz_fail_on_partial_page_download_without_artifact(self):
        good = data_url(image_bytes())
        bad = "data:image/png;base64,not-valid-base64"
        ComixAPI.get_chapter_image_report = staticmethod(
            lambda *args, **kwargs: ChapterImageFetchReport([good, bad], page_count=2)
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            for output_format in (OutputFormat.PDF, OutputFormat.CBZ):
                root = Path(tmpdir) / output_format.value
                (success, message), manga, chapter = self._download(root, output_format)
                artifact = (
                    root
                    / manga.get_safe_title()
                    / f"{chapter.get_safe_folder_name()}.{output_format.value}"
                )

                self.assertFalse(success)
                self.assertIn("Incomplete download", message)
                self.assertFalse(artifact.exists())

    def test_failed_pages_are_recovered_from_refreshed_sources(self):
        good = data_url(image_bytes((10, 20, 30)))
        bad = "data:image/png;base64,not-valid-base64"
        reports = iter([
            ChapterImageFetchReport([good, bad], page_count=2),
            ChapterImageFetchReport([good, good], page_count=2),
        ])
        ComixAPI.get_chapter_image_report = staticmethod(lambda *args, **kwargs: next(reports))

        with tempfile.TemporaryDirectory() as tmpdir:
            config = DownloadConfig(
                output_format=OutputFormat.IMAGES,
                download_path=tmpdir,
                max_image_workers=2,
            )
            manga = MangaInfo(hash_id="abc", slug="abc-title", title="Recovery Manga")
            chapter = Chapter(chapter_id=11, number="1")
            success, message = ChapterDownloader(config, manga).download_chapter(chapter)

            self.assertTrue(success, message)
            files = list((Path(tmpdir) / "Recovery Manga" / "Chapter_1").glob("*"))
            self.assertEqual(len(files), 2)

    def test_tls_eof_retries_with_a_reset_session(self):
        class RequestException(Exception):
            pass

        class SSLError(RequestException):
            pass

        class ConnectionError(RequestException):
            pass

        class Timeout(RequestException):
            pass

        class HTTPError(RequestException):
            pass

        fake_requests = types.SimpleNamespace(
            exceptions=types.SimpleNamespace(
                RequestException=RequestException,
                SSLError=SSLError,
                ConnectionError=ConnectionError,
                Timeout=Timeout,
                HTTPError=HTTPError,
            )
        )

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def raise_for_status(self): return None
            def iter_content(self, chunk_size=8192): yield image_bytes()

        class Session:
            def __init__(self): self.calls = 0; self.resets = 0
            def get(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise SSLError("UNEXPECTED_EOF_WHILE_READING")
                return Response()
            def reset(self): self.resets += 1

        session = Session()
        config = DownloadConfig(max_image_workers=1, retry_count=1, retry_delay=0)
        with patch.object(downloader_module, "requests", fake_requests), \
                patch.object(downloader_module, "get_session", return_value=session):
            index, data, error = ImageDownloader(config).download_image("https://cdn.example/page", 1)

        self.assertEqual(index, 1)
        self.assertIsNone(error)
        self.assertIsNotNone(data)
        self.assertEqual(session.calls, 2)
        self.assertEqual(session.resets, 1)

    def test_cbz_success_includes_metadata(self):
        urls = [
            data_url(image_bytes((10, 20, 30))),
            data_url(image_bytes((30, 20, 10), "JPEG"), "image/jpeg"),
        ]
        ComixAPI.get_chapter_image_report = staticmethod(
            lambda *args, **kwargs: ChapterImageFetchReport(urls, page_count=2)
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            manga = MangaInfo(
                hash_id="abc",
                slug="abc-title",
                title="Metadata Manga",
                genres=["Action"],
                original_language="ja",
            )
            chapter = Chapter(chapter_id=10, number="2", title="Meta", volume="1")
            config = DownloadConfig(output_format=OutputFormat.CBZ, download_path=tmpdir)
            success, message = ChapterDownloader(config, manga).download_chapter(chapter)

            self.assertTrue(success, message)
            cbz_path = Path(tmpdir) / manga.get_safe_title() / f"{chapter.get_safe_folder_name()}.cbz"
            with zipfile.ZipFile(cbz_path) as cbz:
                comic_info = cbz.read("ComicInfo.xml").decode("utf-8")

            self.assertIn("<LanguageISO>ja</LanguageISO>", comic_info)
            self.assertIn("<Genre>Action</Genre>", comic_info)
            self.assertIn("<Volume>1</Volume>", comic_info)


class CliConfigTests(unittest.TestCase):
    def test_invalid_direct_cli_format_does_not_persist_to_config(self):
        cli_app = importlib.import_module("src.cli.app")

        with tempfile.TemporaryDirectory() as tmpdir, temp_cwd(Path(tmpdir)):
            with self.assertRaises(typer.Exit):
                cli_app.download(
                    url="https://comix.to/title/fake-title",
                    chapters="all",
                    format="badformat",
                    output=None,
                    headless=True,
                )

            config_path = Path("config.json")
            if config_path.exists():
                self.assertNotEqual(json.loads(config_path.read_text())["output_format"], "badformat")

    def test_direct_cli_overrides_are_one_shot(self):
        cli_app = importlib.import_module("src.cli.app")

        manga = MangaInfo(hash_id="abc", slug="abc-title", title="CLI Manga")
        chapters = [Chapter(chapter_id=1, number="1")]

        class FakeDownloader:
            seen_config = None

            def __init__(self, config):
                FakeDownloader.seen_config = config

            def download_chapters(self, manga, selected, progress):
                return 1, 0

        with tempfile.TemporaryDirectory() as tmpdir, temp_cwd(Path(tmpdir)):
            with patch.object(cli_app.ComixAPI, "get_manga_info", return_value=manga), \
                 patch.object(cli_app.ComixAPI, "get_all_chapters", return_value=chapters), \
                 patch.object(cli_app, "MangaDownloader", FakeDownloader):
                cli_app.download(
                    url="https://comix.to/title/abc-title",
                    chapters="all",
                    format="cbz",
                    output="one-shot-output",
                    headless=False,
                )

            self.assertEqual(FakeDownloader.seen_config.output_format, OutputFormat.CBZ)
            self.assertEqual(FakeDownloader.seen_config.download_path, "one-shot-output")
            self.assertFalse(FakeDownloader.seen_config.headless)

            persisted = ConfigManager("config.json").get_download_config()
            self.assertEqual(persisted.output_format, OutputFormat.IMAGES)
            self.assertEqual(persisted.download_path, "downloads")
            self.assertTrue(persisted.headless)


class GuiBridgeTests(unittest.TestCase):
    def test_gui_bridge_cbz_preserves_metadata_and_rejects_bad_format(self):
        try:
            from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer
            from gui.bridge.download_bridge import DownloadBridge
        except ImportError:
            self.skipTest("PyQt6 is not installed")

        app = QCoreApplication.instance() or QCoreApplication([])

        with tempfile.TemporaryDirectory() as tmpdir:
            from src.utils.config import ConfigManager
            bridge = DownloadBridge(config_manager=ConfigManager(Path(tmpdir) / "config.json"))

            class FakeConfigManager:
                def get_download_config(self):
                    return DownloadConfig(download_path=tmpdir, max_chapter_workers=1, max_image_workers=1)

            bridge._config_manager = FakeConfigManager()

            errors = []
            bridge.errorOccurred.connect(errors.append)
            bridge.startDownload({}, [{"chapter_id": 1, "number": "1"}], "badformat", "Any")
            self.assertIn("Invalid format", errors[-1])

            original_report = ComixAPI.get_chapter_image_report
            try:
                ComixAPI.get_chapter_image_report = staticmethod(
                    lambda *args, **kwargs: ChapterImageFetchReport([data_url(image_bytes())], page_count=1)
                )

                finished = []
                loop = QEventLoop()
                bridge.downloadFinished.connect(lambda successful, failed: (finished.append((successful, failed)), loop.quit()))
                QTimer.singleShot(10000, loop.quit)
                bridge.startDownload(
                    {
                        "manga_id": 1,
                        "hash_id": "abc",
                        "slug": "abc-title",
                        "title": "GUI Metadata Manga",
                        "alt_titles": [],
                        "manga_type": "manga",
                        "status": "ongoing",
                        "original_language": "ja",
                        "genres": ["Drama"],
                        "description": "",
                    },
                    [{"chapter_id": 1, "number": "1", "title": "One", "volume": "3", "group_name": "Team"}],
                    "cbz",
                    "Any",
                )
                loop.exec()
            finally:
                ComixAPI.get_chapter_image_report = original_report

            self.assertEqual(finished, [(1, 0)])
            cbz_path = Path(tmpdir) / "GUI Metadata Manga" / "Chapter_1_One.cbz"
            with zipfile.ZipFile(cbz_path) as cbz:
                comic_info = cbz.read("ComicInfo.xml").decode("utf-8")

            self.assertIn("<LanguageISO>ja</LanguageISO>", comic_info)
            self.assertIn("<Genre>Drama</Genre>", comic_info)
            self.assertIn("<Volume>3</Volume>", comic_info)


if __name__ == "__main__":
    unittest.main()
