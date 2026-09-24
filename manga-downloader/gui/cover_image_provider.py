"""Cloudflare-aware asynchronous image provider for Comix cover art."""

from __future__ import annotations

import base64
import threading
import time
from urllib.parse import urlparse

import requests
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QImage
from PyQt6.QtQml import QQmlImageProviderBase
from PyQt6.QtQuick import QQuickImageProvider

from src.utils.comix_session import get_comix_credentials
from src.utils.logger import get_logger


logger = get_logger(__name__)

_PROVIDER_NAME = "comix-cover"
_PROVIDER_PREFIX = f"image://{_PROVIDER_NAME}/"
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


def cover_image_source(url: str | None) -> str:
    """Convert an approved Comix poster URL into a QML image-provider source."""
    if not _is_allowed_cover_url(url):
        return ""
    encoded = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
    return _PROVIDER_PREFIX + encoded


def _decode_cover_source(image_id: str | None) -> str | None:
    if not isinstance(image_id, str) or not image_id:
        return None
    try:
        decoded = base64.urlsafe_b64decode(image_id + "=" * (-len(image_id) % 4)).decode("utf-8")
    except (ValueError, UnicodeError):
        return None
    return decoded if _is_allowed_cover_url(decoded) else None


def _is_allowed_cover_url(url: str | None) -> bool:
    if not isinstance(url, str) or not url:
        return False
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and hostname in {"static.comix.to", "uploads.mangadex.org"}
        and port in (None, 443)
        and bool(parsed.path)
    )


class ComixCoverImageProvider(QQuickImageProvider):
    """Fetch Comix covers with the active browser clearance session."""

    def __init__(self):
        super().__init__(
            QQmlImageProviderBase.ImageType.Image,
            QQmlImageProviderBase.Flag.ForceAsynchronousImageLoading,
        )
        self._logged_failures: set[tuple[str, str]] = set()
        self._failure_lock = threading.Lock()

    def _log_failure_once(self, url: str, reason: str) -> None:
        key = (url, reason)
        with self._failure_lock:
            if key in self._logged_failures:
                return
            if len(self._logged_failures) >= 512:
                self._logged_failures.clear()
            self._logged_failures.add(key)
        logger.warning("Comix cover unavailable (%s): %s", reason, url)

    def requestImage(self, image_id: str | None, requested_size: QSize):
        del requested_size
        url = _decode_cover_source(image_id)
        if url is None:
            return QImage(), QSize()

        credentials = get_comix_credentials()
        is_comix = urlparse(url).hostname == "static.comix.to"
        if is_comix and not credentials.user_agent:
            self._log_failure_once(url, "browser session is not ready")
            return QImage(), QSize()

        headers = {
            "User-Agent": credentials.user_agent if is_comix else "MangaDownloader/1.0",
            "Referer": "https://comix.to/" if is_comix else "https://mangadex.org/",
        }
        response = None
        for attempt in range(2):
            try:
                response = requests.get(
                    url,
                    headers=headers,
                    cookies=credentials.cookie_dict if is_comix else {},
                    timeout=(5, 15),
                    stream=True,
                )
            except requests.RequestException as exc:
                if attempt == 0:
                    time.sleep(0.15)
                    continue
                self._log_failure_once(url, type(exc).__name__)
                return QImage(), QSize()

            if response.status_code not in _RETRYABLE_STATUSES or attempt == 1:
                break
            close = getattr(response, "close", None)
            if callable(close):
                close()
            time.sleep(0.15)

        if response is None:
            self._log_failure_once(url, "no response")
            return QImage(), QSize()
        if response.status_code != 200:
            close = getattr(response, "close", None)
            if callable(close):
                close()
            self._log_failure_once(url, f"HTTP {response.status_code}")
            return QImage(), QSize()
        if not _is_allowed_cover_url(response.url):
            close = getattr(response, "close", None)
            if callable(close):
                close()
            self._log_failure_once(url, "unexpected redirect")
            return QImage(), QSize()
        if not response.headers.get("content-type", "").lower().startswith("image/"):
            close = getattr(response, "close", None)
            if callable(close):
                close()
            self._log_failure_once(url, "non-image response")
            return QImage(), QSize()

        content_length = response.headers.get("content-length")
        try:
            too_large = content_length is not None and int(content_length) > _MAX_IMAGE_BYTES
        except (TypeError, ValueError):
            too_large = False
        if too_large:
            close = getattr(response, "close", None)
            if callable(close):
                close()
            self._log_failure_once(url, "response too large")
            return QImage(), QSize()

        try:
            iterator = getattr(response, "iter_content", None)
            if callable(iterator):
                chunks = []
                total = 0
                for chunk in iterator(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > _MAX_IMAGE_BYTES:
                        self._log_failure_once(url, "response too large")
                        return QImage(), QSize()
                    chunks.append(chunk)
                image_data = b"".join(chunks)
            else:
                image_data = response.content
        except Exception as exc:
            self._log_failure_once(url, type(exc).__name__)
            return QImage(), QSize()
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

        if len(image_data) > _MAX_IMAGE_BYTES:
            self._log_failure_once(url, "response too large")
            return QImage(), QSize()
        image = QImage()
        if not image.loadFromData(image_data):
            self._log_failure_once(url, "invalid image data")
            return QImage(), QSize()
        return image, image.size()


def provider_name() -> str:
    return _PROVIDER_NAME
