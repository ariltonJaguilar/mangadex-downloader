"""
Bridge module for Python↔QML communication
"""
from .manga_bridge import MangaBridge
from .discovery_bridge import DiscoveryBridge
from .download_bridge import DownloadBridge
from .settings_bridge import SettingsBridge

__all__ = ["MangaBridge", "DiscoveryBridge", "DownloadBridge", "SettingsBridge"]
