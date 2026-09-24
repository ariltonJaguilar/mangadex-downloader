"""Exercise a long expanded download while progress notifications arrive."""
import subprocess
import sys
from pathlib import Path


def test_expanded_download_keeps_scroll_and_delegates_during_progress():
    script = '''
import tempfile
from pathlib import Path
from unittest.mock import patch
from PyQt6.QtCore import QObject, QUrl, qInstallMessageHandler
from PyQt6.QtWidgets import QApplication
from PyQt6.QtQuick import QQuickView
from PyQt6.QtTest import QTest
from gui.bridge.download_bridge import DownloadBridge
from src.utils.config import ConfigManager

app = QApplication([])
messages = []
qInstallMessageHandler(lambda kind, context, message: messages.append(message))
with tempfile.TemporaryDirectory() as tmp:
    queue = DownloadBridge(config_manager=ConfigManager(Path(tmp) / "config.json"))
    chapters = [{"chapter_id": str(i), "number": str(i), "pages_count": 20, "language": "pt-br"}
                for i in range(1, 301)]
    with patch.object(queue, "startPending"):
        queue.startDownload({"title": "Long manga", "source": "mangadex"}, chapters, "pdf", "Any")
        queue.startDownload({"title": "Next manga", "source": "mangadex"}, chapters[:1], "pdf", "Any")
    window = QQuickView()
    window.rootContext().setContextProperty("DownloadBridge", queue)
    window.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    window.resize(900, 650)
    window.setSource(QUrl.fromLocalFile(str(Path("gui/qml/views/DownloadsView.qml").resolve())))
    assert window.rootObject(), messages
    window.show()
    root = window.rootObject()
    job = queue.jobs[0]
    root.toggleJob(job["id"])
    QTest.qWait(50)
    view = root.findChild(QObject, "downloadJobList")
    content = view.property("contentItem")
    card = next(item for item in content.childItems() if item.objectName() == "downloadCard_" + job["id"])
    assert card.height() > 10000, card.height()
    # Chapters have no nested Flickable that could consume the wheel.
    assert not [child for child in card.findChildren(QObject)
                if child.metaObject().indexOfProperty("contentY") >= 0]
    row_name = "downloadChapter_" + job["id"] + "_20"
    def visual_find(item, name):
        if item.objectName() == name:
            return item
        for child in item.childItems():
            found = visual_find(child, name)
            if found is not None:
                return found
        return None
    chapter = visual_find(card, row_name)
    assert chapter is not None
    view.setProperty("contentY", 1700.0)
    QTest.qWait(20)
    position = view.property("contentY")
    assert position > 1000
    queue._active = job
    for current in range(1, 21):
        queue._on_detail_progress("20", current, 20)
        QTest.qWait(5)
        assert abs(view.property("contentY") - position) < 1, (position, view.property("contentY"))
        assert visual_find(card, row_name) is chapter
        assert card.property("expanded")
    payload = chapter.property("modelData")
    if hasattr(payload, "toVariant"):
        payload = payload.toVariant()
    assert payload["current"] == 20
    queue._on_detail_complete("20", True, "")
    QTest.qWait(20)
    assert abs(view.property("contentY") - position) < 1
    # Structural changes to other cards also retain the expanded card.
    queue._jobs.pop()
    queue._changed()
    QTest.qWait(20)
    assert view.property("count") == 1
    assert abs(view.property("contentY") - position) < 1
    assert visual_find(card, row_name) is chapter
    root.toggleJob(job["id"])
    # Qt Quick applies the collapsed column height during a render/layout pass;
    # a fixed 20 ms wait can finish before that pass under suite load.
    for _ in range(100):
        if not card.property("expanded") and card.height() < 500:
            break
        QTest.qWait(10)
    assert not card.property("expanded")
    assert card.height() < 500
    assert not [message for message in messages if any(word in message for word in
                ("ReferenceError", "TypeError", "Unable to assign", "Binding loop"))], messages
    queue._active = None
    window.close()
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parents[1],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
