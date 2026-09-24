"""A single visible, human-operated verification window for headless sessions."""
import asyncio
import threading
import time
from ..utils.comix_limits import ComixBlockedError, check_blocked_page

from ..utils.nodriver_compat import load_nodriver

MANUAL_VERIFICATION_TIMEOUT = 300.0
_verification_lock = threading.Lock()
_generation = 0
_cookies = ()
_failure = None


def generation():
    return _generation


def _check_cancelled(cancel_check):
    if cancel_check and cancel_check():
        raise InterruptedError("Download cancelled")


def _cookie_parameters(cookies):
    """Copy Comix cookies using the CDP input type, without response-only fields."""
    network = load_nodriver().cdp.network
    result = []
    for cookie in cookies:
        domain = str(cookie.domain).lstrip(".").lower()
        if domain != "comix.to" and not domain.endswith(".comix.to"):
            continue
        result.append(network.CookieParam(
            name=cookie.name, value=cookie.value, domain=cookie.domain,
            path=cookie.path, secure=cookie.secure, http_only=cookie.http_only,
            same_site=cookie.same_site,
            expires=network.TimeSinceEpoch(cookie.expires) if cookie.expires > 0 else None,
        ))
    return result


async def _wait_for_human(browser, page, ready_script, cancel_check, timeout):
    from .comix import _page_has_cloudflare_challenge, ComixVerificationRequiredError
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _check_cancelled(cancel_check)
        if browser.stopped is True:
            raise ComixVerificationRequiredError("A janela de verificação foi fechada. Tente novamente quando estiver pronto.")
        try:
            await asyncio.wait_for(check_blocked_page(page), timeout=5)
            title = await asyncio.wait_for(page.evaluate("document.title"), timeout=5)
            challenge = str(title).strip().lower() == "security check"
            challenge = challenge or await asyncio.wait_for(_page_has_cloudflare_challenge(page), timeout=5)
            if not challenge and await asyncio.wait_for(page.evaluate(ready_script), timeout=5):
                return
        except ComixBlockedError:
            raise
        except Exception:
            pass  # Navigation can briefly replace the page's execution context.
        await asyncio.sleep(0.25)
    raise ComixVerificationRequiredError("A verificação não foi concluída em 5 minutos. Tente novamente.")


async def verify_headless_session(browser, page, ready_script, observed_generation, cancel_check=None):
    """Ask the user once, then reload each waiting headless tab with that session."""
    from .comix import (_start_comix_browser, _close_comix_browser, _save_comix_cookies,
                        _publish_comix_session, ComixVerificationRequiredError, logger)
    global _generation, _cookies, _failure
    deadline = time.monotonic() + MANUAL_VERIFICATION_TIMEOUT + 60
    while not _verification_lock.acquire(blocking=False):
        _check_cancelled(cancel_check)
        if time.monotonic() >= deadline:
            raise ComixVerificationRequiredError("Outra verificação ainda está em andamento. Tente novamente.")
        await asyncio.sleep(0.1)
    try:
        _check_cancelled(cancel_check)
        if _generation == observed_generation:
            visible_browser = None
            _cookies = ()
            _failure = "A verificação foi interrompida. Tente novamente."
            try:
                logger.warning("Verificação necessária: resolva o captcha na janela do Chrome. O download continuará em segundo plano.")
                visible_browser = await asyncio.wait_for(_start_comix_browser(False), timeout=30)
                # Navigate to the exact challenged page, never simulate solving a challenge.
                visible_page = await asyncio.wait_for(visible_browser.get(page.url), timeout=30)
                await _wait_for_human(visible_browser, visible_page, ready_script, cancel_check,
                                      MANUAL_VERIFICATION_TIMEOUT)
                cookies = await asyncio.wait_for(visible_browser.cookies.get_all(), timeout=10)
                _cookies = tuple(_cookie_parameters(cookies))
                await asyncio.wait_for(_save_comix_cookies(visible_browser), timeout=10)
                await asyncio.wait_for(_publish_comix_session(visible_browser, visible_page), timeout=10)
                _failure = None
            except InterruptedError:
                raise
            except Exception as exc:
                _failure = f"Não foi possível concluir a verificação: {exc}"
                raise ComixVerificationRequiredError(_failure) from exc
            finally:
                _generation += 1
                await _close_comix_browser(visible_browser)
        elif _failure:
            raise ComixVerificationRequiredError(_failure)
        _check_cancelled(cancel_check)
        try:
            await asyncio.wait_for(browser.cookies.set_all(list(_cookies)), timeout=10)
            await asyncio.wait_for(page.reload(), timeout=30)
        except Exception as exc:
            raise ComixVerificationRequiredError(f"Não foi possível retomar a sessão em segundo plano: {exc}") from exc
        browser._comix_verification_generation = _generation
        logger.info("Verificação concluída. Continuando com o navegador headless.")
    finally:
        _verification_lock.release()
