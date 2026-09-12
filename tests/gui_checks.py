"""Run with python -m unittest discover -s tests -p gui_checks.py."""

import subprocess
import sys
import tempfile
import time
import tkinter as tk
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mangadex_downloader.gui import DownloaderWindow, build_arguments
from mangadex_downloader.format.comic_book import generate_Comicinfo
from mangadex_downloader.format.epub import EpubPlugin
from mangadex_downloader.format.pdf import PDFFile
from mangadex_downloader.format.pdf import MAX_WEBCOMIC_PIXELS
from mangadex_downloader.format.base import ConvertedSingleFormat
from mangadex_downloader.main import merge_fallback_chapters
from mangadex_downloader.downloader import FileDownloader
from mangadex_downloader.network import requestsMangaDexSession


URL = "https://mangadex.org/title/00000000-0000-0000-0000-000000000001/example"


class ArgumentChecks(unittest.TestCase):
    def test_decimal_range_and_path_with_spaces(self):
        args = build_arguments(URL, "folder with spaces", "pt-br", "cbz", "1,5", "3", True)
        self.assertEqual(args[args.index("--start-chapter") + 1], "1.5")
        self.assertIn("folder with spaces", args[args.index("--path") + 1])
        self.assertIn("--use-compressed-image", args)
        self.assertEqual(args[args.index("--filename-single") + 1], "{manga.title}{file_ext}")

    def test_editable_metadata_arguments(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg") as cover:
            args = build_arguments(
                URL, "downloads", "pt-br", "epub-single",
                title="Meu título", author="Autora A, Autor B", custom_cover=cover.name,
            )
        self.assertEqual(args[args.index("--override-title") + 1], "Meu título")
        self.assertEqual(args[args.index("--override-author") + 1], "Autora A, Autor B")
        self.assertIn("--custom-cover", args)

    def test_blank_range_downloads_all_and_enables_fallback(self):
        args = build_arguments(
            URL, "downloads", "pt-br", "cbz-single", fallback_english=True
        )
        self.assertNotIn("--start-chapter", args)
        self.assertNotIn("--end-chapter", args)
        self.assertIn("--fallback-english", args)

    def test_pdf_ipad_options(self):
        args = build_arguments(
            URL, "downloads", "pt-br", "pdf-single",
            pdf_page_width="2048", pdf_layout="webcomic",
        )
        self.assertEqual(args[args.index("--pdf-page-width") + 1], "2048")
        self.assertEqual(args[args.index("--pdf-layout") + 1], "webcomic")
        with self.assertRaises(ValueError):
            build_arguments(URL, "downloads", "pt-br", "pdf-single", pdf_page_width="200")

    def test_reject_invalid_input(self):
        for changes in ({"url": "https://example.com/title/123"},
                        {"url": "https://mangadex.org/title/invalid"},
                        {"destination": " "}, {"start": "nan"},
                        {"end": "inf"}, {"start": "-1"},
                        {"start": "3", "end": "2"}):
            values = dict(url=URL, destination="downloads", language="pt-br", fmt="cbz")
            values.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_arguments(**values)

    def test_arguments_match_real_cli(self):
        from mangadex_downloader.cli.args_parser import get_args

        _, args = get_args(build_arguments(URL, "downloads", "pt-br", "cbz-single", "1", "2", True))
        self.assertEqual(args.language.value, "pt-br")
        self.assertEqual(args.save_as, "cbz-single")
        self.assertEqual(args.start_chapter, 1)
        self.assertTrue(args.use_compressed_image)

    def test_downloader_uses_buffered_64kb_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            downloader = FileDownloader("https://example.com/page", Path(directory) / "page.jpg")
        self.assertEqual(downloader.chunk_size, 65536)


class MetadataChecks(unittest.TestCase):
    def setUp(self):
        language = SimpleNamespace(value="pt-br")
        self.manga = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000001",
            title="Título do Mangá", authors=["Autora A", "Autor B"], artists=[],
            genres=[], tags=[], description="Descrição", alternative_titles=[],
            chapters=SimpleNamespace(language=language),
        )

    def test_cbz_author_and_title_metadata(self):
        root = generate_Comicinfo(self.manga, total_pages=10, has_cover=True)
        self.assertEqual(root.findtext("Series"), "Título do Mangá")
        self.assertEqual(root.findtext("Writer"), "Autora A, Autor B")
        self.assertEqual(root.find("Pages/Page").attrib["Type"], "FrontCover")

    def test_epub_author_title_and_cover_metadata(self):
        epub = EpubPlugin(self.manga, "pt-br", has_cover=True)
        metadata = str(epub._opf_root)
        self.assertIn("Título do Mangá", metadata)
        self.assertIn("Autora A, Autor B", metadata)
        self.assertIn('name="cover"', metadata)

    def test_pdf_author_and_title_metadata(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.jpg"
            pdf_path = Path(directory) / "result.pdf"
            Image.new("RGB", (10, 10), "white").save(image_path)
            formatter = object.__new__(PDFFile)
            formatter.manga = self.manga
            formatter.config = SimpleNamespace(pdf_page_width=1600)
            formatter.convert([image_path], pdf_path)
            contents = pdf_path.read_bytes()
        self.assertIn(b"/Author (\xfe\xff" + "Autora A, Autor B".encode("utf-16-be"), contents)
        self.assertIn(b"/Title (\xfe\xff" + "Título do Mangá".encode("utf-16-be"), contents)

    def test_webcomic_images_are_joined_at_standard_width(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            first = directory / "first.png"
            second = directory / "second.png"
            Image.new("RGB", (50, 30), "red").save(first)
            Image.new("RGB", (50, 40), "blue").save(second)
            formatter = object.__new__(PDFFile)
            formatter.config = SimpleNamespace(pdf_page_width=100)
            formatter.effective_page_width = 100
            strips = formatter.create_webcomic_strips([first, second], directory, 1)
            with Image.open(strips[0]) as strip:
                self.assertEqual(strip.size, (100, 140))

    def test_webcomic_strip_stays_below_safe_pixel_limit(self):
        width = 4000
        height = PDFFile.get_webcomic_max_height(width)
        self.assertLessEqual(width * height, MAX_WEBCOMIC_PIXELS)
        self.assertEqual(height, 6250)

    def test_automatic_width_ignores_and_shrinks_larger_cover(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            cover = directory / "cover.png"
            small_page = directory / "small.png"
            largest_page = directory / "largest.png"
            Image.new("RGB", (500, 700), "green").save(cover)
            Image.new("RGB", (100, 180), "white").save(small_page)
            Image.new("RGB", (250, 400), "white").save(largest_page)
            formatter = object.__new__(PDFFile)
            formatter.config = SimpleNamespace(pdf_page_width=0)
            formatter.effective_page_width = formatter.find_largest_width(
                [small_page, largest_page]
            )
            self.assertEqual(formatter.effective_page_width, 250)
            formatter.manga = self.manga
            pdf_path = directory / "automatic.pdf"
            formatter.convert([cover, small_page, largest_page], pdf_path)
            pdf_data = pdf_path.read_bytes()
            self.assertIn(b"/MediaBox [ 0 0 250.0 350.0 ]", pdf_data)
            self.assertIn(b"/MediaBox [ 0 0 250.0 450.0 ]", pdf_data)

    def test_english_fallback_only_adds_missing_chapter_numbers(self):
        chapter = lambda number, language: SimpleNamespace(chapter=number, language=language)
        primary = SimpleNamespace(chapters=[chapter("1", "pt-br"), chapter("3", "pt-br")])
        english = SimpleNamespace(chapters=[chapter("1", "en"), chapter("2", "en"), chapter("3", "en")])
        added = merge_fallback_chapters(primary, english)
        self.assertEqual(added, 1)
        self.assertEqual([item.language for item in primary.chapters], ["pt-br", "pt-br", "en"])


class TrackerRegressionChecks(unittest.TestCase):
    def test_changed_single_filename_is_treated_as_new_download(self):
        tracker = SimpleNamespace(disabled=False, empty=False, get=Mock(return_value=None))
        formatter = object.__new__(ConvertedSingleFormat)
        formatter.manga = SimpleNamespace(title="Novo nome", tracker=tracker)
        formatter.file_ext = ".pdf"
        formatter.replace = False
        formatter.get_fmt_single_cache = Mock(return_value=([("chapter", "images")], 1))
        formatter.create_worker = Mock()
        formatter.create_placeholder_obj_for_single_fmt = Mock()
        formatter.download_single = Mock()
        formatter.cleanup = Mock()
        with patch("mangadex_downloader.format.base.get_filename", return_value="Novo nome.pdf"):
            formatter.main()
        formatter.download_single.assert_called_once()


class NetworkReportChecks(unittest.TestCase):
    def test_optional_report_failure_disables_more_attempts(self):
        session = object.__new__(requestsMangaDexSession)
        session._report_available = True
        session.api_headers = {"User-Agent": "test"}
        response = SimpleNamespace(status_code=522)
        with patch("mangadex_downloader.network.requests.post", return_value=response) as post:
            session._report({"url": "test"})
            session._report({"url": "test-again"})
        self.assertFalse(session._report_available)
        post.assert_called_once()


class WindowChecks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.settings_path = Path(self.temp_dir.name) / "settings.json"
        self.app = DownloaderWindow(self.root, self.settings_path)
        self.app.url.set(URL)

    def tearDown(self):
        if self.app.process is not None:
            self.app.cancel()
            self.wait_until(lambda: self.app.process is None)
        for callback in self.root.tk.call("after", "info"):
            self.root.after_cancel(callback)
        self.root.destroy()
        self.temp_dir.cleanup()

    def wait_until(self, condition):
        deadline = time.monotonic() + 8
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(condition())

    def launch_fake_downloader(self, code):
        popen = subprocess.Popen

        def launch(command, **kwargs):
            self.assertIn("--language", command)
            self.assertEqual(command[command.index("--save-as") + 1], "cbz-single")
            return popen([sys.executable, "-u", "-c", code], **kwargs)

        with patch("mangadex_downloader.gui.subprocess.Popen", side_effect=launch):
            self.app.download()

    def test_settings_are_saved_and_loaded(self):
        self.app.destination.set("G:/Mangas")
        self.app.language.set("Inglês")
        self.app.fmt.set("EPUB — arquivo único")
        self.app.start.set("4")
        self.app.end.set("13")
        self.app.compressed.set(True)
        self.app.title.set("Título editado")
        self.app.author.set("Autora editada")
        self.app.custom_cover.set("G:/capa.jpg")
        self.app.pdf_layout.set("Tiras verticais (webcomic)")
        self.app.save_settings()
        self.app.destination.set("alterado")
        self.app.load_settings()
        self.assertEqual(self.app.destination.get(), "G:/Mangas")
        self.assertEqual(self.app.fmt.get(), "EPUB — arquivo único")
        self.assertEqual(self.app.end.get(), "13")
        self.assertTrue(self.app.compressed.get())
        self.assertEqual(self.app.title.get(), "Título editado")
        self.assertEqual(self.app.author.get(), "Autora editada")
        self.assertEqual(self.app.custom_cover.get(), "G:/capa.jpg")
        self.assertEqual(self.app.pdf_layout.get(), "Tiras verticais (webcomic)")

    def test_form_has_vertical_scroll_region(self):
        self.root.geometry("620x480")
        self.root.update_idletasks()
        region = tuple(map(float, self.app.canvas.cget("scrollregion").split()))
        self.assertGreater(region[3], self.app.canvas.winfo_height())
        with patch.object(self.app.canvas, "yview_scroll") as scroll:
            result = self.app._scroll_with_mouse(SimpleNamespace(widget=self.root, delta=-120))
        scroll.assert_called_once_with(1, "units")
        self.assertEqual(result, "break")

    def test_split_progress_message_updates_percentage_and_chapter(self):
        first = self.app._consume_progress_output("texto\n@@MANGADEX_GUI_PRO")
        second = self.app._consume_progress_output(
            'GRESS@@{"current": 2, "total": 10, "chapter": "Chapter 3"}\ncontinua\n'
        )
        self.assertEqual(first, "texto\n")
        self.assertEqual(second, "continua\n")
        self.assertEqual(float(self.app.progress["value"]), 2)
        self.assertIn("20%", self.app.progress_detail.get())
        self.assertIn("Chapter 3", self.app.progress_detail.get())

    def test_interactive_output_and_success(self):
        self.launch_fake_downloader("print('Escolha:', end='', flush=True); print(input())")
        self.wait_until(lambda: "Escolha:" in self.app.log.get("1.0", "end"))
        self.app.reply.insert(0, "1")
        self.app.send_reply()
        self.wait_until(lambda: self.app.process is None)
        self.assertEqual(self.app.status.get(), "Download concluído")

    def test_failure(self):
        self.launch_fake_downloader("import sys; print('erro de teste'); sys.exit(2)")
        self.wait_until(lambda: self.app.process is None)
        self.assertIn("Falha", self.app.status.get())
        self.assertIn("erro de teste", self.app.log.get("1.0", "end"))

    def test_cancel(self):
        self.launch_fake_downloader("import time; time.sleep(60)")
        self.app.cancel()
        self.wait_until(lambda: self.app.process is None)
        self.assertEqual(self.app.status.get(), "Download cancelado")


if __name__ == "__main__":
    unittest.main()
