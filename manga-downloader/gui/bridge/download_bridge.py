"""
Download Bridge - Handles download operations between Python and QML
"""

import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, QThread
import threading
import time

FINAL_RETRY_SECONDS = 180
FINAL_CHAPTER_SECONDS = 60
from .download_queue import QueueController

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class DownloadWorker(QThread):
    """Background worker for downloading chapters concurrently."""
    
    chapterProgress = pyqtSignal(str, int, int)  # chapter_name, current, total
    chapterComplete = pyqtSignal(str, bool, str)  # chapter_name, success, message
    overallProgress = pyqtSignal(int, int)  # completed, total
    resultReady = pyqtSignal(int, int)  # successful, failed
    detailProgress = pyqtSignal(str, int, int)
    detailComplete = pyqtSignal(str, bool, str)
    error = pyqtSignal(str)
    messageChanged = pyqtSignal(str)
    bookReady = pyqtSignal(str, int, int)
    
    def __init__(self, manga_dict: dict, chapters: list, config):
        super().__init__()
        self.manga_dict = manga_dict
        self.chapters = chapters
        self.config = config
        self._lock = threading.Lock()
        self._completed = 0
        self._successful = 0
        self._failed = 0
        self._outcomes = {}
        self._page_counts = {}
    
    def _download_single_chapter(self, chapter_dict, manga, total):
        """Download a single chapter. Called from thread pool."""
        try:
            from src.core.downloader import ChapterDownloader
            from src.core.models import Chapter
            
            # Convert dict to Chapter object
            chapter = Chapter(
                language=chapter_dict.get("language", ""),
                chapter_id=chapter_dict["chapter_id"],
                number=chapter_dict["number"],
                title=chapter_dict.get("title"),
                volume=chapter_dict.get("volume"),
                votes=chapter_dict.get("votes"),
                group_name=chapter_dict.get("group_name"),
                pages_count=chapter_dict.get("pages_count", 0)
            )
            
            chapter_name = chapter.get_display_name()
            self.detailProgress.emit(str(chapter.chapter_id), 0, chapter.pages_count)
            
            # Create a callback for image progress
            def on_image_progress(current, total):
                with self._lock:
                    self._page_counts[str(chapter.chapter_id)] = total
                self.chapterProgress.emit(chapter_name, current, total)
                self.detailProgress.emit(str(chapter.chapter_id), current, total)
            
            # Download the chapter
            ch_downloader = ChapterDownloader(
                self.config,
                manga,
                reader_service=getattr(self, "_reader_service", None),
                image_pool=getattr(self, "_image_pool", None),
                deadline=getattr(self, "_retry_deadline", None),
            )
            success, message = ch_downloader.download_chapter(
                chapter, 
                on_image_progress=on_image_progress
            )
            
            # Update progress with thread safety
            with self._lock:
                self._outcomes[str(chapter.chapter_id)] = success
                self._completed = len(self._outcomes)
                self._successful = sum(self._outcomes.values())
                self._failed = self._completed - self._successful
                completed = self._completed
                successful = self._successful
                failed = self._failed
            
            # Emit signals (Qt handles thread safety for signals)
            self.chapterComplete.emit(chapter_name, success, message)
            self.detailComplete.emit(str(chapter.chapter_id), success, message)
            self.overallProgress.emit(completed, total)
            
            return success, chapter_name
            
        except Exception as e:
            chapter_name = f"Chapter {chapter_dict.get('number', '?')}"
            with self._lock:
                self._outcomes[str(chapter_dict["chapter_id"])] = False
                self._completed = len(self._outcomes)
                self._successful = sum(self._outcomes.values())
                self._failed = self._completed - self._successful
                completed = self._completed
            
            self.chapterComplete.emit(chapter_name, False, str(e))
            self.detailComplete.emit(str(chapter_dict["chapter_id"]), False, str(e))
            self.overallProgress.emit(completed, total)
            return False, chapter_name
    
    def run(self):
        try:
            from src.core.models import MangaInfo
            from src.core.downloader import reset_downloads, ImageDownloadPool
            from src.api.comix import ChapterReaderService

            
            # Convert dict back to MangaInfo
            manga = MangaInfo(
                source=self.manga_dict.get("source", "comix"),
                manga_id=self.manga_dict.get("manga_id"),
                hash_id=self.manga_dict.get("hash_id"),
                title=self.manga_dict.get("title", "Unknown"),
                author=self.manga_dict.get("author", ""),
                alt_titles=self.manga_dict.get("alt_titles", []),
                rank=self.manga_dict.get("rank"),
                manga_type=self.manga_dict.get("manga_type"),
                status=self.manga_dict.get("status"),
                poster_url=self.manga_dict.get("poster_url"),
                original_language=self.manga_dict.get("original_language"),
                final_chapter=self.manga_dict.get("final_chapter"),
                latest_chapter=self.manga_dict.get("latest_chapter"),
                start_date=self.manga_dict.get("start_date"),
                end_date=self.manga_dict.get("end_date"),
                year=self.manga_dict.get("year"),
                rated_avg=self.manga_dict.get("rated_avg"),
                rated_count=self.manga_dict.get("rated_count"),
                follows_total=self.manga_dict.get("follows_total"),
                is_nsfw=self.manga_dict.get("is_nsfw", False),
                slug=self.manga_dict.get("slug"),
                genres=self.manga_dict.get("genres", []),
                description=self.manga_dict.get("description", "")
            )
            
            total = len(self.chapters)
            max_workers = 1 if manga.source == "comix" else self.config.max_chapter_workers

            if self.config.combine_chapters:
                from src.core.book import prepare_cover
                self.messageChanged.emit("Preparando capa...")
                prepare_cover(self.config, manga)
                self.messageChanged.emit("")

            self._reader_service = ChapterReaderService(self.config.headless) if manga.source == "comix" else None
            self._image_pool = ImageDownloadPool(self.config.max_image_workers)
            
            # Use ThreadPoolExecutor for concurrent downloads
            try:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [
                        executor.submit(self._download_single_chapter, ch, manga, total)
                        for ch in self.chapters
                    ]

                    # Wait for all to complete
                    for future in as_completed(futures):
                        try:
                            future.result()
                        except Exception:
                            pass  # Errors are emitted by _download_single_chapter
                from src.core.downloader import is_cancelled
                from src.core.models import OutputFormat
                from src.utils.comix_limits import comix_limits
                if self.config.combine_chapters and self.config.output_format == OutputFormat.PDF and self._failed and not is_cancelled():
                    if manga.source != "comix" or not comix_limits.reason:
                        self.messageChanged.emit("Tentando novamente as páginas e capítulos com falha...")
                        if self._reader_service is not None:
                            self._reader_service.close()
                            self._reader_service = ChapterReaderService(self.config.headless)
                        failed_rows = [ch for ch in self.chapters if not self._outcomes.get(str(ch["chapter_id"]), False)]
                        retry_end = time.monotonic() + FINAL_RETRY_SECONDS
                        for index, row in enumerate(failed_rows, 1):
                            if is_cancelled() or time.monotonic() >= retry_end or (manga.source == "comix" and comix_limits.reason):
                                break
                            self._retry_deadline = min(retry_end, time.monotonic() + FINAL_CHAPTER_SECONDS)
                            self.messageChanged.emit(f"Tentativa final {index}/{len(failed_rows)} — capítulo {row['number']} (até {FINAL_CHAPTER_SECONDS}s por capítulo)")
                            self._download_single_chapter(row, manga, total)
                        self._retry_deadline = None
            finally:
                self._image_pool.shutdown()
                if self._reader_service is not None:
                    self._reader_service.close()
                self._image_pool = None
                self._reader_service = None
            
            from src.core.downloader import is_cancelled
            from src.core.models import OutputFormat
            if self.config.combine_chapters and not is_cancelled() and (not self._failed or self.config.output_format == OutputFormat.PDF):
                from src.core.book import finalize_book, saved_chapter_pages, saved_page_total
                rows = getattr(self, "book_chapters", self.chapters)
                missing, unknown = 0, 0
                for row in rows:
                    identifier = str(row["chapter_id"])
                    available = len(saved_chapter_pages(self.config, identifier))
                    expected = max(saved_page_total(self.config, identifier), self._page_counts.get(identifier, 0), row.get("pages_count", 0) or 0)
                    missing += max(0, expected - available)
                    unknown += int(not available and not expected)
                partial = bool(self._failed or missing or unknown)
                self.messageChanged.emit("Gerando PDF com as páginas disponíveis..." if partial else "Gerando o livro completo...")
                destination = finalize_book(self.config, manga, rows, allow_incomplete=partial)
                self.bookReady.emit(str(destination), missing, unknown)
                summary = f"Salvo em: {destination}"
                if partial:
                    summary += f" · {missing} páginas falharam"
                    if unknown:
                        summary += f" · {unknown} capítulos sem total de páginas identificado"
                self.messageChanged.emit(summary)
            self.resultReady.emit(self._successful, self._failed)
            
        except Exception as e:
            self.error.emit(str(e))


class DownloadBridge(QueueController):
    """Bridge for download operations exposed to QML."""
    
    downloadStarted = pyqtSignal()
    chapterProgress = pyqtSignal(str, int, int)
    chapterComplete = pyqtSignal(str, bool, str)
    overallProgress = pyqtSignal(int, int)
    downloadFinished = pyqtSignal(int, int)
    errorOccurred = pyqtSignal(str)
    
    def __init__(self, parent=None, config_manager=None):
        super().__init__(parent)
        self._worker = None
        # Import here to avoid circular imports
        if config_manager is None:
            from src.utils.config import ConfigManager

            config_manager = ConfigManager()
        self._config_manager = config_manager
        self.initializeQueue()

    @pyqtSlot(result=str)
    def chooseCover(self):
        from PyQt6.QtWidgets import QFileDialog
        import base64
        from src.formats.images import get_image_extension
        selected, _ = QFileDialog.getOpenFileName(None, "Escolher capa", "",
                                                  "Imagens (*.jpg *.jpeg *.png *.webp *.gif)")
        if not selected:
            return ""
        try:
            data = Path(selected).read_bytes()
            extension = get_image_extension(data).lstrip(".")
            mime = "jpeg" if extension == "jpg" else extension
            return f"data:image/{mime};base64," + base64.b64encode(data).decode("ascii")
        except (OSError, ValueError) as exc:
            self.errorOccurred.emit(f"Não foi possível abrir a capa: {exc}")
            return ""
    
    @pyqtSlot('QVariant', 'QVariant', str, str)
    def startDownload(self, manga: dict, chapters, format_type: str, scanlator: str):
        """
        Start downloading selected chapters concurrently.
        
        Args:
            manga: Manga info dict
            chapters: List of selected chapter dicts (QJSValue from QML)
            format_type: Output format (images/pdf/cbz)
            scanlator: Preferred scanlator or empty for any
        """
        if hasattr(manga, "toVariant"):
            manga = manga.toVariant()
        # Convert QJSValue to Python list
        if hasattr(chapters, 'toVariant'):
            chapters = chapters.toVariant()
        if not isinstance(chapters, list):
            chapters = list(chapters) if chapters else []
        
        if not chapters:
            self.errorOccurred.emit("No chapters selected")
            return
        
        # Import here to avoid circular imports
        from src.core.models import OutputFormat
        
        # Get config and update format
        config = self._config_manager.get_download_config()
        config.selected_language = manga.get("selected_language") or (
            config.selected_language if manga.get("source") == "mangadex" else "en")
        config.combine_chapters = bool(manga.get("combine_chapters", False)) and format_type in ("pdf", "epub")
        layout = manga.get("pdf_layout", config.pdf_layout)
        if layout not in ("pages", "webcomic"):
            self.errorOccurred.emit("Escolha mangá ou tira longa para o PDF.")
            return
        config.pdf_layout = layout
        try:
            config.output_format = OutputFormat(format_type)
        except ValueError:
            self.errorOccurred.emit("Invalid format. Choose images, pdf, epub, or cbz")
            return
        if not str(manga.get("title", "")).strip():
            self.errorOccurred.emit("Informe o nome do mangá.")
            return
        
        def chapter_key(ch):
            return (ch.get("volume") or "", ch.get("language") or "",
                    ch["number"] if ch["number"] != "Oneshot" else ch["chapter_id"])

        # Source priority follows the order in which sources first appear in
        # the chapter feed. A chosen preference moves to the front; missing
        # chapters fall back to the next source in that same order.
        source_order = {}
        chapter_order = {}
        for index, ch in enumerate(chapters):
            source_order.setdefault(ch.get("group_name"), len(source_order))
            chapter_order.setdefault(chapter_key(ch), index)
        preferred = scanlator if scanlator and scanlator != "Any" else None
        chosen = {}
        for ch in chapters:
            key = chapter_key(ch)
            priority = (-1 if preferred is not None and ch.get("group_name") == preferred else
                        source_order[ch.get("group_name")])
            if key not in chosen or priority < chosen[key][0]:
                chosen[key] = (priority, ch)
        chapters = [entry[1] for key, entry in sorted(
            chosen.items(), key=lambda item: chapter_order[item[0]])]
        self._enqueue_download(manga, chapters, config)
    
    @pyqtSlot()
    def cancelDownload(self):
        """Cancel the current download."""
        if self._active:
            self.cancelJob(self._active["id"])
