import base64
import io
import re
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile, ZIP_STORED
from xml.etree import ElementTree as ET

import pytest
from PIL import Image
from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer

from gui.bridge.download_bridge import DownloadBridge, DownloadWorker
from src.api.comix import ChapterImageFetchReport
from src.api.mangadex import MangaDexAPI
from src.core.book import book_filename, saved_chapter_pages
from src.core.downloader import reset_downloads
from src.core.models import DownloadConfig, OutputFormat
from src.utils.config import ConfigManager


def image_data(color, size=(20, 30)):
    data = io.BytesIO()
    Image.new("RGB", size, color).save(data, "PNG")
    return data.getvalue()


def data_url(data):
    return "data:image/png;base64," + base64.b64encode(data).decode()


def report(chapter_id, **kwargs):
    image = image_data("red" if chapter_id == "a" else "blue")
    return ChapterImageFetchReport([data_url(image)], 1, [], [], [1])


def manga():
    return {"title": "Meu Mangá & Amigos", "author": "Autora 日本", "source": "mangadex",
            "hash_id": "title", "combine_chapters": True, "selected_language": "pt-br",
            "poster_url": data_url(image_data("green", (40, 60)))}


def chapters():
    return [{"chapter_id": "a", "number": "1", "language": "pt-br", "pages_count": 1},
            {"chapter_id": "b", "number": "2", "language": "en", "pages_count": 1}]


@pytest.fixture
def app():
    return QCoreApplication.instance() or QCoreApplication([])


def pdf_string(data, field):
    raw = re.search(rb"/" + field + rb"\s*\(((?:\\.|[^\\)])*)\)", data).group(1)
    raw = re.sub(rb"\\([0-7]{1,3}|.)", lambda m: bytes([int(m[1], 8)])
                 if m[1].isdigit() else m[1], raw)
    return raw.decode("utf-16") if raw.startswith(b"\xfe\xff") else raw.decode("latin1")


@pytest.mark.parametrize("format_type", [OutputFormat.PDF, OutputFormat.EPUB])
def test_worker_creates_one_book_with_edited_metadata_cover_and_all_chapters(tmp_path, app, format_type):
    reset_downloads()
    config = DownloadConfig(download_path=str(tmp_path / "output"), temp_path=str(tmp_path / "scratch"),
                            output_format=format_type, combine_chapters=True, book_id=str(uuid4()))
    worker = DownloadWorker(manga(), chapters(), config)
    errors = []
    worker.error.connect(errors.append)
    with patch.object(MangaDexAPI, "get_chapter_image_report", side_effect=report):
        worker.run()
    assert not errors
    path = tmp_path / "output" / f"Meu Mangá & Amigos.{format_type.value}"
    assert list((tmp_path / "output").iterdir()) == [path]
    assert not list(tmp_path.rglob("*.part"))
    if format_type == OutputFormat.PDF:
        data = path.read_bytes()
        assert pdf_string(data, b"Title") == manga()["title"]
        assert pdf_string(data, b"Author") == manga()["author"]
        assert len(re.findall(rb"/Type\s*/Page\b", data)) == 3
        assert b"/Width 40" in data and b"/Height 60" in data
    else:
        with ZipFile(path) as archive:
            assert archive.testzip() is None
            assert archive.infolist()[0].filename == "mimetype"
            assert archive.infolist()[0].compress_type == ZIP_STORED
            assert archive.read("mimetype") == b"application/epub+zip"
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            rootfile = container.find(".//{*}rootfile").attrib["full-path"]
            package = ET.fromstring(archive.read(rootfile))
            ns = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
            assert package.find(".//dc:title", ns).text == manga()["title"]
            assert package.find(".//dc:creator", ns).text == manga()["author"]
            assert [node.text for node in package.findall(".//dc:language", ns)] == ["pt-br", "en"]
            manifest = {item.attrib["id"]: item.attrib for item in package.findall("opf:manifest/opf:item", ns)}
            assert manifest["img-cover"]["properties"] == "cover-image"
            assert archive.read("EPUB/" + manifest["img-cover"]["href"]) == image_data("green", (40, 60))
            spine = [item.attrib["idref"] for item in package.findall("opf:spine/opf:itemref", ns)]
            assert spine == ["cover", "chapter-1-page-1", "chapter-2-page-1"]
            for entry in manifest.values():
                assert "EPUB/" + entry["href"] in archive.namelist()
            for name in archive.namelist():
                if name.endswith((".xml", ".opf", ".xhtml")):
                    ET.fromstring(archive.read(name))
    assert not saved_chapter_pages(config, "a")  # Scratch pages cleaned after publication.


def wait_for_job(bridge, action):
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    bridge.downloadFinished.connect(loop.quit)
    action()
    timer.start(10000)
    loop.exec()
    assert not bridge.busy, "Download did not finish"
    timer.stop()


@pytest.mark.parametrize("source", ["mangadex", "comix"])
def test_resume_after_restart_keeps_completed_chapters_and_language_snapshot(tmp_path, app, source):
    config = ConfigManager(tmp_path / "config.json")
    config.set("download_path", str(tmp_path / "output"))
    config.set("temp_path", str(tmp_path / "scratch"))
    config.set("max_chapter_workers", 1)
    bridge = DownloadBridge(config_manager=config)

    def fail_second(identifier, *args, **kwargs):
        if identifier == "b":
            raise RuntimeError("Temporary chapter failure")
        return report(identifier)

    target = ("src.api.comix.ChapterReaderService.fetch" if source == "comix"
              else "src.api.mangadex.MangaDexAPI.get_chapter_image_report")
    metadata = dict(manga(), source=source)
    rows = chapters()
    if source == "comix":
        metadata["selected_language"] = "en"
        rows = [dict(row, language="en") for row in rows]
    with patch(target, side_effect=fail_second):
        wait_for_job(bridge, lambda: bridge.startDownload(metadata, rows, "epub", "Any"))
    job = bridge.jobs[0]
    assert job["status"] == "failed"
    assert (job["selected_downloaded"], job["fallback_downloaded"]) == (1, 0)
    assert not list(tmp_path.rglob("*.epub"))
    config.set("mangadex_language", "en")
    restored = DownloadBridge(config_manager=config)
    with patch(target, side_effect=lambda identifier, *args, **kwargs: report(identifier)) as fetch:
        wait_for_job(restored, lambda: restored.resumeJob(job["id"]))
    assert [call.args[0] for call in fetch.call_args_list] == ["b"]
    job = restored.jobs[0]
    assert job["status"] == "complete"
    assert job["selected_language"] == metadata["selected_language"]
    assert (job["selected_downloaded"], job["fallback_downloaded"]) == ((2, 0) if source == "comix" else (1, 1))
    assert len(list(tmp_path.rglob("*.epub"))) == 1


def test_failed_publication_keeps_pages_and_can_retry_without_download(tmp_path, app):
    config = ConfigManager(tmp_path / "config.json")
    config.set("download_path", str(tmp_path / "output"))
    config.set("temp_path", str(tmp_path / "scratch"))
    bridge = DownloadBridge(config_manager=config)
    with patch.object(MangaDexAPI, "get_chapter_image_report", side_effect=report), \
         patch("src.core.book.publish_file", side_effect=OSError("Disk unavailable")):
        wait_for_job(bridge, lambda: bridge.startDownload(manga(), chapters(), "pdf", "Any"))
    assert bridge.jobs[0]["status"] == "failed"
    assert bridge.jobs[0]["successful"] == 2
    assert "Disk unavailable" in bridge.jobs[0]["message"]
    with patch.object(MangaDexAPI, "get_chapter_image_report", side_effect=AssertionError("Already downloaded")):
        wait_for_job(bridge, lambda: bridge.resumeJob(bridge.jobs[0]["id"]))
    assert bridge.jobs[0]["status"] == "complete"
    assert len(list((tmp_path / "output").glob("*.pdf"))) == 1


def test_filename_preserves_valid_title_and_stays_within_output_folder():
    assert book_filename("Meu Mangá & Amigos (2026)") == "Meu Mangá & Amigos (2026)"
    assert "/" not in book_filename("../teste")
    assert "\\" not in book_filename("..\\teste")
    assert book_filename("CON") == "_CON"


def test_incomplete_extraction_reports_total_before_failure(tmp_path):
    from src.api.comix import ComixAPI
    from src.core.downloader import ChapterDownloader
    from src.core.models import Chapter, MangaInfo
    reset_downloads()
    config = DownloadConfig(download_path=str(tmp_path), temp_path=str(tmp_path / "scratch"))
    incomplete = ChapterImageFetchReport([data_url(image_data("red"))], 2, [], [2], [1])
    progress = []
    with patch.object(ComixAPI, "get_chapter_image_report", return_value=incomplete):
        success, message = ChapterDownloader(config, MangaInfo(title="Test", hash_id="test", source="comix")).download_chapter(
            Chapter("test-chapter", "3"), on_image_progress=lambda current, total: progress.append((current, total)))
    assert not success
    assert progress[0] == (0, 2)
    assert progress[-1] == (1, 2)
    assert "1/2 pages" in message
