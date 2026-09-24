import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.api.comix import ComixAPI, _discovery_step


def test_invisible_browser_reports_manual_security_check_immediately():
    from src.api.comix import _wait_for_comix_page, ComixVerificationRequiredError

    page = AsyncMock()
    page.evaluate.return_value = "Security check"
    with pytest.raises(ComixVerificationRequiredError, match="no browser session"):
        asyncio.run(_wait_for_comix_page(page, "true", headless=True, operation="discovery"))
    page.sleep.assert_not_awaited()


def test_browser_call_that_never_answers_has_a_deadline():
    async def run():
        cancelled = asyncio.Event()

        async def unresponsive():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with pytest.raises(RuntimeError, match="page / Cloudflare"):
            await _discovery_step(unresponsive(), "page / Cloudflare", 0.01)
        assert cancelled.is_set()

    asyncio.run(run())


def test_discovery_closes_browser_after_timeout():
    async def run():
        browser = AsyncMock()
        close = AsyncMock()

        async def step(awaitable, stage, timeout):
            if stage == "page / Cloudflare verification":
                awaitable.close()
                raise RuntimeError("verification timed out")
            return await awaitable

        with patch("src.api.comix._start_comix_browser", AsyncMock(return_value=browser)), \
             patch("src.api.comix._close_comix_browser", close), \
             patch("src.api.comix._discovery_step", step):
            with pytest.raises(RuntimeError, match="verification timed out"):
                await ComixAPI._get_discovery_async(headless=True)
        close.assert_awaited_once_with(browser)

    asyncio.run(run())
