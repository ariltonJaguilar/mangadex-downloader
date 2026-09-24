"""Load the complete window in a fresh Qt process, without network requests."""
import subprocess
import sys
from pathlib import Path


def test_complete_application_loads():
    script = '''
import tempfile
from pathlib import Path
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication
from PyQt6.QtQml import QQmlApplicationEngine
from PyQt6.QtCore import QUrl
from gui.bridge import MangaBridge, DiscoveryBridge, DownloadBridge, SettingsBridge
from src.utils.config import ConfigManager
from gui.bridge.history_bridge import HistoryBridge
from PyQt6.QtTest import QTest
from PyQt6.QtCore import qInstallMessageHandler
messages = []
qInstallMessageHandler(lambda kind, context, message: messages.append(message))
app = QApplication([])
with tempfile.TemporaryDirectory() as tmp:
    config = ConfigManager(Path(tmp) / "config.json")
    engine = QQmlApplicationEngine()
    bridges = [MangaBridge(config_manager=config), DiscoveryBridge(config_manager=config),
               DownloadBridge(config_manager=config), SettingsBridge(config_manager=config)]
    for name, bridge in zip(["MangaBridge", "DiscoveryBridge", "DownloadBridge", "SettingsBridge"], bridges):
        engine.rootContext().setContextProperty(name, bridge)
    history = HistoryBridge(config)
    history.remember("Example", "mangadex", "")
    engine.rootContext().setContextProperty("HistoryBridge", history)
    with patch.object(DiscoveryBridge, "_enqueue"):
        engine.load(QUrl.fromLocalFile(str(Path("gui/qml/main.qml").resolve())))
    assert engine.rootObjects(), messages
    window = engine.rootObjects()[0]
    assert window.title() == "Manga Downloader"
    from PyQt6.QtCore import QObject
    assert window.findChild(QObject, "platformSelector") is not None
    assert window.findChild(QObject, "languageSelector") is not None
    window.requestActivate()
    QTest.qWait(350)
    from PyQt6.QtCore import QPointF, Qt
    field = window.findChild(QObject, "inputField")
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=field.mapToScene(QPointF(10, 10)).toPoint())
    QTest.qWait(50)
    search_input = window.findChild(QObject, "searchInput")
    assert window.findChild(QObject, "searchHistoryPopup").property("visible"), (messages, search_input.property("historyItems"), search_input.property("isLoading"), field.property("activeFocus"), field.mapToScene(QPointF(10, 10)))
    popup = window.findChild(QObject, "searchHistoryPopup")
    field.setProperty("text", "Keep this search")
    field.selectAll()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=QPointF(window.width() - 20, window.height() - 20).toPoint())
    QTest.qWait(30)
    assert not popup.property("visible")
    assert not field.property("focus")
    assert not field.property("activeFocus")
    assert field.property("selectedText") == ""
    assert field.property("text") == "Keep this search"
    # Activate a second window and return, reproducing an Alt+Tab round trip.
    from PyQt6.QtQuick import QQuickWindow
    other_window = QQuickWindow()
    other_window.show()
    other_window.requestActivate()
    QTest.qWait(30)
    assert other_window.isActive()
    window.requestActivate()
    QTest.qWait(30)
    assert window.isActive()
    assert not popup.property("visible")
    assert not field.property("activeFocus")
    other_window.close()
    # A deliberate click still opens history; Escape also clears selection/focus.
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=field.mapToScene(QPointF(10, 10)).toPoint())
    QTest.qWait(30)
    assert popup.property("visible")
    QTest.keyClick(window, Qt.Key.Key_Escape)
    QTest.qWait(30)
    assert not popup.property("visible")
    assert not field.property("activeFocus")
    # The outside-press observer must not block a history entry's click.
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=field.mapToScene(QPointF(10, 10)).toPoint())
    QTest.qWait(30)
    assert popup.property("visible")
    history_content = popup.property("contentItem")
    with patch.object(bridges[1], "_enqueue") as search:
        QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                         pos=history_content.mapToScene(QPointF(20, 20)).toPoint())
        QTest.qWait(30)
        search.assert_called_once_with("search", "Example", 1)
    assert not popup.property("visible")
    queue = bridges[2]
    with patch.object(queue, "startPending"):
        for title in ("One", "Two"):
            queue.startDownload({"title": title, "source": "mangadex", "hash_id": "00000000-0000-4000-8000-000000000001"},
                                [{"chapter_id": title, "number": "1", "pages_count": 5}], "pdf", "Any")
    window.findChild(QObject, "viewStack").setProperty("currentIndex", 1)
    downloads = window.findChild(QObject, "downloadsView")
    downloads.toggleJob(queue.jobs[0]["id"])
    QTest.qWait(50)
    downloads.toggleJob(queue.jobs[1]["id"])
    QTest.qWait(50)
    assert downloads.property("expandedJobId") == queue.jobs[1]["id"]
    from PyQt6.QtQuick import QQuickItem
    content = window.findChild(QObject, "downloadJobList").property("contentItem")
    cards = [obj for obj in content.childItems() if obj.objectName().startswith("downloadCard_")]
    assert len(cards) == 2, (len(cards), messages, len(queue.jobs), window.findChild(QObject, "downloadJobList").property("count"))
    assert sum(card.property("expanded") for card in cards) == 1
    button = cards[0].findChild(QObject, "openManga_" + queue.jobs[0]["id"])
    assert button is not None and button.property("enabled")
    with patch("gui.bridge.manga_bridge.FetchWorker") as worker:
        button.clicked.emit()
        app.processEvents()
        assert worker.call_args.args[0] == "https://mangadex.org/title/00000000-0000-4000-8000-000000000001"
    assert window.findChild(QObject, "viewStack").property("currentIndex") == 0
    assert downloads.mangaUrl({"source": "comix", "hash_id": "abc"}) == "https://comix.to/title/abc"
    assert not [message for message in messages if any(word in message for word in ("ReferenceError", "TypeError", "Unable to assign", "Binding loop"))], messages
    for name in ["settingsScroll", "chooseDownloadFolderButton", "bookLayoutSelector",
                 "pdfWidthInput", "compressedImagesToggle", "fallbackEnglishToggle",
                 "temporaryPathInput", "chooseTemporaryFolderButton", "clearDownloadedPagesButton"]:
        assert window.findChild(QObject, name) is not None, name
    toggle = window.findChild(QObject, "compressedImagesToggle")
    toggle.toggled.emit(True)
    app.processEvents()
    assert config.get("use_compressed_image") is True
    assert toggle.property("checked") is True
    assert window.findChild(QObject, "pdfLayoutSelector") is None
    bridges[-1].resetToDefaults()
    app.processEvents()
    assert toggle.property("checked") is False
    import base64
    from io import BytesIO
    from PIL import Image
    image = BytesIO()
    Image.new("RGB", (20, 30), "red").save(image, "PNG")
    cover_url = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()
    browse = window.findChild(QObject, "browseView")
    source_manga = {"title": "Site title", "author": "Site author", "poster_url": cover_url,
                   "poster_source": cover_url, "source": "mangadex", "hash_id": "test",
                   "selected_language": "pt-br"}
    source_manga.update(is_nsfw=False, manga_type="Manga", status="ongoing", year=2026,
                        final_chapter="2", latest_chapter="2", rank=0, rated_avg=0,
                        follows_total=0, description="", genres=[], original_language="ja")
    browse.showManga(source_manga)
    browse.showChapters([{"chapter_id": "1", "number": "1", "title": "", "volume": "1",
                         "group_name": "Group", "pages_count": 1, "votes": 0, "language": "pt-br"}])
    chapter_list = browse.getChapterList()
    chapter_list.selectRange("1-1")
    controls = window.findChild(QObject, "downloadControls")
    controls.downloadClicked.emit()
    app.processEvents()
    dialog = window.findChild(QObject, "downloadMetadataDialog")
    assert dialog.property("visible")
    title_input = window.findChild(QObject, "bookTitleInput")
    author_input = window.findChild(QObject, "bookAuthorInput")
    assert title_input.property("text") == "Site title"
    assert author_input.property("text") == "Site author"
    layout_input = window.findChild(QObject, "bookLayoutSelector")
    assert layout_input.property("visible")
    assert layout_input.property("currentIndex") == 0
    assert len(queue.jobs) == 2
    dialog.close()
    assert len(queue.jobs) == 2
    controls.downloadClicked.emit()
    title_input.setProperty("text", "Edited title")
    author_input.setProperty("text", "Edited author")
    cover_path = Path(tmp) / "custom-cover.png"
    Image.new("RGB", (25, 35), "blue").save(cover_path)
    with patch("PyQt6.QtWidgets.QFileDialog.getOpenFileName", return_value=(str(cover_path), "PNG")):
        window.findChild(QObject, "chooseBookCoverButton").clicked.emit()
    window.findChild(QObject, "bookFormatSelector").setProperty("currentIndex", 1)
    assert not layout_input.property("visible")
    window.findChild(QObject, "bookFormatSelector").setProperty("currentIndex", 0)
    assert layout_input.property("visible")
    layout_input.setProperty("currentIndex", 1)
    with patch.object(queue, "startPending"):
        window.findChild(QObject, "confirmBookDownloadButton").clicked.emit()
        app.processEvents()
    assert len(queue.jobs) == 3
    edited = queue.jobs[-1]
    assert edited["manga"]["title"] == "Edited title"
    assert edited["manga"]["author"] == "Edited author"
    assert base64.b64decode(edited["manga"]["poster_url"].split(",", 1)[1]) == cover_path.read_bytes()
    assert edited["config"]["combine_chapters"] is True
    assert edited["config"]["output_format"] == "pdf"
    assert edited["config"]["pdf_layout"] == "webcomic"
    assert config.get("pdf_layout") == "pages"
    assert source_manga["title"] == "Site title"
    assert not [message for message in messages if any(word in message for word in ("ReferenceError", "TypeError", "Unable to assign", "Binding loop"))], messages
    engine.deleteLater()
    app.processEvents()
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parents[1],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
