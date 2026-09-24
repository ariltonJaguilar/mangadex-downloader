"""
PDF creation from downloaded images.
"""

from pathlib import Path
from io import BytesIO
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from ..utils.logger import get_logger

logger = get_logger(__name__)


def create_book_pdf(chapters, output_path, manga, cover=None, *, layout="pages", page_width=0):
    """Write chapters in order while retaining at most one chapter in memory."""
    if not chapters or not any(paths for _, paths in chapters):
        raise ValueError("No chapter pages provided for PDF creation")
    if layout not in ("pages", "webcomic") or (page_width != 0 and not 600 <= page_width <= 4000):
        raise ValueError("Invalid PDF layout or width")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = _part_path(output_path)
    try:
        c = canvas.Canvas(str(partial))
        c.setTitle(manga.title)
        c.setAuthor(manga.author or "")
        groups = [(None, [cover])] if cover else []
        groups += list(chapters)
        for group_index, (chapter, paths) in enumerate(groups):
            images = []
            try:
                for index, path in enumerate(paths, 1):
                    images.append((index, _load_image_for_pdf(path)))
                first_page = True
                for img, width, height in _pdf_pages(images, layout if chapter else "pages", page_width):
                    from ..core.downloader import is_cancelled
                    if is_cancelled():
                        raise InterruptedError("Download cancelled")
                    c.setPageSize((width, height))
                    if first_page:
                        key = f"chapter-{group_index}"
                        c.bookmarkPage(key)
                        c.addOutlineEntry(chapter.get_display_name() if chapter else "Capa", key)
                        first_page = False
                    c.drawImage(ImageReader(img), 0, 0, width, height)
                    c.showPage()
            finally:
                for _, img in images:
                    img.close()
        c.save()
        partial.replace(output_path)
        return output_path
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def _part_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.name}.part")


def _load_image_for_pdf(source) -> Image.Image:
    img = Image.open(source)
    img.load()

    if img.mode in ('RGBA', 'LA', 'P'):
        if img.mode == 'P':
            img = img.convert('RGBA')
        background = Image.new('RGB', img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
        return background

    if img.mode != 'RGB':
        return img.convert('RGB')

    return img


def _pdf_pages(images, layout, page_width):
    """Normalize width and join webtoon panels without crossing chapter boundaries.

    Adapted from mangadex-downloader's PDFFile.create_webcomic_strips (MIT).
    Limit strips to eight screen widths and 32 million pixels.
    """
    width = page_width or max(img.width for _, img in images)
    if layout == "pages":
        for _, img in images:
            yield img, width, max(1, round(img.height * width / img.width))
        return

    max_height = max(1, min(width * 8, 32_000_000 // width))
    strip = Image.new("RGB", (width, max_height), "white")
    used = 0
    try:
        for _, original in images:
            height = max(1, round(original.height * width / original.width))
            normalized = original.resize((width, height), Image.Resampling.LANCZOS)
            try:
                top = 0
                while top < height:
                    count = min(max_height - used, height - top)
                    piece = normalized.crop((0, top, width, top + count))
                    strip.paste(piece, (0, used))
                    piece.close()
                    used += count
                    top += count
                    if used == max_height:
                        yield strip, width, used
                        used = 0
                # Next image continues immediately below the preceding panel.
            finally:
                normalized.close()
        if used:
            cropped = strip.crop((0, 0, width, used))
            try:
                yield cropped, width, used
            finally:
                cropped.close()
    finally:
        strip.close()


def _write_pdf(images: list[tuple[int, Image.Image]], output_path: Path, title: str,
               layout="pages", page_width=0) -> Path:
    if not images:
        raise ValueError("No valid images provided for PDF creation")
    if layout not in ("pages", "webcomic"):
        raise ValueError("Invalid PDF layout")
    if page_width != 0 and not 600 <= page_width <= 4000:
        raise ValueError("PDF width must be automatic (0) or 600–4000 pixels")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _part_path(output_path)
    if tmp_path.exists():
        tmp_path.unlink()

    try:
        c = canvas.Canvas(str(tmp_path))
        c.setTitle(title)

        for idx, (img, img_width, img_height) in enumerate(_pdf_pages(images, layout, page_width), 1):
            c.setPageSize((img_width, img_height))

            img_buffer = BytesIO()
            img.save(img_buffer, format='JPEG', quality=95)
            img_buffer.seek(0)

            c.drawImage(ImageReader(img_buffer), 0, 0, img_width, img_height)
            c.showPage()
            logger.debug(f"Added page {idx} to PDF")

        c.save()
        tmp_path.replace(output_path)
        logger.info(f"Created PDF: {output_path}")
        return output_path
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        for _, img in images:
            img.close()


def create_pdf(
    image_paths: list[Path],
    output_path: str | Path,
    title: str = "Manga Chapter",
    *, layout: str = "pages", page_width: int = 0,
) -> Path:
    """
    Create a PDF from a list of image files.
    
    Args:
        image_paths: List of image file paths
        output_path: Output PDF file path
        title: PDF title metadata
    
    Returns:
        Path to created PDF
    """
    output_path = Path(output_path)
    images = [
        (idx, _load_image_for_pdf(img_path))
        for idx, img_path in enumerate(sorted(image_paths), 1)
    ]
    return _write_pdf(images, output_path, title, layout, page_width)


def create_pdf_from_bytes(
    image_data: list[tuple[int, bytes]],
    output_path: str | Path,
    title: str = "Manga Chapter",
    *, layout: str = "pages", page_width: int = 0,
) -> Path:
    """
    Create a PDF directly from image bytes without saving to disk first.
    
    Args:
        image_data: List of (index, image_bytes) tuples
        output_path: Output PDF file path
        title: PDF title metadata
    
    Returns:
        Path to created PDF
    """
    output_path = Path(output_path)
    images = [
        (idx, _load_image_for_pdf(BytesIO(data)))
        for idx, data in sorted(image_data, key=lambda x: x[0])
    ]
    return _write_pdf(images, output_path, title, layout, page_width)
