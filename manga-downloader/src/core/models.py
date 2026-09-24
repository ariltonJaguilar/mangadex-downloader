"""
Data models for manga, chapters, and configuration.
"""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


def _safe_path_component(value: str, max_length: int, fallback: str) -> str:
    """Return a filesystem-safe path component."""
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in value)
    safe = safe.strip()[:max_length].rstrip(" .")
    return safe or fallback


class OutputFormat(str, Enum):
    """Supported output formats."""
    IMAGES = "images"
    PDF = "pdf"
    CBZ = "cbz"
    EPUB = "epub"


@dataclass(frozen=True)
class MangaSummary:
    """Compact manga row used by discovery/search views."""

    manga_id: Optional[int | str] = None
    manga_code: str = ""
    title: str = "Unknown"
    poster_url: str = ""
    manga_type: Optional[str] = None
    status: Optional[str] = None
    year: Optional[int] = None
    latest_chapter: Optional[str] = None
    rated_avg: Optional[float] = None
    content_rating: str = "safe"
    canonical_url: str = ""


@dataclass(frozen=True)
class MangaBrowsePage:
    """A page of manga summaries returned by the Comix catalog."""

    items: list[MangaSummary] = field(default_factory=list)
    page: int = 1
    last_page: int = 1
    total: int = 0

    @property
    def has_next(self) -> bool:
        return self.page < self.last_page

    @property
    def has_previous(self) -> bool:
        return self.page > 1


@dataclass
class MangaInfo:
    """Manga information from API."""
    manga_id: Optional[int | str] = None
    hash_id: Optional[str] = None
    title: str = "Unknown"
    alt_titles: list[str] = field(default_factory=list)
    slug: Optional[str] = None
    rank: Optional[int] = None
    manga_type: Optional[str] = None
    poster_url: Optional[str] = None
    original_language: Optional[str] = None
    status: Optional[str] = None
    final_chapter: Optional[str] = None
    latest_chapter: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    rated_avg: Optional[float] = None
    rated_count: Optional[int] = None
    follows_total: Optional[int] = None
    is_nsfw: bool = False
    year: Optional[int] = None
    genres: list = field(default_factory=list)
    description: str = ""
    source: str = "comix"
    author: str = ""
    
    def get_safe_title(self) -> str:
        """Get filesystem-safe title."""
        return _safe_path_component(self.title, 100, "Unknown")


@dataclass
class Chapter:
    """Chapter information."""
    chapter_id: int | str
    number: str
    title: Optional[str] = None
    volume: Optional[str] = None
    votes: Optional[int] = None
    group_name: Optional[str] = None
    pages_count: int = 0
    language: str = ""
    
    def get_display_name(self) -> str:
        """Get chapter display name."""
        name = f"Chapter {self.number}"
        if self.title:
            name += f": {self.title}"
        return name
    
    def get_safe_folder_name(self) -> str:
        """Get filesystem-safe folder name."""
        name = f"Chapter_{self.number}"
        if self.title:
            safe_title = _safe_path_component(self.title, 50, "")
            if safe_title:
                name += f"_{safe_title}"
        if self.language:
            volume = _safe_path_component(str(self.volume or "none"), 20, "none")
            name = f"Vol_{volume}_{name}_{self.language}_{self.chapter_id}"
        return name.rstrip(" .")


@dataclass
class DownloadConfig:
    """Download configuration."""
    output_format: OutputFormat = OutputFormat.IMAGES
    keep_images: bool = False
    enable_logs: bool = False
    max_chapter_workers: int = 3
    max_image_workers: int = 5
    download_path: str = "downloads"
    temp_path: str = ""
    retry_count: int = 3
    retry_delay: float = 2.0
    headless: bool = True
    pdf_layout: str = "pages"
    pdf_page_width: int = 0
    use_compressed_image: bool = False
    fallback_english: bool = False
    selected_language: str = ""
    combine_chapters: bool = False
    book_id: str = ""
