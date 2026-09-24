"""Background bridge for manga discovery and catalog search."""

import sys
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.cover_image_provider import cover_image_source
from src.utils.logger import get_logger
import time

logger = get_logger(__name__)


def _summary_to_dict(summary) -> dict:
    """Convert a MangaSummary into a QML-friendly dictionary."""
    return {
        "manga_id": summary.manga_id,
        "manga_code": summary.manga_code,
        "title": summary.title,
        "poster_url": summary.poster_url,
        "poster_source": cover_image_source(summary.poster_url),
        "manga_type": summary.manga_type or "Unknown",
        "status": summary.status or "Unknown",
        "year": summary.year or 0,
        "latest_chapter": summary.latest_chapter or "",
        "rated_avg": summary.rated_avg or 0,
        "content_rating": summary.content_rating or "safe",
        "canonical_url": summary.canonical_url,
    }


class DiscoveryWorker(QThread):
    """Execute one catalog operation away from the Qt GUI thread."""

    highlightsReady = pyqtSignal("QVariant")
    resultsReady = pyqtSignal("QVariant")
    error = pyqtSignal(str)

    def __init__(self, operation: str, query: str = "", page: int = 1, headless: bool = True, source: str = "comix"):
        super().__init__()
        self.operation = operation
        self.query = query
        self.page = page
        self.headless = headless
        self.source = source

    def run(self):
        started = time.monotonic()
        logger.info("Discovery started: platform=%s operation=%s headless=%s", self.source, self.operation, self.headless)
        try:
            from src.api.providers import get_provider
            ComixAPI = get_provider(self.source)

            if self.operation == "highlights":
                highlights = ComixAPI.get_manga_highlights(headless=self.headless)
                self.highlightsReady.emit({
                    key: [_summary_to_dict(summary) for summary in summaries]
                    for key, summaries in highlights.items()
                })
                return

            browse_page = ComixAPI.search_manga(
                self.query,
                page=self.page,
                headless=self.headless,
            )
            self.resultsReady.emit({
                "items": [_summary_to_dict(summary) for summary in browse_page.items],
                "page": browse_page.page,
                "last_page": browse_page.last_page,
                "total": browse_page.total,
                "has_next": browse_page.has_next,
                "has_previous": browse_page.has_previous,
                "query": self.query,
            })
        except Exception as exc:
            logger.exception("Discovery failed for %s", self.source)
            self.error.emit(str(exc))
        finally:
            logger.info("Discovery worker finished after %.1fs", time.monotonic() - started)


class DiscoveryBridge(QObject):
    """Expose catalog discovery operations to QML."""

    highlightsLoaded = pyqtSignal("QVariant")
    resultsLoaded = pyqtSignal("QVariant")
    errorOccurred = pyqtSignal(str)
    loadingChanged = pyqtSignal(bool)

    def __init__(self, parent=None, config_manager=None):
        super().__init__(parent)
        if config_manager is None:
            from src.utils.config import ConfigManager

            config_manager = ConfigManager()
        self._config_manager = config_manager
        self._worker = None
        self._pending = None
        self._loading = False

    @pyqtSlot()
    def loadHighlights(self):
        """Load the initial trending/latest discovery rails."""
        self._enqueue("highlights", "", 1)

    @pyqtSlot(str, int)
    def search(self, query: str, page: int = 1):
        """Search the catalog, replacing any queued page request."""
        query = (query or "").strip()
        if not query:
            return
        self._enqueue("search", query, max(1, int(page)))

    def _enqueue(self, operation: str, query: str, page: int):
        request = (operation, query, page)
        if self._worker is not None and self._worker.isRunning():
            self._pending = request
            return
        self._start(request)

    def _start(self, request):
        operation, query, page = request
        headless = self._config_manager.get_download_config().headless
        self._worker = DiscoveryWorker(operation, query, page, headless=headless, source=self._config_manager.get("platform"))
        self._worker.highlightsReady.connect(self.highlightsLoaded.emit)
        self._worker.resultsReady.connect(self.resultsLoaded.emit)
        self._worker.error.connect(self.errorOccurred.emit)
        self._worker.finished.connect(self._on_worker_finished)
        if not self._loading:
            self._loading = True
            self.loadingChanged.emit(True)
        self._worker.start()

    @pyqtSlot()
    def _on_worker_finished(self):
        worker = self.sender()
        if worker is not None:
            worker.deleteLater()
        self._worker = None
        if self._pending is not None:
            request = self._pending
            self._pending = None
            self._start(request)
            return
        if self._loading:
            self._loading = False
            self.loadingChanged.emit(False)
