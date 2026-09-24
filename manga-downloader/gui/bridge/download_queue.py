"""Persistent FIFO downloads. Only one manga owns the shared downloader at a time."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from uuid import uuid4

from PyQt6.QtCore import QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot
from src.core.models import DownloadConfig, OutputFormat
from src.utils.state import read_list, write_json


class QueueController(QObject):
    jobsChanged = pyqtSignal()
    busyChanged = pyqtSignal()
    shutdownReady = pyqtSignal()

    def initializeQueue(self):
        self._queue_path = self._config_manager.config_path.with_name("downloads.json")
        self._jobs = [job for job in read_list(self._queue_path)
                      if all(k in job for k in ("id", "manga", "chapters", "config", "status", "details"))]
        self._active = None
        self._closing = False
        self._result = None
        from src.utils.comix_limits import comix_limits
        for job in self._jobs:
            if job.get("comix_resume_after") and job["status"] in ("paused", "partial"):
                comix_limits.restore_pause(job.get("message") or "Comix pausado após bloqueio.", job["comix_resume_after"])
            self._recount(job)
            if job["status"] in ("running", "cancelling"):
                job["status"] = "paused"
                job["message"] = "Interrompido ao fechar o aplicativo. Retome quando quiser."
                for ch in job["details"]:
                    if ch["status"] in ("running", "queued"):
                        ch["status"] = "paused"
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1000)
        self._save_timer.timeout.connect(self._save)

    @pyqtProperty('QVariant', notify=jobsChanged)
    def jobs(self):
        return self._jobs

    @pyqtProperty(bool, notify=busyChanged)
    def busy(self):
        return self._worker is not None

    def _save(self):
        try:
            write_json(self._queue_path, self._jobs)
        except OSError as exc:
            self.errorOccurred.emit(f"Não foi possível salvar o histórico: {exc}")

    def _changed(self, immediate=False):
        self.jobsChanged.emit()
        if immediate:
            self._save_timer.stop()
            self._save()
        elif not self._save_timer.isActive():
            self._save_timer.start()

    def _enqueue_download(self, manga, chapters, config):
        if self._closing:
            return
        identifier = str(uuid4())
        if config.combine_chapters:
            config.book_id = identifier
        job = {"id": identifier, "manga": deepcopy(manga), "chapters": deepcopy(chapters),
               "config": asdict(config), "status": "queued", "message": "",
               "created_at": datetime.now(timezone.utc).isoformat(),
               "total": len(chapters), "completed": 0, "successful": 0, "failed": 0,
               "details": [{"id": str(ch["chapter_id"]),
                            "name": f"Capítulo {ch['number']}" + (f" — {ch['title']}" if ch.get('title') else ""),
                            "status": "queued", "current": 0, "total": ch.get("pages_count", 0),
                            "message": ""} for ch in chapters]}
        job["config"]["output_format"] = config.output_format.value
        self._recount(job)
        self._jobs.append(job)
        self._changed(True)
        self.downloadStarted.emit()
        self.startPending()

    @pyqtSlot()
    def startPending(self):
        if self._worker is not None or self._closing:
            return
        from src.utils.comix_limits import comix_limits
        if comix_limits.reason:
            for pending in self._jobs:
                if pending["status"] == "queued" and pending["manga"].get("source", "comix") == "comix":
                    pending.update(status="paused", message=comix_limits.reason,
                                   comix_resume_after=comix_limits.resume_timestamp())
            self._changed(True)
        job = next((job for job in self._jobs if job["status"] == "queued"), None)
        if job is None:
            return
        from .download_bridge import DownloadWorker
        from src.core.downloader import reset_downloads
        try:
            config = DownloadConfig(**job["config"])
            config.output_format = OutputFormat(config.output_format)
        except (TypeError, ValueError) as exc:
            job["status"] = "failed"
            job["message"] = str(exc)
            self._changed(True)
            QTimer.singleShot(0, self.startPending)
            return
        if config.combine_chapters:
            from src.core.book import saved_chapter_pages
            for detail in job["details"]:
                if detail["status"] == "complete" and not saved_chapter_pages(config, detail["id"]):
                    detail.update(status="queued", current=0, message="Páginas temporárias ausentes; baixando novamente.")
            self._recount(job)
        completed_ids = {c["id"] for c in job["details"] if c["status"] == "complete"}
        chapters = [ch for ch in job["chapters"] if str(ch["chapter_id"]) not in completed_ids]
        reset_downloads()
        self._active = job
        self._result = None
        job["status"] = "running"
        job["generation_failed"] = False
        job.pop("output_path", None)
        job.pop("failed_pages", None)
        job.pop("unknown_page_chapters", None)
        self._worker = DownloadWorker(job["manga"], chapters, config)
        self._worker.book_chapters = job["chapters"]
        if hasattr(self._worker, "bookReady"):
            self._worker.bookReady.connect(self._on_book_ready)
        if hasattr(self._worker, "messageChanged"):
            self._worker.messageChanged.connect(self._on_job_message)
        self._worker.detailProgress.connect(self._on_detail_progress)
        self._worker.detailComplete.connect(self._on_detail_complete)
        self._worker.resultReady.connect(self._on_result)
        self._worker.error.connect(self._on_job_error)
        # Advance only after native QThread.finished, never while the old worker is alive.
        self._worker.finished.connect(self._on_thread_finished)
        self._changed(True)
        self.busyChanged.emit()
        self._worker.start()

    @pyqtSlot(str, int, int)
    def _on_detail_progress(self, identifier, current, total):
        if self._active is None:
            return
        for ch in self._active["details"]:
            if ch["id"] == identifier:
                ch.update(current=current, total=total, status="running")
                break
        self._changed()

    @pyqtSlot(str, bool, str)
    def _on_detail_complete(self, identifier, success, message):
        if self._active is None:
            return
        for ch in self._active["details"]:
            if ch["id"] == identifier:
                status = "complete" if success else ("cancelled" if self._active["status"] == "cancelling" else "failed")
                ch.update(status=status, message=message)
                if success:
                    ch["current"] = ch["total"]
                break
        self._recount(self._active)
        self._changed(True)

    @staticmethod
    def _recount(job):
        job["successful"] = sum(ch["status"] == "complete" for ch in job["details"])
        job["failed"] = sum(ch["status"] == "failed" for ch in job["details"])
        job["completed"] = job["successful"] + job["failed"]
        rows = {str(ch["chapter_id"]): ch for ch in job["chapters"]}
        selected = job["config"].get("selected_language")
        if not selected:
            # Older queues lack a language snapshot; English-only history is ambiguous.
            selected = next((ch.get("language") for ch in job["chapters"]
                             if ch.get("language") and ch["language"] != "en"), "")
        job["selected_language"] = selected
        job["selected_downloaded"] = 0
        job["fallback_downloaded"] = 0
        for detail in job["details"]:
            language = rows.get(str(detail["id"]), {}).get("language") or (
                "en" if job["manga"].get("source", "comix") == "comix" else "")
            detail["language"] = language
            detail["is_fallback"] = bool(selected and selected != "en" and language == "en"
                                          and job["manga"].get("source") == "mangadex")
            if detail["status"] == "complete":
                job["selected_downloaded"] += int(bool(selected and language == selected))
                job["fallback_downloaded"] += int(detail["is_fallback"])

    @pyqtSlot(str)
    def _on_job_message(self, message):
        if self._active:
            self._active["message"] = message
            self._changed()

    @pyqtSlot(int, int)
    def _on_result(self, successful, failed):
        self._result = (successful, failed)

    @pyqtSlot(str)
    def _on_job_error(self, message):
        if self._active:
            self._active["message"] = message
            self._active["generation_failed"] = True
        self.errorOccurred.emit(message)

    @pyqtSlot(str, int, int)
    def _on_book_ready(self, path, failed_pages, unknown_chapters):
        if self._active:
            self._active.update(output_path=path, failed_pages=failed_pages,
                                unknown_page_chapters=unknown_chapters)
            self._changed(True)

    @pyqtSlot()
    def _on_thread_finished(self):
        job = self._active
        if job:
            cancelled = job["status"] == "cancelling"
            for ch in job["details"]:
                if ch["status"] in ("queued", "running"):
                    ch["status"] = "cancelled" if cancelled else "failed"
                    ch["message"] = job["message"] or "Download interrompido"
            self._recount(job)
            job["status"] = ("paused" if self._closing else "cancelled") if cancelled else (
                "complete" if job["successful"] == job["total"] and not job.get("generation_failed") else "failed")
            if not cancelled and job.get("output_path") and (job["failed"] or job.get("failed_pages") or job.get("unknown_page_chapters")):
                job["status"] = "partial"
            from src.utils.comix_limits import comix_limits
            if job["manga"].get("source", "comix") == "comix" and comix_limits.reason:
                job["comix_resume_after"] = comix_limits.resume_timestamp()
                if not job.get("output_path"):
                    job.update(status="paused", message=comix_limits.reason)
                for ch in job["details"]:
                    if ch["status"] != "complete":
                        ch.update(status="paused", message=comix_limits.reason)
                self._recount(job)
        worker = self._worker
        self._worker = None
        self._active = None
        if worker is not None:
            worker.deleteLater()
        self._changed(True)
        self.busyChanged.emit()
        if job:
            self.downloadFinished.emit(job["successful"], job["failed"])
        if self._closing:
            self.shutdownReady.emit()
        else:
            QTimer.singleShot(0, self.startPending)

    @pyqtSlot(str)
    def cancelJob(self, identifier):
        job = next((j for j in self._jobs if j["id"] == identifier), None)
        if job is None:
            return
        if job is self._active:
            from src.core.downloader import cancel_downloads
            job["status"] = "cancelling"
            cancel_downloads()
        elif job["status"] == "queued":
            job["status"] = "cancelled"
            for ch in job["details"]:
                if ch["status"] != "complete":
                    ch["status"] = "cancelled"
        self._changed(True)

    @pyqtSlot(str)
    def resumeJob(self, identifier):
        job = next((j for j in self._jobs if j["id"] == identifier), None)
        if job is None or job["status"] not in ("paused", "cancelled", "failed", "partial"):
            return
        if job["manga"].get("source", "comix") == "comix":
            from src.utils.comix_limits import comix_limits, ComixBlockedError
            try:
                comix_limits.resume()
            except ComixBlockedError as exc:
                job["message"] = str(exc)
                self._changed(True)
                self.errorOccurred.emit(str(exc))
                return
        job["status"] = "queued"
        job.pop("comix_resume_after", None)
        job["message"] = ""
        for ch in job["details"]:
            if ch["status"] != "complete":
                ch.update(status="queued", current=0, message="")
        self._recount(job)
        self._changed(True)
        self.startPending()

    @pyqtSlot()
    def clearFinished(self):
        self._jobs = [job for job in self._jobs if job["status"] in ("queued", "running", "cancelling", "paused", "partial")]
        self._changed(True)

    @pyqtSlot()
    def prepareShutdown(self):
        self._closing = True
        if self._active:
            self.cancelJob(self._active["id"])
        else:
            self._save()
            self.shutdownReady.emit()
