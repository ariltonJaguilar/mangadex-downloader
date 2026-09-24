"""
Manga Downloader GUI - Main Entry Point
PyQt6 + QML Application
"""

import sys
import os
import argparse
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtQml import QQmlApplicationEngine
from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtGui import QFontDatabase

from gui.bridge import DiscoveryBridge, DownloadBridge, MangaBridge, SettingsBridge
from gui.cover_image_provider import ComixCoverImageProvider, provider_name
from src.utils.config import ConfigManager
from src.utils.logger import setup_logging
from gui.bridge.history_bridge import HistoryBridge
from src.utils.temporary import configure_temporary_root


def load_fonts():
    """Load custom fonts."""
    fonts_dir = Path(__file__).parent / "resources" / "fonts"
    if fonts_dir.exists():
        for font_file in fonts_dir.glob("*.ttf"):
            QFontDatabase.addApplicationFont(str(font_file))


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Manga Downloader GUI")
    parser.add_argument("--debug", action="store_true", help="Log diagnostic messages to the terminal and logs/")
    parser.add_argument(
        "--cpu", 
        action="store_true",
        help="Use software (CPU) rendering instead of GPU"
    )
    return parser.parse_args()


def main():
    """Main entry point for the GUI application."""
    args = parse_args()
    if args.debug:
        os.environ["COMIX_LOG_DIR"] = str(project_root / "logs")
    
    # Suppress Qt/QML warnings (they are harmless but noisy)
    # os.environ["QT_LOGGING_RULES"] = "*=false"  # Suppress all Qt debug/warning messages
    
    # Set rendering backend (must be set BEFORE QApplication)
    if args.cpu:
        # Full software rendering - works on all systems
        os.environ["QT_QUICK_BACKEND"] = "software"
        os.environ["QT_OPENGL"] = "software"
        os.environ["QSG_RENDER_LOOP"] = "basic"
        print("Using software (CPU) rendering")
    
    # Enable high DPI scaling
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
    
    app = QApplication(sys.argv)
    app.setApplicationName("Manga Downloader")
    app.setOrganizationName("MangaDownloader")
    
    # Load fonts
    load_fonts()
    
    # Force Fusion style via environment variable to avoid DLL loading issues with native styles
    # This also avoids needing the PyQt6.QtQuickControls2 module which might be missing
    os.environ["QT_QUICK_CONTROLS_STYLE"] = "Fusion"
    
    # Create QML engine
    engine = QQmlApplicationEngine()
    cover_provider = ComixCoverImageProvider()
    engine.addImageProvider(provider_name(), cover_provider)
    
    # Use one in-memory configuration source for all GUI bridges.  This keeps
    # settings changes visible to workers created later in the same session.
    config_manager = ConfigManager()
    setup_logging(enable=args.debug or bool(config_manager.get("enable_logs", False)))
    temp_error = ""
    try:
        configure_temporary_root(config_manager.get("temp_path", ""))
    except OSError as exc:
        temp_error = f"A pasta temporária não está disponível. Altere-a em Settings: {exc}"
    manga_bridge = MangaBridge(config_manager=config_manager)
    discovery_bridge = DiscoveryBridge(config_manager=config_manager)
    download_bridge = DownloadBridge(config_manager=config_manager)
    settings_bridge = SettingsBridge(config_manager=config_manager)
    history_bridge = HistoryBridge(config_manager=config_manager)
    
    # Expose bridges to QML
    engine.rootContext().setContextProperty("MangaBridge", manga_bridge)
    engine.rootContext().setContextProperty("DiscoveryBridge", discovery_bridge)
    engine.rootContext().setContextProperty("DownloadBridge", download_bridge)
    engine.rootContext().setContextProperty("SettingsBridge", settings_bridge)
    engine.rootContext().setContextProperty("HistoryBridge", history_bridge)
    
    # Add QML import path
    qml_dir = Path(__file__).parent / "qml"
    engine.addImportPath(str(qml_dir))
    
    # Load main QML file
    qml_file = qml_dir / "main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_file)))
    
    # Check if QML loaded successfully
    if not engine.rootObjects():
        print("Error: Failed to load QML")
        sys.exit(-1)
    
    # Run the application
    QTimer.singleShot(0, download_bridge.startPending)
    if temp_error:
        QTimer.singleShot(0, lambda: settings_bridge.errorOccurred.emit(temp_error))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
