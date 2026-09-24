import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.api import comix, comix_verification as verification


@pytest.fixture(autouse=True)
def isolated_verification(monkeypatch):
    monkeypatch.setattr(verification, "_generation", 0)
    monkeypatch.setattr(verification, "_cookies", ())
    monkeypatch.setattr(verification, "_failure", None)


class Page:
    url = "https://comix.to/title/test"

    def __init__(self, challenged=True):
        self.challenged = challenged
        self.reload = AsyncMock(side_effect=self.clear)

    def clear(self):
        self.challenged = False

    async def evaluate(self, script):
        if script == "document.title":
            return "Security check" if self.challenged else "Comix"
        if "#challenge-running" in script:
            return self.challenged
        return not self.challenged

    async def sleep(self, duration):
        await asyncio.sleep(0)


def browser():
    return SimpleNamespace(stopped=False, cookies=SimpleNamespace(set_all=AsyncMock(), get_all=AsyncMock()),
                           _comix_verification_generation=0)


def test_headless_challenge_opens_human_window_then_resumes_original_browser():
    async def run():
        hidden, page, visible = browser(), Page(), browser()
        visible_page = Page(False)
        visible.get = AsyncMock(return_value=visible_page)
        visible.cookies.get_all.return_value = ["verified-cookie"]
        with patch.object(comix, "_start_comix_browser", AsyncMock(return_value=visible)) as start, \
             patch.object(comix, "_close_comix_browser", AsyncMock()) as close, \
             patch.object(comix, "_save_comix_cookies", AsyncMock()) as save, \
             patch.object(comix, "_publish_comix_session", AsyncMock()), \
             patch.object(verification, "_cookie_parameters", side_effect=lambda values: values):
            await comix._wait_for_comix_page(page, "ready", headless=True, browser=hidden, operation="reader")
        start.assert_awaited_once_with(False)
        visible.get.assert_awaited_once_with(page.url)
        save.assert_awaited_once_with(visible)
        close.assert_awaited_once_with(visible)
        hidden.cookies.set_all.assert_awaited_once_with(["verified-cookie"])
        page.reload.assert_awaited_once()
        assert not page.challenged
        assert hidden._comix_verification_generation == 1
    asyncio.run(run())


def test_concurrent_chapters_share_one_verification_window():
    async def run():
        first, second = browser(), browser()
        visible = browser()
        visible.get = AsyncMock(return_value=Page(False))
        visible.cookies.get_all.return_value = ["verified-cookie"]
        async def human(*args):
            await asyncio.sleep(0.02)
        with patch.object(comix, "_start_comix_browser", AsyncMock(return_value=visible)) as start, \
             patch.object(comix, "_close_comix_browser", AsyncMock()) as close, \
             patch.object(comix, "_save_comix_cookies", AsyncMock()), \
             patch.object(comix, "_publish_comix_session", AsyncMock()), \
             patch.object(verification, "_cookie_parameters", side_effect=lambda values: values), \
             patch.object(verification, "_wait_for_human", side_effect=human):
            await asyncio.gather(
                verification.verify_headless_session(first, Page(), "ready", 0),
                verification.verify_headless_session(second, Page(), "ready", 0))
        start.assert_awaited_once_with(False)
        close.assert_awaited_once_with(visible)
        first.cookies.set_all.assert_awaited_once_with(["verified-cookie"])
        second.cookies.set_all.assert_awaited_once_with(["verified-cookie"])
    asyncio.run(run())


@pytest.mark.parametrize("reason", ["closed", "timeout", "cancelled"])
def test_failed_human_verification_closes_window_and_does_not_resume(reason):
    async def run():
        hidden, visible, page = browser(), browser(), Page()
        visible.get = AsyncMock(return_value=Page())
        if reason == "closed":
            visible.stopped = True
        cancelled = lambda: reason == "cancelled" and visible.get.await_count > 0
        with patch.object(comix, "_start_comix_browser", AsyncMock(return_value=visible)), \
             patch.object(comix, "_close_comix_browser", AsyncMock()) as close, \
             patch.object(verification, "MANUAL_VERIFICATION_TIMEOUT", 0 if reason == "timeout" else 1):
            with pytest.raises(InterruptedError if reason == "cancelled" else comix.ComixVerificationRequiredError):
                await verification.verify_headless_session(hidden, page, "ready", 0, cancelled)
        close.assert_awaited_once_with(visible)
        hidden.cookies.set_all.assert_not_awaited()
        page.reload.assert_not_awaited()
        assert not verification._verification_lock.locked()
        assert verification._failure
    asyncio.run(run())


def test_human_poll_does_not_accept_ready_dom_while_challenge_is_present():
    async def run():
        page = Page()
        async def user_solves():
            await asyncio.sleep(0.01)
            page.clear()
        task = asyncio.create_task(user_solves())
        await verification._wait_for_human(browser(), page, "ready", None, 1)
        await task
        assert not page.challenged
    asyncio.run(run())


def test_no_visible_browser_for_normal_pages_or_explicit_visible_mode():
    async def run():
        with patch.object(comix, "verify_headless_session", AsyncMock()) as verify:
            await comix._wait_for_comix_page(Page(False), "ready", headless=True, browser=browser(), operation="reader")
            await comix._wait_for_comix_page(Page(False), "ready", headless=False, browser=browser(), operation="reader")
        verify.assert_not_awaited()
    asyncio.run(run())


def test_cookie_transfer_uses_cdp_input_fields_and_only_comix_domains():
    def cookie(domain):
        return SimpleNamespace(name="clearance", value="test", domain=domain, path="/", secure=True,
                               http_only=True, same_site=None, expires=-1)
    result = verification._cookie_parameters([cookie(".comix.to"), cookie("unrelated.example")])
    assert len(result) == 1
    data = result[0].to_json()
    assert data["domain"] == ".comix.to"
    assert data["httpOnly"] is True
    assert "expires" not in data and "size" not in data and "session" not in data


def test_reader_does_not_reopen_verification_for_every_pending_chapter():
    from src.core.downloader import reset_downloads
    reset_downloads()
    error = comix.ComixVerificationRequiredError("Verification window closed")
    with patch.object(comix, "_start_comix_browser", AsyncMock(return_value=browser())) as start, \
         patch.object(comix, "_close_comix_browser", AsyncMock()), \
         patch.object(comix.ComixAPI, "_get_chapter_images_async", AsyncMock(side_effect=error)) as fetch:
        service = comix.ChapterReaderService(True)
        try:
            for identifier in (1, 2, 3):
                with pytest.raises(comix.ComixVerificationRequiredError):
                    service.fetch(identifier, "test", str(identifier))
        finally:
            service.close()
    assert start.await_count == 1
    assert fetch.await_count == 1
