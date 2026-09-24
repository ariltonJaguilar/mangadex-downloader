"""The 50 most recent searches and opened titles, shared across sessions."""
from datetime import datetime, timezone
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, pyqtProperty
from src.api.providers import resolve_title_url
from src.utils.state import read_list, write_json
from src.utils.logger import get_logger

logger = get_logger(__name__)


class HistoryBridge(QObject):
    changed = pyqtSignal()

    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.path = config_manager.config_path.with_name("search-history.json")
        self._items = read_list(self.path)[:50]

    @pyqtProperty('QVariant', notify=changed)
    def items(self):
        return self._items

    @pyqtSlot(str, str, str)
    def remember(self, value, platform, title=""):
        value = value.strip()
        if not value:
            return
        try:
            platform, identifier = resolve_title_url(value)
            kind = "title"
            value = f"https://{'mangadex.org' if platform == 'mangadex' else 'comix.to'}/title/{identifier}"
            key = f"{platform}:title:{identifier}"
        except ValueError:
            kind = "search"
            key = f"{platform}:search:{value.casefold()}"
        previous = next((item for item in self._items if item.get("key") == key), {})
        entry = {"key": key, "kind": kind, "value": value, "platform": platform,
                 "title": title or previous.get("title") or value,
                 "timestamp": datetime.now(timezone.utc).isoformat()}
        self._items = [entry] + [item for item in self._items if item.get("key") != key][:49]
        try:
            write_json(self.path, self._items)
        except OSError:
            logger.exception("Could not save search history")
        self.changed.emit()
