"""Application scratch space and safe publication across different drives."""
import errno
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4
from contextlib import contextmanager

DEFAULT_TEMP_ROOT = Path(tempfile.gettempdir()) / "manga-downloader"


def temporary_root(value=""):
    path = Path(value).expanduser() if value and str(value).strip() else DEFAULT_TEMP_ROOT
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    return path.resolve()


def configure_temporary_root(value=""):
    """Set the app's library/browser scratch location for subsequent operations."""
    path = temporary_root(value)
    path.mkdir(parents=True, exist_ok=True)
    # Fail promptly on read-only folders (mkstemp retries PermissionError on Windows).
    probe = path / (".write-check-" + uuid4().hex)
    with probe.open("xb"):
        pass
    probe.unlink()
    tempfile.tempdir = str(path)
    os.environ["TMP"] = os.environ["TEMP"] = os.environ["TMPDIR"] = str(path)
    return path


@contextmanager
def chapter_workspace(value=""):
    root = temporary_root(value)
    root.mkdir(parents=True, exist_ok=True)
    directory = root / ("chapter-" + uuid4().hex)
    directory.mkdir()
    try:
        yield directory
    finally:
        # Only delete this invocation's freshly-created, uniquely-named directory.
        shutil.rmtree(directory)


def publish_file(source, destination):
    """Keep the old output intact until the replacement is complete, even across drives."""
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, destination)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV and getattr(exc, "winerror", None) != 17:
            raise
    # Cross-drive rename is not atomic. Copy to a unique destination-side file,
    # then atomically publish that complete file. Never truncate an existing PDF/CBZ.
    partial = destination.with_name(f".{destination.name}.{uuid4().hex}.part")
    try:
        shutil.copyfile(source, partial)
        os.replace(partial, destination)
        source.unlink()
    finally:
        partial.unlink(missing_ok=True)
