from .models import Chapter, DownloadConfig, MangaBrowsePage, MangaInfo, MangaSummary

__all__ = [
    "MangaInfo",
    "MangaSummary",
    "MangaBrowsePage",
    "Chapter",
    "DownloadConfig",
    "MangaDownloader",
]


def __getattr__(name):
    if name == "MangaDownloader":
        from .downloader import MangaDownloader

        return MangaDownloader
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
