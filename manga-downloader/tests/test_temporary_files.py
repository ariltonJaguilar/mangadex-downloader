import errno
import io
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from src.api.mangadex import MangaDexAPI
from src.core.downloader import ChapterDownloader, cancel_downloads, reset_downloads
from src.core.models import Chapter, MangaInfo, DownloadConfig, OutputFormat
from src.formats.pdf import create_pdf
from src.utils.temporary import chapter_workspace, configure_temporary_root, publish_file
from src.utils.config import ConfigManager


def image_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (30, 40), "red").save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def runtime_temp_state(monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    for key in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(key, os.environ.get(key, tempfile.gettempdir()))


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_chapter_pages_and_conversion_use_selected_temp_and_clean_up(tmp_path, outcome):
    reset_downloads()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    sentinel = scratch / "user-file.txt"
    sentinel.write_text("Keep this")
    target = tmp_path / "downloads"
    cfg = DownloadConfig(download_path=str(target), temp_path=str(scratch), output_format=OutputFormat.PDF)
    inspected = []

    def convert(paths, output, *args, **kwargs):
        assert all(scratch in Path(path).parents for path in paths)
        assert scratch in Path(output).parents
        assert all(Path(path).exists() for path in paths)
        assert not target.exists()
        inspected.extend(paths)
        if outcome == "error":
            raise OSError("Conversion failed")
        result = create_pdf(paths, output, *args, **kwargs)
        if outcome == "cancel":
            cancel_downloads()
        return result

    with patch.object(MangaDexAPI, "_get", return_value={"baseUrl": "https://example.org", "chapter": {
            "hash": "hash", "data": ["1.png", "2.png"]}}), \
         patch("src.core.downloader.ImageDownloader.download_image", side_effect=lambda url, idx: (idx, image_bytes(), None)), \
         patch("src.core.downloader.create_pdf", side_effect=convert):
        success, message = ChapterDownloader(cfg, MangaInfo(source="mangadex", hash_id="id")).download_chapter(Chapter("a", "1"))
    assert success is (outcome == "success"), message
    assert len(inspected) == 2
    assert list(scratch.iterdir()) == [sentinel]
    assert bool(list(target.rglob("*.pdf"))) is (outcome == "success")
    reset_downloads()


def test_workspaces_are_isolated_and_leave_unrelated_files(tmp_path):
    with chapter_workspace(str(tmp_path)) as first:
        with chapter_workspace(str(tmp_path)) as second:
            assert first != second
            (first / "one").write_text("one")
            (second / "two").write_text("two")
        assert not second.exists()
        assert (first / "one").exists()
    assert not first.exists()


@pytest.mark.parametrize("fail_copy", [False, True])
def test_publish_across_drives_is_atomic_and_cleans_destination_partial(tmp_path, fail_copy):
    source = tmp_path / "new.pdf"
    destination = tmp_path / "old.pdf"
    source.write_bytes(b"new")
    destination.write_bytes(b"original")
    replace = os.replace

    def cross_device(src, dest):
        if Path(src) == source:
            raise OSError(errno.EXDEV, "Different volumes")
        replace(src, dest)

    with patch("src.utils.temporary.os.replace", side_effect=cross_device):
        if fail_copy:
            with patch("src.utils.temporary.shutil.copyfile", side_effect=OSError("disk full")):
                with pytest.raises(OSError):
                    publish_file(source, destination)
            assert destination.read_bytes() == b"original"
            assert source.exists()
        else:
            publish_file(source, destination)
            assert destination.read_bytes() == b"new"
            assert not source.exists()
    assert not list(tmp_path.glob("*.part"))


def test_temp_setting_native_picker_persists_and_configures_libraries(tmp_path, runtime_temp_state):
    from gui.bridge.settings_bridge import SettingsBridge
    config = ConfigManager(tmp_path / "config.json")
    bridge = SettingsBridge(config_manager=config)
    folder = tmp_path / "Temporary pages"
    with patch("gui.bridge.settings_bridge.QFileDialog.getExistingDirectory", return_value=str(folder)):
        bridge.chooseTemporaryFolder()
    assert bridge.tempPath == str(folder.resolve())
    assert ConfigManager(config.config_path).get_download_config().temp_path == str(folder.resolve())
    assert tempfile.gettempdir() == str(folder.resolve())
    assert os.environ["TEMP"] == str(folder.resolve())
    with tempfile.TemporaryDirectory(prefix="browser-test-") as browser_profile:
        assert Path(browser_profile).parent == folder
    with patch("gui.bridge.settings_bridge.QFileDialog.getExistingDirectory", return_value=""):
        bridge.chooseTemporaryFolder()
    assert bridge.tempPath == str(folder.resolve())


def test_unusable_temp_setting_does_not_replace_saved_preference(tmp_path, runtime_temp_state):
    from gui.bridge.settings_bridge import SettingsBridge
    config = ConfigManager(tmp_path / "config.json")
    bridge = SettingsBridge(config_manager=config)
    errors = []
    bridge.errorOccurred.connect(errors.append)
    file = tmp_path / "not-a-folder"
    file.write_text("file")
    bridge.setValue("temp_path", str(file))
    assert errors
    assert config.get("temp_path") == ""
