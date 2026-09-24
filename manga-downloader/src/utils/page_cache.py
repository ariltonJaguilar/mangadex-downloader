"""Persistent validated pages, independent of expiring CDN query strings."""
import hashlib
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from .config import default_config_path
from .state import read_list, write_json
from .temporary import temporary_root
from ..formats.images import validate_image_bytes

_lock = threading.RLock()
_active = 0


def registry_path():
    return default_config_path().with_name("page-cache.json")


@contextmanager
def page_session():
    global _active
    with _lock:
        _active += 1
    try:
        yield
    finally:
        with _lock:
            _active -= 1


def register(path):
    path = str(Path(path).resolve())
    with _lock:
        entries = read_list(registry_path())
        if not any(entry.get("path") == path for entry in entries):
            entries.append({"path": path})
            write_json(registry_path(), entries)


class PageCache:
    def __init__(self, config, manga, chapter, urls, namespace=None):
        # Host/signature changes must not invalidate pages; chapter, quality,
        # ordered page paths and count must match before they can be reused.
        identity = [manga.source, str(manga.hash_id or manga.manga_id or manga.slug),
                    str(chapter.chapter_id), config.use_compressed_image,
                    [urlsplit(url).path if not url.startswith("data:") else url for url in urls]]
        if namespace is not None:
            identity.append(namespace)
        key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        self.root = temporary_root(config.temp_path) / "retained-pages" / key
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, index):
        return self.root / f"{index:06d}.page"

    def get(self, index):
        path = self.path(index)
        try:
            data = path.read_bytes()
            validate_image_bytes(data)
            return data
        except (OSError, ValueError):
            return None

    def put(self, index, data):
        import os
        from uuid import uuid4
        path = self.path(index)
        register(path)  # Record ownership before writing, including crash recovery.
        partial = path.with_name(path.name + "." + uuid4().hex + ".part")
        register(partial)
        try:
            partial.write_bytes(data)
            os.replace(partial, path)
        finally:
            partial.unlink(missing_ok=True)


def clear_pages():
    """Delete only individually registered pages; never recursively delete folders."""
    with _lock:
        if _active:
            raise RuntimeError("Aguarde ou cancele os downloads antes de limpar as páginas.")
        entries = read_list(registry_path())
        remaining = []
        removed = 0
        for entry in entries:
            try:
                path = Path(entry["path"])
                if path.is_file() or path.is_symlink():
                    path.unlink()
                    removed += 1
            except (OSError, KeyError):
                remaining.append(entry)
        write_json(registry_path(), remaining)
        if remaining:
            raise OSError(f"{removed} arquivos removidos; {len(remaining)} não puderam ser removidos. Tente novamente.")
        return removed
