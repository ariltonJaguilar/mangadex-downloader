"""Image-based EPUB 3 books with embedded cover and author metadata.

Package/container structure follows https://www.w3.org/TR/epub-33/.
"""
from datetime import datetime, timezone
from html import escape
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED

from PIL import Image


def create_epub(chapters, output_path, manga, cover=None):
    if not chapters or not any(paths for _, paths in chapters):
        raise ValueError("No chapter pages provided for EPUB creation")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_name(output_path.name + ".part")
    manifest, spine, navigation = [], [], []
    languages = list(dict.fromkeys(ch.language or "und" for ch, _ in chapters))
    title = escape(manga.title)
    try:
        with ZipFile(partial, "w", ZIP_DEFLATED) as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
            archive.writestr("META-INF/container.xml", '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>''')

            def add_page(source, identifier, label, language, is_cover=False):
                from ..core.downloader import is_cancelled
                if is_cancelled():
                    raise InterruptedError("Download cancelled")
                with Image.open(source) as image:
                    width, height = image.size
                    if image.format in ("JPEG", "PNG", "GIF"):
                        extension, mime = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"),
                                           "GIF": ("gif", "image/gif")}[image.format]
                        data = Path(source).read_bytes()
                    else:
                        buffer = BytesIO()
                        image.convert("RGB").save(buffer, "PNG")
                        data, extension, mime = buffer.getvalue(), "png", "image/png"
                img_name = f"images/{identifier}.{extension}"
                page_name = f"{identifier}.xhtml"
                archive.writestr("EPUB/" + img_name, data)
                props = ' properties="cover-image"' if is_cover else ""
                manifest.append(f'<item id="img-{identifier}" href="{img_name}" media-type="{mime}"{props}/>')
                manifest.append(f'<item id="{identifier}" href="{page_name}" media-type="application/xhtml+xml"/>')
                spine.append(f'<itemref idref="{identifier}"/>')
                archive.writestr("EPUB/" + page_name, f'''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{escape(language, quote=True)}">
<head><title>{escape(label)}</title><meta name="viewport" content="width={width},height={height}"/>
<style>html,body{{margin:0;padding:0;width:{width}px;height:{height}px}} img{{display:block;width:100%;height:100%}}</style></head>
<body><img src="{img_name}" alt="{escape(label, quote=True)}"/></body></html>''')
                return page_name

            if cover:
                page = add_page(cover, "cover", manga.title, languages[0], True)
                navigation.append(f'<li><a href="{page}">Capa</a></li>')
            for chapter_index, (chapter, paths) in enumerate(chapters, 1):
                for page_index, path in enumerate(paths, 1):
                    label = f"{chapter.get_display_name()} — {page_index}"
                    page = add_page(path, f"chapter-{chapter_index}-page-{page_index}", label, chapter.language or "und")
                    if page_index == 1:
                        navigation.append(f'<li><a href="{page}">{escape(chapter.get_display_name())}</a></li>')
            archive.writestr("EPUB/nav.xhtml", f'''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{escape(languages[0], quote=True)}">
<head><title>{title}</title></head><body><nav epub:type="toc" id="toc"><h1>{title}</h1><ol>{''.join(navigation)}</ol></nav></body></html>''')
            manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
            modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            creator = f'<dc:creator>{escape(manga.author)}</dc:creator>' if manga.author else ""
            cover_meta = '<meta name="cover" content="img-cover"/>' if cover else ""
            archive.writestr("EPUB/package.opf", f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="book-id">urn:uuid:{uuid4()}</dc:identifier><dc:title>{title}</dc:title>{creator}
{''.join('<dc:language>' + escape(language) + '</dc:language>' for language in languages)}
<meta property="dcterms:modified">{modified}</meta><meta property="rendition:layout">pre-paginated</meta>
<meta property="rendition:spread">none</meta>{cover_meta}</metadata>
<manifest>{''.join(manifest)}</manifest><spine>{''.join(spine)}</spine></package>''')
        partial.replace(output_path)
        return output_path
    except Exception:
        partial.unlink(missing_ok=True)
        raise
