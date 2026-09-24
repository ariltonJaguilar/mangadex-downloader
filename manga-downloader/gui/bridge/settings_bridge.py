"""
Settings Bridge - Exposes configuration to QML
"""

import sys
from pathlib import Path
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, pyqtProperty
from PyQt6.QtWidgets import QFileDialog

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.utils.config import ConfigManager, application_root, default_config_path
from src.utils.logger import setup_logging
from src.utils.temporary import temporary_root, configure_temporary_root
from src.utils.page_cache import clear_pages


class SettingsBridge(QObject):
    """Bridge for settings/configuration."""
    
    # Signals
    settingsChanged = pyqtSignal()
    chapterOptionsChanged = pyqtSignal()
    mangaLanguageChanged = pyqtSignal()
    errorOccurred = pyqtSignal(str)
    pagesCleared = pyqtSignal(str)
    
    def __init__(self, parent=None, config_manager=None):
        super().__init__(parent)
        self._config_manager = (
            config_manager if config_manager is not None else ConfigManager()
        )
    
    # Output Format
    @pyqtProperty(str, notify=settingsChanged)
    def outputFormat(self):
        return self._config_manager.get("output_format", "images")
    
    @outputFormat.setter
    def outputFormat(self, value: str):
        self._config_manager.set("output_format", value)
        self.settingsChanged.emit()
    
    # Keep Images
    @pyqtProperty(bool, notify=settingsChanged)
    def keepImages(self):
        return self._config_manager.get("keep_images", False)
    
    @keepImages.setter
    def keepImages(self, value: bool):
        self._config_manager.set("keep_images", value)
        self.settingsChanged.emit()
    
    # Download Path
    @pyqtProperty(str, notify=settingsChanged)
    def downloadPath(self):
        return self._config_manager.get("download_path", "downloads")
    
    @downloadPath.setter
    def downloadPath(self, value: str):
        self._config_manager.set("download_path", value)
        self.settingsChanged.emit()
    
    # Max Chapter Workers
    @pyqtProperty(str, notify=settingsChanged)
    def tempPath(self):
        return str(temporary_root(self._config_manager.get("temp_path", "")))

    @pyqtSlot()
    def chooseTemporaryFolder(self):
        selected = QFileDialog.getExistingDirectory(
            None, "Escolher pasta de arquivos temporários", self.tempPath,
            QFileDialog.Option.ShowDirsOnly,
        )
        if selected:
            self.setValue("temp_path", selected)

    @pyqtSlot()
    def clearDownloadedPages(self):
        try:
            count = clear_pages()
            self.pagesCleared.emit(f"{count} arquivos de páginas removidos.")
        except (OSError, RuntimeError) as exc:
            self.errorOccurred.emit(str(exc))

    @pyqtProperty(int, notify=settingsChanged)
    def maxChapterWorkers(self):
        return self._config_manager.get("max_chapter_workers", 3)
    
    @maxChapterWorkers.setter
    def maxChapterWorkers(self, value: int):
        self._config_manager.set("max_chapter_workers", max(1, min(10, value)))
        self.settingsChanged.emit()
    
    # Max Image Workers
    @pyqtProperty(int, notify=settingsChanged)
    def maxImageWorkers(self):
        return self._config_manager.get("max_image_workers", 5)
    
    @maxImageWorkers.setter
    def maxImageWorkers(self, value: int):
        self._config_manager.set("max_image_workers", max(1, min(20, value)))
        self.settingsChanged.emit()
        
    # Headless
    @pyqtProperty(bool, notify=settingsChanged)
    def headless(self):
        return self._config_manager.get("headless", True)
    
    @headless.setter
    def headless(self, value: bool):
        self._config_manager.set("headless", value)
        self.settingsChanged.emit()
    
    # Slots for QML
    @pyqtProperty('QVariant', notify=settingsChanged)
    def options(self):
        return self._config_manager.all_settings

    @pyqtSlot()
    def chooseDownloadFolder(self):
        selected = QFileDialog.getExistingDirectory(
            None, "Escolher pasta de download", self.getDownloadPathAbsolute(),
            QFileDialog.Option.ShowDirsOnly,
        )
        if selected:
            self.downloadPath = selected

    @pyqtSlot(str, 'QVariant')
    def setValue(self, key: str, value):
        """Generic setter for any config value."""
        if key == "temp_path":
            try:
                value = str(configure_temporary_root(str(value)))
            except (OSError, ValueError) as exc:
                self.errorOccurred.emit(f"Não foi possível usar a pasta temporária: {exc}")
                return
        if key == "pdf_layout" and value not in ("pages", "webcomic"):
            self.errorOccurred.emit("Invalid PDF layout")
            return
        if key == "pdf_page_width":
            try:
                value = int(value)
                if value != 0 and not 600 <= value <= 4000:
                    raise ValueError()
            except (ValueError, TypeError):
                self.errorOccurred.emit("PDF width must be 0 (automatic) or 600–4000 pixels")
                return
        if key == "max_chapter_workers":
            value = max(1, min(10, int(value)))
        if key == "max_image_workers":
            value = max(1, min(20, int(value)))
        self._config_manager.set(key, value)
        if key == "enable_logs":
            setup_logging(enable=bool(value))
        self.settingsChanged.emit()
        if key == "fallback_english":
            self.chapterOptionsChanged.emit()
        if key == "mangadex_language":
            self.mangaLanguageChanged.emit()
    
    @pyqtSlot(str, result='QVariant')
    def getValue(self, key: str):
        """Generic getter for any config value."""
        return self._config_manager.get(key)
    
    @pyqtSlot()
    def resetToDefaults(self):
        """Reset all settings to defaults."""
        self._config_manager.reset_to_defaults()
        try:
            configure_temporary_root()
        except OSError as exc:
            self.errorOccurred.emit(f"Não foi possível restaurar a pasta temporária: {exc}")
        setup_logging(enable=False)
        self.settingsChanged.emit()
        self.chapterOptionsChanged.emit()
    
    @pyqtSlot(result=str)
    def getDownloadPathAbsolute(self):
        """Get absolute path to downloads folder."""
        path = Path(self._config_manager.get("download_path", "downloads"))
        if not path.is_absolute() and self._config_manager.config_path == default_config_path():
            path = application_root() / path
        return str(path.absolute())
