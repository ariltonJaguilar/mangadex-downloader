"""Conservative download pacing and a shared stop on server refusal."""
import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import threading
import time
import re


class ComixBlockedError(RuntimeError):
    pass


def retry_after_seconds(value):
    try:
        return max(0.0, float(value))
    except (ValueError, TypeError):
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0.0


class ComixLimits:
    def __init__(self):
        self._lock = threading.Lock()
        self._next = {}
        self.reason = ""
        self.resume_at = 0.0

    def block(self, reason, retry_after=None):
        with self._lock:
            self.reason = reason
            # An unknown refusal gets a conservative local cooldown, not a
            # claim about the site's published limits. Resumption is manual.
            self.resume_at = max(self.resume_at, time.monotonic() + max(60, retry_after_seconds(retry_after)))
        return ComixBlockedError(reason)

    def resume(self):
        with self._lock:
            remaining = self.resume_at - time.monotonic()
            if self.reason and remaining > 0:
                raise ComixBlockedError(f"Aguarde mais {int(remaining) + 1}s antes de retomar o Comix.")
            self.reason = ""

    def chapter_finished(self):
        with self._lock:
            self._next["chapter"] = max(self._next.get("chapter", 0), time.monotonic() + 5)

    def resume_timestamp(self):
        with self._lock:
            return time.time() + max(0, self.resume_at - time.monotonic())

    def restore_pause(self, reason, timestamp):
        with self._lock:
            remaining = timestamp - time.time()
            if remaining > 0:
                self.reason = reason
                self.resume_at = max(self.resume_at, time.monotonic() + remaining)

    def delay(self, kind, interval):
        with self._lock:
            if self.reason:
                raise ComixBlockedError(self.reason)
            now = time.monotonic()
            remaining = self._next.get(kind, 0) - now
            if remaining <= 0:
                self._next[kind] = now + interval
                return 0.0
            return min(remaining, 0.1)

    def wait_image(self, cancelled):
        while True:
            if cancelled():
                raise InterruptedError("Download cancelled")
            delay = self.delay("image", 0.5)
            if not delay:
                return
            time.sleep(delay)

    async def wait_chapter(self, cancelled):
        while True:
            if cancelled():
                raise InterruptedError("Download cancelled")
            delay = self.delay("chapter", 5.0)
            if not delay:
                return
            await asyncio.sleep(delay)


comix_limits = ComixLimits()


async def check_blocked_page(page):
    text = await page.evaluate("document.querySelector('#cf-error-details')?.innerText || ''")
    if isinstance(text, str) and re.search(r"why have i been blocked|sorry, you have been blocked|error\s*1015", text, re.I):
        raise comix_limits.block(
            "O Comix bloqueou o acesso. Downloads pausados; aguarde antes de retomar manualmente."
        )
