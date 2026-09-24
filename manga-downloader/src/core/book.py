"""Durable chapter pages and one final book per queued download."""
import base64
import hashlib
import json
from pathlib import Path
from uuid import UUID

from .models import Chapter, OutputFormat
from ..formats.images import validate_image_bytes, get_image_extension
from ..utils.page_cache import register
from ..utils.state import write_json
from ..utils.temporary import temporary_root, chapter_workspace, publish_file


def book_filename(title):
    """Preserve the edited title except characters forbidden in Windows filenames."""
    name = "".join("_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch for ch in title)
    name = name.strip()[:180].rstrip(" .") or "Mangá"
    stem = name.split(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        name = "_" + name
    return name


def book_directory(config):
    identifier = str(UUID(config.book_id))
    return temporary_root(config.temp_path) / "books" / identifier


def chapter_directory(config, chapter_id):
    key = hashlib.sha256(str(chapter_id).encode()).hexdigest()
    return book_directory(config) / key


def save_chapter_pages(config, chapter, images, expected_pages=None):
    directory = chapter_directory(config, chapter.chapter_id)
    directory.mkdir(parents=True, exist_ok=True)
    names = []
    for index, source in sorted(images):
        data = source.read_bytes() if isinstance(source, Path) else source
        target = directory / f"{index:06d}{get_image_extension(data)}"
        register(target)
        target.write_bytes(data)
        names.append(target.name)
    write_json(directory / "pages.json", names)
    write_json(directory / "page-total.json", expected_pages if expected_pages is not None else len(names))


def saved_page_total(config, chapter_id):
    try:
        return max(0, int(json.loads((chapter_directory(config, chapter_id) / "page-total.json").read_text())))
    except (OSError, ValueError, TypeError):
        return 0


def saved_chapter_pages(config, chapter_id):
    directory = chapter_directory(config, chapter_id)
    try:
        names = json.loads((directory / "pages.json").read_text(encoding="utf-8"))
        if not isinstance(names, list) or not names:
            return []
        paths = []
        for name in names:
            if not isinstance(name, str) or Path(name).name != name:
                return []
            path = directory / name
            validate_image_bytes(path.read_bytes())
            paths.append(path)
        return paths
    except (OSError, ValueError):
        return []


def prepare_cover(config, manga):
    """Snapshot the chosen cover once, including across cancellation/resume."""
    if not manga.poster_url:
        return None
    directory = book_directory(config)
    target = directory / "cover.image"
    if target.exists():
        try:
            validate_image_bytes(target.read_bytes())
            return target
        except ValueError:
            pass
    if manga.poster_url.startswith("data:image/"):
        data = base64.b64decode(manga.poster_url.split(",", 1)[1], validate=True)
        validate_image_bytes(data)
    else:
        from .downloader import ImageDownloader
        _, data, error = ImageDownloader(config, source=manga.source).download_image(manga.poster_url, 0)
        if data is None:
            raise RuntimeError(f"Não foi possível baixar a capa: {error}")
    directory.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def finalize_book(config, manga, chapter_rows, allow_incomplete=False):
    from .downloader import is_cancelled
    chapters = []
    for row in chapter_rows:
        paths = saved_chapter_pages(config, row["chapter_id"])
        if not paths:
            if allow_incomplete:
                continue
            raise RuntimeError(f"Páginas do capítulo {row['number']} ausentes. Retome o download.")
        chapter = Chapter(row["chapter_id"], str(row["number"]), title=row.get("title"),
                          volume=row.get("volume"), language=row.get("language", ""))
        chapters.append((chapter, paths))
    if not chapters:
        raise RuntimeError("Nenhuma página disponível para gerar o arquivo. Retome o download.")
    cover = prepare_cover(config, manga)
    with chapter_workspace(config.temp_path) as workspace:
        staged = workspace / f"book.{config.output_format.value}"
        if config.output_format == OutputFormat.PDF:
            from ..formats.pdf import create_book_pdf
            create_book_pdf(chapters, staged, manga, cover,
                            layout=config.pdf_layout, page_width=config.pdf_page_width)
        elif config.output_format == OutputFormat.EPUB:
            from ..formats.epub import create_epub
            create_epub(chapters, staged, manga, cover)
        else:
            raise ValueError("Combined books require PDF or EPUB")
        if is_cancelled():
            raise InterruptedError("Download cancelled")
        destination = Path(config.download_path) / f"{book_filename(manga.title)}.{config.output_format.value}"
        publish_file(staged, destination)
    if not config.keep_images and not allow_incomplete:
        for _, paths in chapters:
            for path in paths:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass  # A published book remains successful if scratch cleanup fails.
    return destination
