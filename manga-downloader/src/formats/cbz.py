"""
CBZ (Comic Book ZIP) creation with ComicInfo.xml metadata.
"""

import zipfile
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom
from typing import Optional
from ..core.models import MangaInfo, Chapter
from .images import get_image_extension, validate_image_bytes
from ..utils.logger import get_logger

logger = get_logger(__name__)


def _part_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.name}.part")


def _write_cbz(
    entries: list[tuple[str, bytes | Path]],
    output_path: Path,
    manga: Optional[MangaInfo],
    chapter: Optional[Chapter],
) -> Path:
    if not entries:
        raise ValueError("No valid images provided for CBZ creation")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _part_path(output_path)
    if tmp_path.exists():
        tmp_path.unlink()

    try:
        with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as cbz:
            for filename, source in entries:
                if isinstance(source, Path):
                    cbz.write(source, filename)
                else:
                    cbz.writestr(filename, source)
                logger.debug(f"Added to CBZ: {filename}")

            if manga and chapter:
                comic_info = create_comic_info_xml(manga, chapter, len(entries))
                cbz.writestr("ComicInfo.xml", comic_info)
                logger.debug("Added ComicInfo.xml")

        tmp_path.replace(output_path)
        logger.info(f"Created CBZ: {output_path}")
        return output_path
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def create_comic_info_xml(
    manga: MangaInfo,
    chapter: Chapter,
    page_count: int
) -> str:
    """
    Create ComicInfo.xml content for CBZ metadata.
    
    Args:
        manga: Manga information
        chapter: Chapter information
        page_count: Number of pages
    
    Returns:
        XML string
    """
    root = Element("ComicInfo")
    root.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
    root.set("xmlns:xsd", "http://www.w3.org/2001/XMLSchema")
    
    # Title
    SubElement(root, "Title").text = chapter.get_display_name()
    
    # Series info
    SubElement(root, "Series").text = manga.title
    
    if manga.alt_titles:
        SubElement(root, "AlternateSeries").text = manga.alt_titles[0] if manga.alt_titles else ""
    
    # Chapter/Volume numbers
    try:
        SubElement(root, "Number").text = str(chapter.number)
    except (ValueError, TypeError):
        pass
    
    if chapter.volume:
        try:
            SubElement(root, "Volume").text = str(chapter.volume)
        except (ValueError, TypeError):
            pass
    
    # Summary
    if manga.description:
        SubElement(root, "Summary").text = manga.description[:2000]  # Limit length
    
    # Year
    if manga.year:
        SubElement(root, "Year").text = str(manga.year)
    
    # Publisher/Team
    if chapter.group_name:
        SubElement(root, "Publisher").text = chapter.group_name
    
    # Genre
    if manga.genres:
        SubElement(root, "Genre").text = ", ".join(str(g) for g in manga.genres[:10])
    
    # Page count
    SubElement(root, "PageCount").text = str(page_count)
    
    # Language
    if manga.original_language:
        SubElement(root, "LanguageISO").text = manga.original_language
    
    # Manga type
    SubElement(root, "Manga").text = "Yes" if manga.manga_type in ("manga", "manhwa", "manhua") else "Unknown"
    
    # Rating
    if manga.rated_avg:
        # Convert to 5-star scale
        rating = min(5.0, max(0.0, float(manga.rated_avg)))
        SubElement(root, "CommunityRating").text = f"{rating:.1f}"
    
    # Status
    if manga.status:
        SubElement(root, "SeriesStatus").text = manga.status.title()
    
    # NSFW flag
    if manga.is_nsfw:
        SubElement(root, "AgeRating").text = "Adults Only 18+"
    
    # Web link
    SubElement(root, "Web").text = f"https://comix.to/title/{manga.hash_id}-{manga.slug}"
    
    # Format to pretty XML
    xml_str = tostring(root, encoding="unicode")
    parsed = minidom.parseString(xml_str)
    return parsed.toprettyxml(indent="  ", encoding=None)


def create_cbz(
    image_paths: list[Path],
    output_path: str | Path,
    manga: Optional[MangaInfo] = None,
    chapter: Optional[Chapter] = None
) -> Path:
    """
    Create a CBZ archive from image files.
    
    Args:
        image_paths: List of image file paths
        output_path: Output CBZ file path
        manga: Manga information for ComicInfo.xml
        chapter: Chapter information for ComicInfo.xml
    
    Returns:
        Path to created CBZ
    """
    output_path = Path(output_path)
    entries = []
    for img_path in sorted(image_paths):
        data = img_path.read_bytes()
        validate_image_bytes(data)
        entries.append((img_path.name, img_path))

    return _write_cbz(entries, output_path, manga, chapter)


def create_cbz_from_bytes(
    image_data: list[tuple[int, bytes]],
    output_path: str | Path,
    manga: Optional[MangaInfo] = None,
    chapter: Optional[Chapter] = None
) -> Path:
    """
    Create a CBZ archive directly from image bytes.
    
    Args:
        image_data: List of (index, image_bytes) tuples
        output_path: Output CBZ file path
        manga: Manga information for ComicInfo.xml
        chapter: Chapter information for ComicInfo.xml
    
    Returns:
        Path to created CBZ
    """
    output_path = Path(output_path)
    entries = []
    for idx, data in sorted(image_data, key=lambda x: x[0]):
        ext = _get_extension_from_bytes(data)
        entries.append((f"{idx:03d}{ext}", data))

    return _write_cbz(entries, output_path, manga, chapter)


def _get_extension_from_bytes(data: bytes) -> str:
    """Determine image extension from file header."""
    return get_image_extension(data)
