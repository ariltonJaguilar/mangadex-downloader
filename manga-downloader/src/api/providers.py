"""Resolve supported title URLs and select a platform adapter."""

from urllib.parse import urlparse
from uuid import UUID


def resolve_title_url(url: str) -> tuple[str, str]:
    value = (url or "").strip()
    parsed = urlparse(value if "://" in value else "https://" + value)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("Use a Comix or MangaDex title URL.")
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2 or parts[0] != "title":
        raise ValueError("Paste a title URL, rather than a chapter or list URL.")
    host = (parsed.hostname or "").lower()
    if host in {"mangadex.org", "www.mangadex.org"}:
        try:
            return "mangadex", str(UUID(parts[1]))
        except ValueError:
            raise ValueError("Invalid MangaDex title ID.") from None
    if host in {"comix.to", "www.comix.to"}:
        from .comix import ComixAPI
        return "comix", ComixAPI.extract_manga_code("https://comix.to" + parsed.path)
    raise ValueError("Supported platforms: comix.to and mangadex.org.")


def get_provider(source: str = "comix", language: str = "pt-br", *, data_saver=False, fallback_english=False):
    if source == "mangadex":
        from .mangadex import MangaDexAPI
        return MangaDexAPI(language=language, data_saver=data_saver, fallback_english=fallback_english)
    if source == "comix":
        from .comix import ComixAPI
        return ComixAPI
    raise ValueError(f"Unsupported platform: {source}")
