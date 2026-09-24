import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal, QUrl
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtQuick import QQuickItem
    from PyQt6.QtQml import QQmlComponent, QQmlEngine
except ImportError:  # pragma: no cover - exercised only in minimal environments
    QCoreApplication = None
    QObject = None
    pyqtSignal = None
    QUrl = None
    QGuiApplication = None
    QQuickItem = None
    QQmlComponent = None
    QQmlEngine = None


@unittest.skipUnless(QGuiApplication is not None, "PyQt6 is not installed")
class DiscoveryQmlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QGuiApplication.instance() or QGuiApplication([])

    def _component(self, relative_path, context_properties=None):
        self.engine = QQmlEngine()
        for name, value in (context_properties or {}).items():
            self.engine.rootContext().setContextProperty(name, value)
        source = Path(__file__).parents[1] / relative_path
        component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(source)))
        if component.isError():
            self.fail("Failed to load QML: " + " | ".join(error.toString() for error in component.errors()))
        item = component.create()
        if item is None:
            self.fail("Failed to instantiate QML component")
        return item

    def tearDown(self):
        if hasattr(self, "item") and self.item is not None:
            self.item.deleteLater()
        if hasattr(self, "engine"):
            self.engine.deleteLater()
        self.qt_app.processEvents()

    def test_discovery_view_switches_between_highlights_and_results(self):
        self.item = self._component("gui/qml/views/DiscoveryView.qml")
        self.item.setHighlights({
            "trending": [{"manga_code": "a1", "title": "Trending"}],
            "latest": [{"manga_code": "b2", "title": "Latest"}],
        })
        self.assertFalse(self.item.property("searchMode"))
        self.assertEqual(self.item.property("trending")[0]["title"], "Trending")

        self.item.setProperty("query", "hero")
        self.item.setResults({
            "items": [{"manga_code": "c3", "title": "Hero"}],
            "page": 1,
            "last_page": 2,
            "total": 21,
            "query": "hero",
        })
        self.assertTrue(self.item.property("searchMode"))
        self.assertEqual(self.item.property("results")[0]["title"], "Hero")
        self.assertEqual(self.item.property("lastPage"), 2)

    def test_smart_input_classifies_urls_and_queries(self):
        class FakeBridge(QObject):
            loadingChanged = pyqtSignal(bool)

        manga_bridge = FakeBridge()
        discovery_bridge = FakeBridge()
        self.item = self._component(
            "gui/qml/components/UrlInput.qml",
            {"MangaBridge": manga_bridge, "DiscoveryBridge": discovery_bridge},
        )
        self.assertTrue(self.item.isUrlInput("https://comix.to/title/a1-example"))
        self.assertTrue(self.item.isUrlInput("comix.to/title/a1-example"))
        self.assertFalse(self.item.isUrlInput("one piece"))
