"""
Configuration management - load/save settings from one shared user file.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any
from ..core.models import DownloadConfig, OutputFormat
from .logger import get_logger
from .state import write_json
from .temporary import temporary_root

logger = get_logger(__name__)


def default_config_path() -> Path:
    """Return the one shared, user-owned settings path.

    Keeping this outside the current working directory is important because the
    CLI is normally started from the repository root while the GUI is commonly
    started from ``gui/``.  Both entry points must therefore resolve the same
    file regardless of where they were launched.
    """
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "manga-downloader" / "config.json"


def application_root() -> Path:
    """Return the source/package root used to anchor relative downloads."""
    return Path(__file__).resolve().parents[2]


class ConfigManager:
    """Manages application configuration."""
    
    DEFAULT_CONFIG = {
        "output_format": "images",
        "keep_images": False,
        "enable_logs": False,
        "max_chapter_workers": 3,
        "max_image_workers": 5,
        "download_path": "downloads",
        "temp_path": "",
        "retry_count": 3,
        "retry_delay": 2.0,
        "chapters_display_limit": 20,  # 0 = show all
        "platform": "comix",
        "mangadex_language": "pt-br",
        "pdf_layout": "pages",
        "pdf_page_width": 0,
        "use_compressed_image": False,
        "fallback_english": False,
        "headless": True
    }
    
    def __init__(self, config_path: str | Path | None = None):
        self.config_path = Path(config_path) if config_path is not None else default_config_path()
        self._config: dict[str, Any] = {}
        self.load()
    
    def load(self) -> None:
        """Load configuration from file."""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
                if not isinstance(self._config, dict):
                    raise ValueError("Settings must be an object")
                logger.debug(f"Loaded config from {self.config_path}")
            except (ValueError, IOError) as e:
                logger.warning(f"Failed to load config: {e}. Using defaults.")
                self._config = self.DEFAULT_CONFIG.copy()
        else:
            self._config = (
                self._migrate_legacy_configs()
                if self.config_path == default_config_path()
                else self.DEFAULT_CONFIG.copy()
            )
            self.save()

    def _migrate_legacy_configs(self) -> dict[str, Any]:
        """Merge the old root and GUI files on first run.

        Values that differ from defaults are treated as user preferences.  If
        both legacy files contain different non-default values, the file with
        the newer modification time wins; GUI wins an exact tie because it is
        the entry point most users use for downloads.  Legacy files are never
        modified or removed.
        """
        merged = self.DEFAULT_CONFIG.copy()
        project_root = Path(__file__).resolve().parents[2]
        candidates = [project_root / "config.json", project_root / "gui" / "config.json"]
        existing: list[tuple[Path, dict[str, Any]]] = []
        for path in candidates:
            if path == self.config_path or not path.exists():
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                existing.append((path, value))

        # Root is applied first; GUI is applied second only when it is the
        # newer source or the root value is still the built-in default.
        existing.sort(key=lambda item: (item[0].stat().st_mtime, item[0].name == "config.json"))
        for path, values in existing:
            for key, value in values.items():
                if key not in self.DEFAULT_CONFIG and key not in merged:
                    merged[key] = value
                    continue
                current = merged.get(key, self.DEFAULT_CONFIG.get(key))
                default = self.DEFAULT_CONFIG.get(key)
                if current == default or value == current:
                    if value != default:
                        merged[key] = value
                    continue
                if value != default:
                    merged[key] = value

        logger.info("Migrated legacy settings into %s", self.config_path)
        return merged
    
    def save(self) -> None:
        """Save configuration to file."""
        try:
            write_json(self.config_path, self._config)
            logger.debug(f"Saved config to {self.config_path}")
        except IOError as e:
            logger.error(f"Failed to save config: {e}")
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        return self._config.get(key, self.DEFAULT_CONFIG.get(key, default))
    
    def set(self, key: str, value: Any) -> None:
        """Set a configuration value and save."""
        self._config[key] = value
        self.save()
    
    def get_download_config(self) -> DownloadConfig:
        """Get DownloadConfig from current settings."""
        try:
            output_format = OutputFormat(self.get("output_format", "images"))
        except ValueError:
            bad_format = self.get("output_format", "images")
            logger.warning(f"Invalid output_format {bad_format!r}; using images")
            output_format = OutputFormat.IMAGES

        download_path = self.get("download_path", "downloads")
        if self.config_path == default_config_path():
            path = Path(str(download_path)).expanduser()
            if not path.is_absolute():
                download_path = str((application_root() / path).resolve())

        return DownloadConfig(
            output_format=output_format,
            keep_images=self.get("keep_images", False),
            enable_logs=self.get("enable_logs", False),
            max_chapter_workers=self.get("max_chapter_workers", 3),
            max_image_workers=self.get("max_image_workers", 5),
            download_path=download_path,
            temp_path=str(temporary_root(self.get("temp_path", ""))),
            retry_count=self.get("retry_count", 3),
            retry_delay=self.get("retry_delay", 2.0),
            headless=self.get("headless", True),
            pdf_layout=self.get("pdf_layout", "pages"),
            pdf_page_width=self.get("pdf_page_width", 0),
            use_compressed_image=self.get("use_compressed_image", False),
            fallback_english=self.get("fallback_english", False),
            selected_language=self.get("mangadex_language", "pt-br"),
        )
    
    def update_from_download_config(self, config: DownloadConfig) -> None:
        """Update settings from DownloadConfig."""
        self._config.update({
            "output_format": config.output_format.value,
            "keep_images": config.keep_images,
            "enable_logs": config.enable_logs,
            "max_chapter_workers": config.max_chapter_workers,
            "max_image_workers": config.max_image_workers,
            "download_path": config.download_path,
            "temp_path": config.temp_path,
            "retry_count": config.retry_count,
            "retry_delay": config.retry_delay,
            "headless": config.headless,
            "pdf_layout": config.pdf_layout,
            "pdf_page_width": config.pdf_page_width,
            "use_compressed_image": config.use_compressed_image,
            "fallback_english": config.fallback_english,
        })
        self.save()
    
    def reset_to_defaults(self) -> None:
        """Reset all settings to defaults."""
        self._config = self.DEFAULT_CONFIG.copy()
        self.save()
    
    @property
    def all_settings(self) -> dict[str, Any]:
        """Get all current settings."""
        return {**self.DEFAULT_CONFIG, **self._config}
