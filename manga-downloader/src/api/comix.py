"""
Comix.to API wrapper for manga information and chapter data.
"""

import json
import re
import asyncio
import os
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from ..utils.retry import retry_with_backoff
from ..utils.logger import get_logger
from ..utils.hash import generate_comix_hash
from ..utils.nodriver_browser import start_browser
from ..utils.nodriver_compat import load_cdp_page
from ..utils.comix_session import publish_comix_credentials
from .comix_verification import MANUAL_VERIFICATION_TIMEOUT, generation, verify_headless_session
from ..utils.comix_limits import ComixBlockedError, comix_limits, check_blocked_page

logger = get_logger(__name__)

# Global lock to synchronize browser creation and cookie loading/saving across threads
_browser_lock = threading.Lock()

_CANONICAL_COOKIE_FILE = Path(__file__).resolve().parents[2] / "cf_cookies.dat"
_CLOUDFLARE_TIMEOUT_SECONDS = 60.0
_CLOUDFLARE_POLL_SECONDS = 0.25
_CLOUDFLARE_TITLE = "just a moment..."
_INITIAL_DATA_TIMEOUT_SECONDS = 15.0
_INITIAL_DATA_POLL_SECONDS = 0.05
_CHAPTER_EXTRACTION_MIN_SECONDS = 180.0
_CHAPTER_EXTRACTION_MAX_SECONDS = 900.0


async def _discovery_step(awaitable, stage: str, timeout: float):
    """Bound browser calls themselves, not only the loop around those calls."""
    logger.info("Discovery stage: %s (timeout=%ss)", stage, timeout)
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            f"Comix did not respond within {timeout:g}s during {stage}. "
            "Try again, or disable Headless in Settings to inspect the browser."
        ) from exc


class MangaInfoFetchError(RuntimeError):
    """Raised when Comix does not expose a complete manga detail payload."""


class ComixPageUnavailableError(RuntimeError):
    """Raised when a valid browser response is a terminal not-found page."""


class ComixVerificationRequiredError(RuntimeError):
    """The website requires a human check in a visible browser."""


def _cookie_file_candidates() -> list[Path]:
    """Return canonical and legacy cookie locations without duplicating paths."""
    candidates = [_CANONICAL_COOKIE_FILE]
    legacy = Path.cwd() / "cf_cookies.dat"
    try:
        same_file = legacy.resolve() == _CANONICAL_COOKIE_FILE.resolve()
    except OSError:
        same_file = legacy == _CANONICAL_COOKIE_FILE
    if not same_file:
        candidates.append(legacy)
    return candidates


async def _start_comix_browser(headless: bool):
    """Start a browser and load the shared Comix cookie jar if available."""
    _browser_lock.acquire()
    try:
        browser = await start_browser(headless)
        for cookie_file in _cookie_file_candidates():
            if not cookie_file.exists():
                continue
            try:
                await browser.cookies.load(str(cookie_file))
                logger.info("Loaded cookies from %s", cookie_file)
                break
            except Exception as exc:
                logger.warning("Failed loading cookies from %s: %s", cookie_file, exc)
        browser._comix_verification_generation = generation()
        return browser
    finally:
        _browser_lock.release()


async def _close_comix_browser(browser) -> None:
    """Close the CDP connection before terminating Chrome.

    nodriver's ``stop`` schedules ``aclose`` as a background task.  The task
    can be lost when the ``asyncio.run`` loop closes, leaving a child process or
    websocket behind.  Await it here and retain ``stop`` as the final fallback
    for compatible nodriver facades and test doubles.
    """
    if browser is None:
        return
    aclose = getattr(browser, "aclose", None)
    if callable(aclose):
        try:
            await asyncio.wait_for(aclose(), timeout=3.0)
        except Exception as exc:
            logger.debug("Browser connection close failed: %s", exc)
    stop = getattr(browser, "stop", None)
    if callable(stop):
        try:
            stop()
        except Exception as exc:
            logger.debug("Browser process stop failed: %s", exc)


async def _save_comix_cookies(browser) -> None:
    """Persist browser cookies atomically after a verified Comix page."""
    cookie_file = _CANONICAL_COOKIE_FILE
    cookie_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = cookie_file.with_name(
        f".{cookie_file.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    _browser_lock.acquire()
    try:
        try:
            await browser.cookies.save(str(temporary_file), pattern=".*")
            os.replace(temporary_file, cookie_file)
            logger.info("Saved cookies to %s", cookie_file)
        except Exception as exc:
            logger.warning("Failed saving cookies to %s: %s", cookie_file, exc)
    finally:
        _browser_lock.release()
        try:
            temporary_file.unlink(missing_ok=True)
        except OSError:
            pass


async def _publish_comix_session(browser, page) -> None:
    """Expose the verified browser identity to the GUI cover provider."""
    try:
        user_agent = await page.evaluate("navigator.userAgent")
        cookies = await browser.cookies.get_all()
        credentials = publish_comix_credentials(user_agent, cookies)
        logger.debug(
            "Published Comix image session (generation=%s, cookies=%s)",
            credentials.generation,
            len(credentials.cookies),
        )
    except Exception as exc:
        # Cover loading is an enhancement for GUI callers; API data should keep
        # working if a browser facade does not expose cookie metadata.
        logger.warning("Could not publish Comix image session: %s", exc)


async def _page_has_cloudflare_challenge(page) -> bool:
    """Identify Cloudflare's challenge page without false title matches."""
    title = await page.evaluate("document.title")
    title = title if isinstance(title, str) else ""
    if title.strip().lower() == _CLOUDFLARE_TITLE:
        return True
    try:
        marker = await page.evaluate(
            "Boolean(document.querySelector('#challenge-running, #challenge-stage, form#challenge-form'))"
        )
        return marker is True
    except Exception:
        return False


async def _wait_for_comix_page(
    page,
    ready_script: str,
    *,
    headless: bool,
    operation: str,
    timeout: float = _CLOUDFLARE_TIMEOUT_SECONDS,
    unavailable_script: str | None = None,
    browser=None,
    cancel_check=None,
) -> None:
    """Wait for Cloudflare verification and operation-specific DOM readiness."""
    deadline = time.monotonic() + timeout
    saw_challenge = False
    last_title = ""
    observed_generation = getattr(browser, "_comix_verification_generation", generation())
    if not isinstance(observed_generation, int):
        observed_generation = generation()
    verification_attempted = False
    challenge_since = None
    while time.monotonic() < deadline:
        if cancel_check and cancel_check():
            raise InterruptedError("Download cancelled")
        try:
            await check_blocked_page(page)
            last_title = (await page.evaluate("document.title")) or ""
            manual_challenge = str(last_title).strip().lower() == "security check"
            if manual_challenge:
                if headless and browser is None:
                    raise ComixVerificationRequiredError(
                        "Comix requires manual verification (Security check), but no browser session is available."
                    )
                if not saw_challenge:
                    logger.warning("Comix requires manual verification. Complete it in the Chrome window.")
                saw_challenge = True
            challenge = manual_challenge or await _page_has_cloudflare_challenge(page)
            saw_challenge = saw_challenge or challenge
            if challenge:
                if challenge_since is None:
                    challenge_since = time.monotonic()
                # Brief automatic checks can finish without showing a window.
                if headless and browser is not None and not verification_attempted and (
                        manual_challenge or time.monotonic() - challenge_since >= 8):
                    verification_attempted = True
                    await verify_headless_session(browser, page, ready_script, observed_generation, cancel_check)
                    deadline = time.monotonic() + timeout
                    continue
            else:
                challenge_since = None
            if unavailable_script and bool(await page.evaluate(unavailable_script)):
                raise ComixPageUnavailableError(
                    f"Comix {operation} is unavailable (the requested route was not found)"
                )
            ready = bool(await page.evaluate(ready_script))
            if ready and not challenge:
                return
        except (ComixPageUnavailableError, ComixVerificationRequiredError, ComixBlockedError, InterruptedError):
            raise
        except Exception:
            pass
        await page.sleep(_CLOUDFLARE_POLL_SECONDS)

    if saw_challenge:
        if verification_attempted:
            raise ComixVerificationRequiredError(
                "O site voltou a pedir verificação após resolver o captcha. Tente novamente."
            )
        mode = "headless" if headless else "headful"
        raise RuntimeError(
            f"Comix Cloudflare verification did not complete within {int(timeout)} seconds "
            f"while fetching {operation} ({mode} mode; last title: {last_title!r})"
        )
    raise RuntimeError(
        f"Comix page did not become ready within {int(timeout)} seconds while fetching {operation}"
    )


def _find_manga_detail(data: object, manga_code: str) -> Optional[dict]:
    """Return the requested manga detail query from parsed initial-data."""
    if not isinstance(data, dict):
        return None

    queries = data.get("queries")
    if not isinstance(queries, dict):
        return None

    for key, value in queries.items():
        key_text = key if isinstance(key, str) else str(key)
        if (
            "manga" in key_text.lower()
            and "detail" in key_text.lower()
            and manga_code in key_text
            and isinstance(value, dict)
        ):
            return value
    return None


async def _wait_for_initial_data(
    page,
    *,
    operation: str,
    manga_code: Optional[str] = None,
    timeout: float = _INITIAL_DATA_TIMEOUT_SECONDS,
) -> dict:
    """Wait until Comix's streamed initial-data script contains valid JSON.

    The script element is inserted before its body is necessarily complete.  A
    non-empty value is therefore not a sufficient readiness signal; parsing and
    validating the expected query is the completion condition.
    """
    deadline = time.monotonic() + timeout
    last_length = 0
    last_error = "no payload observed"

    while time.monotonic() < deadline:
        try:
            raw_data = await page.evaluate(
                "document.getElementById('initial-data') ? "
                "document.getElementById('initial-data').textContent : null"
            )
            if isinstance(raw_data, str):
                last_length = len(raw_data)
                if raw_data.strip():
                    try:
                        parsed = json.loads(raw_data)
                    except json.JSONDecodeError as exc:
                        last_error = f"JSON {exc.msg} at position {exc.pos}"
                    else:
                        if not isinstance(parsed, dict):
                            last_error = f"root value is {type(parsed).__name__}, not an object"
                        elif not isinstance(parsed.get("queries"), dict):
                            last_error = "queries is missing or is not an object"
                        elif manga_code is not None and _find_manga_detail(parsed, manga_code) is None:
                            last_error = f"manga detail query for {manga_code} is not present yet"
                        else:
                            return parsed
        except Exception as exc:
            last_error = f"page read failed: {type(exc).__name__}"

        await page.sleep(_INITIAL_DATA_POLL_SECONDS)

    code_suffix = f" for {manga_code}" if manga_code else ""
    raise MangaInfoFetchError(
        f"Comix initial data did not become valid while fetching {operation}{code_suffix} "
        f"within {timeout:g} seconds (last payload length: {last_length}; {last_error})"
    )


@dataclass
class ChapterImageFetchReport:
    """Images extracted from a chapter reader page, plus extraction diagnostics."""

    image_urls: list[str]
    page_count: int = 0
    skipped_pages: list[int] = field(default_factory=list)
    failed_pages: list[int] = field(default_factory=list)
    page_numbers: list[int] = field(default_factory=list)

    @property
    def expected_image_count(self) -> int:
        if self.page_count <= 0:
            return len(self.image_urls)
        return max(0, self.page_count - len(self.skipped_pages))


def run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


class ComixAPI:
    """API wrapper for comix.to"""
    
    BASE_URL = "https://comix.to/api/v2"
    CHAPTERS_PAGE_LIMIT = 100
    MAX_CHAPTER_PAGES = 200
    DISCOVERY_PAGE_LIMIT = 20
    DISCOVERY_HIGHLIGHT_LIMIT = 8
    DISCOVERY_CONTENT_RATINGS = ("safe", "suggestive", "erotica", "pornographic")
    
    @staticmethod
    def extract_manga_code(url: str) -> str:
        """
        Extract manga code from the title URL.
        Example: https://comix.to/title/93q1r-the-summoner -> 93q1r
        """
        parts = url.rstrip("/").split("/")
        last = parts[-1] if parts[-1] else parts[-2]
        code = last.split("-")[0]
        logger.debug(f"Extracted manga code: {code} from URL: {url}")
        return code

    @classmethod
    def _normalize_manga_summary(cls, item: dict):
        """Normalize a catalog item returned by Comix's manga API."""
        from ..core.models import MangaSummary

        if not isinstance(item, dict):
            return None

        title = cls._first_present(item, "title", "name")
        if not title:
            return None

        raw_url = cls._first_present(item, "url", "canonical_url", "canonicalUrl") or ""
        if raw_url.startswith("/"):
            canonical_url = f"https://comix.to{raw_url}"
        elif raw_url.startswith("http://") or raw_url.startswith("https://"):
            canonical_url = raw_url
        else:
            canonical_url = ""

        manga_code = cls._first_present(item, "hid", "hash_id", "hashId", "manga_code", "code") or ""
        if not manga_code and canonical_url:
            try:
                manga_code = cls.extract_manga_code(canonical_url)
            except (IndexError, AttributeError):
                manga_code = ""
        if not manga_code:
            return None

        poster = cls._first_present(item, "poster", "cover") or {}
        if isinstance(poster, dict):
            poster_url = cls._first_present(poster, "large", "medium", "small") or ""
        else:
            poster_url = poster if isinstance(poster, str) else ""

        manga_id = cls._first_present(item, "id", "manga_id", "mangaId")
        try:
            manga_id = int(manga_id) if manga_id is not None else None
        except (TypeError, ValueError):
            manga_id = None

        year = cls._first_present(item, "year", "startDateYear")
        try:
            year = int(year) if year not in (None, "") else None
        except (TypeError, ValueError):
            year = None

        rated_avg = cls._first_present(item, "ratedAvg", "rated_avg", "score")
        try:
            rated_avg = float(rated_avg) if rated_avg not in (None, "") else None
        except (TypeError, ValueError):
            rated_avg = None

        content_rating = str(
            cls._first_present(item, "contentRating", "content_rating") or "safe"
        ).lower()

        if not canonical_url:
            canonical_url = f"https://comix.to/title/{manga_code}"

        return MangaSummary(
            manga_id=manga_id,
            manga_code=str(manga_code),
            title=str(title),
            poster_url=str(poster_url),
            manga_type=cls._first_present(item, "type", "manga_type"),
            status=cls._first_present(item, "status"),
            year=year,
            latest_chapter=str(
                cls._first_present(item, "latestChapter", "latest_chapter")
                or ""
            ),
            rated_avg=rated_avg,
            content_rating=content_rating,
            canonical_url=canonical_url,
        )

    @classmethod
    def _normalize_manga_browse_page(cls, payload: dict, requested_page: int = 1):
        """Normalize a list response into the GUI-facing browse page model."""
        from ..core.models import MangaBrowsePage

        if not isinstance(payload, dict):
            raise RuntimeError("Comix returned an invalid manga list response")

        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raw_items = payload.get("data") if isinstance(payload.get("data"), list) else []

        items = []
        seen_codes = set()
        for item in raw_items:
            summary = cls._normalize_manga_summary(item)
            if summary and summary.manga_code not in seen_codes:
                seen_codes.add(summary.manga_code)
                items.append(summary)

        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        try:
            page = max(1, int(meta.get("page", requested_page)))
        except (TypeError, ValueError):
            page = max(1, requested_page)
        try:
            last_page = max(page, int(meta.get("lastPage", page)))
        except (TypeError, ValueError):
            last_page = page + 1 if meta.get("hasNext") else page
        try:
            total = max(0, int(meta.get("total", len(items))))
        except (TypeError, ValueError):
            total = len(items)

        return MangaBrowsePage(items=items, page=page, last_page=last_page, total=total)

    @classmethod
    async def _fetch_discovery_via_page_api(
        cls,
        page,
        *,
        keyword: str = "",
        page_number: int = 1,
        limit: int = DISCOVERY_PAGE_LIMIT,
        highlights: bool = False,
    ) -> dict:
        """Fetch catalog data through Comix's own in-page API client."""
        script = f"""(async () => {{
            const keyword = {json.dumps(keyword)};
            const pageNumber = {int(page_number)};
            const limit = {int(limit)};
            const ratings = {json.dumps(list(cls.DISCOVERY_CONTENT_RATINGS))};
            const highlights = {str(bool(highlights)).lower()};

            async function resolveEnvModule() {{
                const moduleScripts = Array.from(document.querySelectorAll('script[type="module"][src]'))
                    .map((script) => script.src);

                for (const scriptUrl of moduleScripts) {{
                    try {{
                        const response = await fetch(scriptUrl, {{ credentials: 'same-origin' }});
                        if (!response.ok) continue;
                        const source = await response.text();
                        const matches = Array.from(source.matchAll(/from\\s*["']\\.\\/(env-[^"']+\\.js)["']/g));
                        for (const match of matches) {{
                            const moduleUrl = new URL(match[1], scriptUrl).href;
                            try {{ return await import(moduleUrl); }} catch (e) {{}}
                        }}
                    }} catch (e) {{}}
                }}
                throw new Error('Comix API module not found');
            }}

            function findMangaApi(module) {{
                for (const value of Object.values(module)) {{
                    if (value && typeof value === 'object' &&
                        typeof value.list === 'function' && typeof value.top === 'function') {{
                        return value;
                    }}
                }}
                return null;
            }}

            const module = await resolveEnvModule();
            const api = findMangaApi(module);
            if (!api) throw new Error('Comix manga API export not found');

            if (highlights) {{
                const trending = await api.top({{
                    type: 'trending', days: 7, limit, content_rating: ratings,
                }});
                const latest = await api.list({{
                    order: {{ chapter_updated_at: 'desc' }},
                    page: 1, limit, content_rating: ratings,
                }});
                return JSON.stringify({{ ok: true, trending, latest }});
            }}

            const data = await api.list({{
                keyword: keyword,
                order: {{ relevance: 'desc' }},
                page: pageNumber,
                limit,
                content_rating: ratings,
            }});
            return JSON.stringify({{ ok: true, data }});
        }})().catch((error) => JSON.stringify({{
            ok: false,
            error: error && error.message ? error.message : String(error),
        }}))"""

        result_str = await page.evaluate(
            script,
            await_promise=True,
            return_by_value=True,
        )
        if not isinstance(result_str, str) or not result_str:
            raise RuntimeError("Comix discovery API returned no serialized result")
        try:
            result = json.loads(result_str)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Comix discovery API returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise RuntimeError("Comix discovery API returned an invalid response object")
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "Unknown Comix discovery API error")
        return result

    @classmethod
    async def _get_discovery_async(
        cls,
        *,
        keyword: str = "",
        page_number: int = 1,
        limit: int = DISCOVERY_PAGE_LIMIT,
        highlights: bool = False,
        headless: bool,
    ) -> dict:
        logger.info("Discovery: starting Chrome (headless=%s)", headless)
        browser = await _discovery_step(_start_comix_browser(headless), "browser startup", 30)

        try:
            logger.info("Discovery: navigating to Comix browse page")
            page = await _discovery_step(browser.get("https://comix.to/browse"), "navigation", 30)
            logger.info("Discovery: waiting for page / Cloudflare")
            await _discovery_step(_wait_for_comix_page(
                page,
                "Boolean(document.getElementById('initial-data'))",
                headless=headless,
                operation="discovery",
                browser=browser,
                timeout=60 if headless else 180,
            ), "page / Cloudflare verification", MANUAL_VERIFICATION_TIMEOUT + 240 if headless else 185)
            await _discovery_step(_publish_comix_session(browser, page), "browser session", 10)
            await _discovery_step(_save_comix_cookies(browser), "cookie storage", 10)

            logger.info("Discovery: requesting catalog through page API")
            result = await _discovery_step(cls._fetch_discovery_via_page_api(
                page,
                keyword=keyword,
                page_number=page_number,
                limit=limit,
                highlights=highlights,
            ), "catalog response", 30)
            return result
        finally:
            await _close_comix_browser(browser)

    @classmethod
    def search_manga(
        cls,
        keyword: str,
        page: int = 1,
        limit: int = DISCOVERY_PAGE_LIMIT,
        headless: Optional[bool] = None,
    ):
        """Search the Comix catalog for manga summaries."""
        from ..core.models import MangaBrowsePage
        from ..utils.config import ConfigManager

        keyword = (keyword or "").strip()
        if not keyword:
            return MangaBrowsePage()
        if headless is None:
            headless = ConfigManager().get("headless", True)
        page = max(1, int(page))
        try:
            result = run_async(
                cls._get_discovery_async(
                    keyword=keyword,
                    page_number=page,
                    limit=limit,
                    highlights=False,
                    headless=headless,
                )
            )
            return cls._normalize_manga_browse_page(result.get("data", {}), page)
        except Exception:
            logger.exception("Discovery search failed for %r", keyword)
            raise

    @classmethod
    def get_manga_highlights(
        cls,
        limit: int = DISCOVERY_HIGHLIGHT_LIMIT,
        headless: Optional[bool] = None,
    ) -> dict[str, list]:
        """Fetch compact trending and latest catalog rails for the GUI."""
        from ..utils.config import ConfigManager

        if headless is None:
            headless = ConfigManager().get("headless", True)
        try:
            result = run_async(
                cls._get_discovery_async(
                    limit=limit,
                    highlights=True,
                    headless=headless,
                )
            )
            trending = result.get("trending", [])
            latest_payload = result.get("latest", {})
            if not isinstance(trending, list):
                trending = []
            if not isinstance(latest_payload, dict):
                latest_payload = {}
            latest = latest_payload.get("items", [])
            return {
                "trending": [
                    summary for item in trending
                    if (summary := cls._normalize_manga_summary(item)) is not None
                ],
                "latest": [
                    summary for item in latest
                    if (summary := cls._normalize_manga_summary(item)) is not None
                ],
            }
        except Exception:
            logger.exception("Discovery highlights failed")
            raise
    
    @classmethod
    async def _get_manga_info_async(cls, manga_code: str, headless: bool) -> dict:
        url = f"https://comix.to/title/{manga_code}"
        browser = await _start_comix_browser(headless)
        
        try:
            page = await browser.get(url)
            await _wait_for_comix_page(
                page,
                "Boolean(document.getElementById('initial-data'))",
                headless=headless,
                operation="manga details",
                browser=browser,
            )
            initial_data = await _wait_for_initial_data(
                page,
                operation="manga details",
                manga_code=manga_code,
            )
            await _publish_comix_session(browser, page)
            await _save_comix_cookies(browser)
            return initial_data
            
        finally:
            await _close_comix_browser(browser)
            
    @classmethod
    def get_manga_info(cls, manga_code: str, headless: Optional[bool] = None):
        """Fetch manga information from DOM using nodriver."""
        from ..core.models import MangaInfo
        if headless is None:
            from ..utils.config import ConfigManager
            headless = ConfigManager().get("headless", True)
            
        logger.info(f"Fetching manga info using nodriver (headless={headless}) for {manga_code}...")
        
        try:
            json_data = run_async(cls._get_manga_info_async(manga_code, headless))
        except MangaInfoFetchError as exc:
            logger.error("nodriver failed to fetch manga info for %s: %s", manga_code, exc)
            raise
        except Exception as exc:
            logger.exception("nodriver failed to fetch manga info for %s", manga_code)
            raise MangaInfoFetchError(
                f"Could not fetch manga information for {manga_code}: {exc}"
            ) from exc

        # Find the manga detail query in the json_data
        manga_detail = _find_manga_detail(json_data, manga_code)
        if not manga_detail:
            queries = json_data.get("queries", {}) if isinstance(json_data, dict) else {}
            keys = list(queries.keys()) if isinstance(queries, dict) else []
            raise MangaInfoFetchError(
                f"Comix initial data did not contain manga details for {manga_code} "
                f"(query keys: {keys})"
            )
            
        # Get alt titles safely
        alt_titles = manga_detail.get("altTitles", [])
        if not isinstance(alt_titles, list):
            alt_titles = [alt_titles] if alt_titles else []
            
        # Poster URL
        poster = manga_detail.get("poster") or {}
        poster_url = None
        if isinstance(poster, dict):
            poster_url = poster.get("large") or poster.get("medium")
            
        genres = []
        for g in manga_detail.get("genres", []):
            if isinstance(g, dict) and "title" in g:
                genres.append(g["title"])
            elif isinstance(g, str):
                genres.append(g)

        authors = manga_detail.get("authors") or manga_detail.get("author") or []
        if not isinstance(authors, list):
            authors = [authors]
        author_names = [a.get("name") or a.get("title") or "" if isinstance(a, dict)
                        else str(a) for a in authors]
        return MangaInfo(
            manga_id=manga_detail.get("id"),
            hash_id=manga_detail.get("hid"),
            title=manga_detail.get("title", "Unknown"),
            author=", ".join(dict.fromkeys(name for name in author_names if name)),
            alt_titles=alt_titles,
            slug=manga_detail.get("url", "").split("/")[-1] if manga_detail.get("url") else None,
            rank=manga_detail.get("rank"),
            manga_type=manga_detail.get("type"),
            poster_url=poster_url,
            original_language=manga_detail.get("originalLanguage"),
            status=manga_detail.get("status"),
            final_chapter=str(manga_detail.get("finalChapter") or 0),
            latest_chapter=str(manga_detail.get("latestChapter") or 0),
            start_date=manga_detail.get("startDate"),
            end_date=manga_detail.get("endDate"),
            rated_avg=manga_detail.get("ratedAvg"),
            rated_count=manga_detail.get("ratedCount"),
            follows_total=manga_detail.get("followsTotal"),
            is_nsfw=manga_detail.get("contentRating") == "nsfw",
            year=manga_detail.get("year"),
            genres=genres,
            description=manga_detail.get("synopsis", "")
        )

    @staticmethod
    def _first_present(data: dict, *keys: str):
        """Return the first non-empty value from a dict."""
        for key in keys:
            value = data.get(key)
            if value is not None:
                return value
        return None

    @classmethod
    def _normalize_chapter_api_item(cls, item: dict) -> Optional[dict]:
        """Normalize a Comix chapter API item to the app's internal row shape."""
        if not isinstance(item, dict):
            return None

        chapter_id = cls._first_present(item, "id", "chapter_id", "chapterId")
        number = cls._first_present(item, "number", "chap", "chapter")
        if chapter_id is None or number is None:
            return None

        group = cls._first_present(item, "group", "scanlation_group", "scanlationGroup")
        group_name = cls._first_present(item, "group_name", "groupName")
        if isinstance(group, dict):
            group_name = group.get("name") or group.get("title") or group_name
        if not group_name and cls._first_present(item, "isOfficial", "is_official"):
            group_name = "Official"

        try:
            chapter_id = int(chapter_id)
        except (TypeError, ValueError):
            return None

        return {
            "chapter_id": chapter_id,
            "number": str(number),
            "title": cls._first_present(item, "name", "title") or "",
            "volume": cls._first_present(item, "volume", "vol"),
            "votes": cls._first_present(item, "votes", "vote_count", "voteCount") or 0,
            "group_name": group_name,
            "pages_count": cls._first_present(item, "pages_count", "pagesCount") or 0,
        }

    @classmethod
    def _normalize_chapter_dom_row(cls, row: dict) -> Optional[dict]:
        """Normalize a chapter row scraped from the rendered chapter list."""
        href = row.get("href") if isinstance(row, dict) else None
        if not href:
            return None

        # Parse `/title/{slug}/{chap_id}-chapter-{chap_num}`
        match = re.match(r".*/title/[^/]+/(\d+)-chapter-(.+)$", href)
        if not match:
            return None

        chapter_id, chapter_number = match.group(1), match.group(2)
        group = row.get("group")
        if not group and row.get("group_official"):
            group = "Official"

        return {
            "chapter_id": int(chapter_id),
            "number": chapter_number,
            "title": row.get("title") or "",
            "volume": None,
            "votes": 0,
            "group_name": group,
            "pages_count": 0,
        }

    @staticmethod
    def _dedupe_chapter_rows(rows: list[dict]) -> list[dict]:
        """Keep the first row for each chapter id."""
        seen_ids = set()
        unique_rows = []
        for row in rows:
            chapter_id = row.get("chapter_id")
            if chapter_id in seen_ids:
                continue
            seen_ids.add(chapter_id)
            unique_rows.append(row)
        return unique_rows

    @staticmethod
    def _manga_has_chapters(initial_data: dict, manga_code: str) -> Optional[bool]:
        """Read hasChapters from already validated initial-data."""
        detail = _find_manga_detail(initial_data, manga_code)
        if not detail or not isinstance(detail.get("hasChapters"), bool):
            return None
        return detail["hasChapters"]

    @classmethod
    async def _fetch_chapters_via_page_api(cls, page, manga_code: str) -> list[dict]:
        """
        Fetch chapters from Comix's in-page API client.

        The API is currently guarded by an obfuscated token/signing layer. Running
        this inside the loaded page reuses Comix's own client and avoids copying
        brittle signing code into the downloader.
        """
        script = f"""(async () => {{
            const mangaCode = {json.dumps(manga_code)};
            const limit = {cls.CHAPTERS_PAGE_LIMIT};
            const maxPages = {cls.MAX_CHAPTER_PAGES};

            async function resolveEnvModule() {{
                const moduleScripts = Array.from(document.querySelectorAll('script[type="module"][src]'))
                    .map((script) => script.src);

                for (const scriptUrl of moduleScripts) {{
                    try {{
                        const response = await fetch(scriptUrl, {{ credentials: 'same-origin' }});
                        if (!response.ok) continue;
                        const source = await response.text();
                        const matches = Array.from(source.matchAll(/from\\s*["']\\.\\/(env-[^"']+\\.js)["']/g));
                        for (const match of matches) {{
                            const moduleUrl = new URL(match[1], scriptUrl).href;
                            try {{
                                return await import(moduleUrl);
                            }} catch (e) {{}}
                        }}
                    }} catch (e) {{}}
                }}

                throw new Error('Comix API module not found');
            }}

            function findMangaApi(module) {{
                for (const value of Object.values(module)) {{
                    if (value && typeof value === 'object' && typeof value.chapters === 'function') {{
                        return value;
                    }}
                }}
                return null;
            }}

            const module = await resolveEnvModule();
            const api = findMangaApi(module);
            if (!api) throw new Error('Comix manga API export not found');

            const items = [];
            const seenIds = new Set();

            for (let page = 1; page <= maxPages; page += 1) {{
                const data = await api.chapters(mangaCode, {{
                    page,
                    limit,
                    order: {{ number: 'desc' }},
                }});

                const pageItems = Array.isArray(data && data.items) ? data.items : [];
                for (const item of pageItems) {{
                    const id = item && (item.id ?? item.chapter_id ?? item.chapterId);
                    if (id == null || seenIds.has(String(id))) continue;
                    seenIds.add(String(id));
                    items.push(item);
                }}

                const meta = (data && data.meta) || {{}};
                if (
                    pageItems.length === 0 ||
                    meta.hasNext === false ||
                    (Number(meta.lastPage) > 0 && page >= Number(meta.lastPage))
                ) {{
                    break;
                }}
            }}

            return JSON.stringify({{ ok: true, items }});
        }})().catch((error) => JSON.stringify({{
            ok: false,
            error: error && error.message ? error.message : String(error),
        }}))"""

        # nodriver does not await JavaScript promises by default. Without this
        # flag the async IIFE returns before it resolves and evaluate() yields
        # no serialized value, which used to become the misleading "Unknown
        # Comix page API error" and trigger the timing-sensitive DOM fallback.
        result_str = await page.evaluate(
            script,
            await_promise=True,
            return_by_value=True,
        )
        if not isinstance(result_str, str) or not result_str:
            raise RuntimeError("Comix page API evaluation returned no serialized result")
        try:
            result = json.loads(result_str)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Comix page API returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise RuntimeError("Comix page API returned an invalid response object")
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "Unknown Comix page API error")

        rows = []
        for item in result.get("items", []):
            row = cls._normalize_chapter_api_item(item)
            if row:
                rows.append(row)
        return cls._dedupe_chapter_rows(rows)
    
    @classmethod
    async def _get_all_chapters_async(cls, manga_code: str, headless: bool) -> list[dict]:
        url = f"https://comix.to/title/{manga_code}"
        browser = await _start_comix_browser(headless)
                
        scrape_js = """(() => {
            const rows = Array.from(document.querySelectorAll('.mchap-item')).map(li => {
                const a = li.querySelector('.mchap-row__primary');
                const ch = li.querySelector('.mchap-row__ch');
                const ti = li.querySelector('.mchap-row__title');
                const gp = li.querySelector('.mchap-row__group');
                return {
                    href: a ? a.getAttribute('href') : null,
                    chap_label: ch ? ch.textContent.trim() : null,
                    title: ti ? ti.textContent.trim() : null,
                    group: gp ? (gp.querySelector('span') ? gp.querySelector('span').textContent.trim() : gp.textContent.trim()) : null,
                    group_official: gp ? gp.classList.contains('is-official') : false,
                };
            });
            return JSON.stringify(rows);
        })()"""
        
        all_rows = []
        seen_ids = set()
        
        try:
            page = await browser.get(url)
            await _wait_for_comix_page(
                page,
                "Boolean(document.getElementById('initial-data'))",
                headless=headless,
                operation="chapter listing",
                browser=browser,
            )
            initial_data = await _wait_for_initial_data(
                page,
                operation="chapter listing",
                manga_code=manga_code,
            )
            await _publish_comix_session(browser, page)
            await _save_comix_cookies(browser)

            has_chapters = cls._manga_has_chapters(initial_data, manga_code)

            try:
                api_rows = await cls._fetch_chapters_via_page_api(page, manga_code)
                if api_rows:
                    logger.info(f"Fetched {len(api_rows)} chapters using Comix page API")
                    return api_rows
                logger.warning("Comix page API returned no chapters; falling back to DOM scraping")
            except Exception as e:
                logger.warning(f"Comix page API chapter fetch failed: {e}. Falling back to DOM scraping")
            
            prev_first_href = None
            consecutive_dup_pages = 0
            
            for page_n in range(1, cls.MAX_CHAPTER_PAGES + 1):
                page_url = f"{url}?page={page_n}"
                if page_n > 1 or page_url != page.url:
                    await page.get(page_url)
                    
                rows = []
                for _ in range(20):
                    rows_str = await page.evaluate(scrape_js)
                    rows = json.loads(rows_str) if rows_str else []
                    if rows:
                        if prev_first_href is None or rows[0].get("href") != prev_first_href:
                            break
                    await page.sleep(0.2)
                
                if not rows:
                    break
                    
                prev_first_href = rows[0].get("href")
                page_added = 0
                
                for row in rows:
                    href = row.get("href")
                    if not href:
                        continue
                    
                    normalized = cls._normalize_chapter_dom_row(row)
                    if not normalized:
                        continue

                    if normalized["chapter_id"] in seen_ids:
                        continue

                    seen_ids.add(normalized["chapter_id"])
                    all_rows.append(normalized)
                    page_added += 1
                    
                if page_added == 0:
                    consecutive_dup_pages += 1
                    if consecutive_dup_pages >= 2:
                        break
                else:
                    consecutive_dup_pages = 0
            
            all_rows = cls._dedupe_chapter_rows(all_rows)
            if not all_rows and has_chapters:
                raise RuntimeError("Comix reports chapters exist, but no chapter rows could be fetched")

            return all_rows
        finally:
            await _close_comix_browser(browser)

    @classmethod
    def get_all_chapters(cls, manga_code: str, headless: Optional[bool] = None) -> list[any]:
        """Fetch all chapters for a manga using Comix's page API with DOM fallback."""
        from ..core.models import Chapter
        if headless is None:
            from ..utils.config import ConfigManager
            headless = ConfigManager().get("headless", True)
            
        logger.info(f"Fetching chapters using nodriver (headless={headless}) for {manga_code}...")
        
        chapters: list[Chapter] = []
        try:
            rows = run_async(cls._get_all_chapters_async(manga_code, headless))
            for row in rows:
                chapters.append(Chapter(
                    chapter_id=row["chapter_id"],
                    number=row["number"],
                    title=row["title"],
                    volume=row.get("volume"),
                    votes=row.get("votes", 0),
                    group_name=row["group_name"],
                    pages_count=row.get("pages_count", 0)
                ))
        except Exception as e:
            logger.error(f"nodriver failed to fetch chapters for {manga_code}: {e}")
            raise
            
        # Reverse the list so old chapters (low numbers) are at the beginning
        chapters.reverse()
        logger.info(f"Found {len(chapters)} chapters using nodriver")
        return chapters
    
    @classmethod
    async def _get_chapter_images_async(
        cls,
        chapter_id: int,
        manga_slug: str,
        chapter_number: str,
        headless: bool,
        browser=None,
        only_pages=None,
    ) -> ChapterImageFetchReport:
        from ..core.downloader import is_cancelled
        await comix_limits.wait_chapter(is_cancelled)
        chapter_url = f"https://comix.to/title/{manga_slug}/{chapter_id}-chapter-{chapter_number}"
        owns_browser = browser is None
        if owns_browser:
            browser = await _start_comix_browser(headless)
                
        image_urls = []
        page_count = 0
        skipped_pages = []
        failed_pages = []
        page_numbers = []
        
        try:
            # Setup init script to backup original toDataURL and set localStorage reader.default preload config
            page = browser.main_tab
            try:
                cdp_page = load_cdp_page()
                await page.send(cdp_page.enable())
                init_js = """
                try {
                    window.__origToDataURL = HTMLCanvasElement.prototype.toDataURL;
                    const k = 'reader.default';
                    const cur = JSON.parse(localStorage.getItem(k) || '{}');
                    cur.preload = 'all';
                    localStorage.setItem(k, JSON.stringify(cur));
                } catch (e) {}
                """
                await page.send(cdp_page.add_script_to_evaluate_on_new_document(source=init_js))
            except Exception as e:
                logger.warning(f"Failed to setup page init script: {e}")
                
            # Now navigate directly to chapter page
            page = await browser.get(chapter_url)
            from ..core.downloader import is_cancelled
            await _wait_for_comix_page(
                page,
                "document.querySelectorAll('.rpage-page').length > 0",
                headless=headless,
                operation="chapter reader",
                browser=browser,
                cancel_check=is_cancelled,
                timeout=90.0,
                unavailable_script=(
                    "Boolean(document.body && /could not be found|page not found|404/i.test("
                    "document.body.innerText || ''))"
                ),
            )
            page_count = await page.evaluate("document.querySelectorAll('.rpage-page').length") or 0
            await _publish_comix_session(browser, page)
            await _save_comix_cookies(browser)
                
            if page_count == 0:
                logger.error(f"Chapter page had no pages in DOM: {chapter_url}")
                return ChapterImageFetchReport([], 0, [], [])
                
            # Wait for first page to begin rendering
            for _ in range(150):
                try:
                    first_ready = await page.evaluate(
                        "document.querySelector('.rpage-page[data-page=\"1\"] canvas, .rpage-page[data-page=\"1\"] img') ? true : false"
                    )
                    if first_ready:
                        break
                except Exception:
                    pass
                await page.sleep(0.2)
                
            logger.info(f"Chapter has {page_count} pages. Extracting content...")
            extraction_deadline = time.monotonic() + min(
                _CHAPTER_EXTRACTION_MAX_SECONDS,
                max(_CHAPTER_EXTRACTION_MIN_SECONDS, page_count * 5.0),
            )
            
            for page_num in range(1, page_count + 1):
                if only_pages is not None and page_num not in only_pages:
                    continue
                if time.monotonic() >= extraction_deadline:
                    failed_pages.extend(range(page_num, page_count + 1))
                    logger.error(
                        "Chapter extraction deadline reached after %s pages",
                        page_num - 1,
                    )
                    break
                # Scroll page element into view to trigger render/decryption
                try:
                    await page.evaluate(
                        f"(() => {{ const el = document.querySelector('.rpage-page[data-page=\"{page_num}\"]'); if (el) el.scrollIntoView({{behavior: 'instant', block: 'center'}}); }})()"
                    )
                except Exception:
                    pass
                    
                # Wait for image element or canvas element to be ready
                ready = None
                for _attempt in range(150):
                    try:
                        ready_res = await page.evaluate(
                            f"""(() => {{
                                const el = document.querySelector('.rpage-page[data-page="{page_num}"]');
                                if (!el) return null;
                                const isLoading = el.classList.contains('is-loading');
                                
                                // Check canvas
                                const c = el.querySelector('canvas');
                                if (c && c.width > 10 && c.height > 10) {{
                                    if (isLoading) return null; // Wait if still loading
                                    const toDataURL = window.__origToDataURL || c.toDataURL;
                                    const data = toDataURL.call(c, 'image/webp', 0.95);
                                    if (data.length < 20000) {{
                                        return JSON.stringify({{type: 'skip'}}); // Blank/Ad canvas
                                    }}
                                    return JSON.stringify({{type: 'canvas_data', data: data}});
                                }}
                                
                                // Check image
                                const i = el.querySelector('img');
                                if (i && i.src) {{
                                    if (i.complete) {{
                                        if (i.naturalWidth > 10 && i.naturalHeight > 10) {{
                                            return JSON.stringify({{type: 'img', src: i.src}});
                                        }}
                                        if (i.naturalWidth > 0 && i.naturalWidth <= 10) {{
                                            return JSON.stringify({{type: 'skip'}}); // 1x1 placeholder
                                        }}
                                    }}
                                }}
                                return null;
                            }})()"""
                        )
                        ready = json.loads(ready_res) if ready_res else None
                    except Exception:
                        ready = None
                    if ready:
                        break
                    await page.sleep(0.2)
                    
                if not ready:
                    logger.error(f"Page {page_num} timed out waiting for render.")
                    failed_pages.append(page_num)
                    continue
                    
                if ready.get('type') == 'skip':
                    logger.debug(f"Page {page_num} is an ad/placeholder page. Skipping.")
                    skipped_pages.append(page_num)
                    continue
                    
                if ready.get('type') == 'canvas_data':
                    image_urls.append(ready.get('data'))
                    page_numbers.append(page_num)
                    continue
                    
                # Extract image data or URL from image
                try:
                    extracted_url = await page.evaluate(
                        f"""(() => {{
                            try {{
                                const el = document.querySelector('.rpage-page[data-page="{page_num}"]');
                                if (!el) return null;
                                
                                const c = el.querySelector('canvas');
                                if (c && c.width > 0 && c.height > 0) {{
                                    const toDataURL = window.__origToDataURL || c.toDataURL;
                                    return toDataURL.call(c, 'image/webp', 0.95);
                                }}
                                
                                const i = el.querySelector('img');
                                if (i && i.src) {{
                                    if (i.src.startsWith('blob:')) {{
                                        try {{
                                            const canvas = document.createElement('canvas');
                                            canvas.width = i.naturalWidth || i.width;
                                            canvas.height = i.naturalHeight || i.height;
                                            const ctx = canvas.getContext('2d');
                                            ctx.drawImage(i, 0, 0);
                                            const toDataURL = window.__origToDataURL || canvas.toDataURL;
                                            return toDataURL.call(canvas, 'image/webp', 0.95);
                                        }} catch (e) {{
                                            return null;
                                        }}
                                    }}
                                    return i.src;
                                }}
                                return null;
                            }} catch (e) {{
                                return null;
                            }}
                        }})()"""
                    )
                except Exception as e:
                    logger.error(f"Page {page_num} extraction failed: {e}")
                    failed_pages.append(page_num)
                    continue
                    
                if extracted_url:
                    image_urls.append(extracted_url)
                    page_numbers.append(page_num)
                else:
                    logger.error(f"Page {page_num} failed to extract valid URL or data.")
                    failed_pages.append(page_num)
            
            return ChapterImageFetchReport(
                image_urls,
                page_count,
                skipped_pages,
                failed_pages,
                page_numbers,
            )
        finally:
            if owns_browser:
                await _close_comix_browser(browser)

    @classmethod
    def get_chapter_image_report(cls, chapter_id: int, manga_slug: str = None, chapter_number: str = None, headless: Optional[bool] = None) -> ChapterImageFetchReport:
        """Fetch image URLs / data URLs for a chapter with extraction diagnostics."""
        if headless is None:
            from ..utils.config import ConfigManager
            headless = ConfigManager().get("headless", True)
            
        if not manga_slug or not chapter_number:
            manga_slug = "manga"
            chapter_number = "1"
            
        chapter_url = f"https://comix.to/title/{manga_slug}/{chapter_id}-chapter-{chapter_number}"
        logger.info(f"Fetching chapter images via nodriver DOM (headless={headless}) for {chapter_url}...")
        
        report = ChapterImageFetchReport([])
        try:
            report = run_async(cls._get_chapter_images_async(chapter_id, manga_slug, chapter_number, headless))
        except Exception as e:
            logger.error(f"nodriver failed to fetch images for chapter {chapter_id}: {e}")
            
        logger.info(f"Retrieved {len(report.image_urls)} / {report.page_count} page images.")
        return report

    @classmethod
    def get_chapter_images(cls, chapter_id: int, manga_slug: str = None, chapter_number: str = None, headless: Optional[bool] = None) -> list[str]:
        """Fetch all image URLs / data URLs for a chapter using nodriver."""
        return cls.get_chapter_image_report(
            chapter_id,
            manga_slug=manga_slug,
            chapter_number=chapter_number,
            headless=headless,
        ).image_urls


class ChapterReaderService:
    """Serialize chapter extraction through one reusable browser process.

    The downloader still uses chapter workers and a shared image pool, but
    reader extraction is owned by this service so a twelve-chapter job cannot
    spawn twelve independent Chrome processes.  A dedicated event-loop thread
    keeps nodriver objects on the loop where they were created.
    """

    def __init__(self, headless: bool):
        self.headless = bool(headless)
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._closed = False
        self._browser = None
        self._verification_error = None
        self._thread = threading.Thread(
            target=self._run_loop,
            name="comix-chapter-reader",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("Chapter reader event loop did not start")

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    async def _ensure_browser(self):
        if self._browser is None:
            self._browser = await _start_comix_browser(self.headless)
        return self._browser

    async def _fetch(self, chapter_id: int, manga_slug: str, chapter_number: str, only_pages=None):
        # The lock is intentionally async: queued chapter tasks do not occupy
        # additional Chrome processes or threads while waiting their turn.
        if not hasattr(self, "_operation_lock"):
            self._operation_lock = asyncio.Lock()
        async with self._operation_lock:
            from ..core.downloader import is_cancelled
            if is_cancelled():
                raise InterruptedError("Download cancelled")
            if self._verification_error is not None:
                raise self._verification_error
            last_error = None
            for attempt in range(2):
                try:
                    browser = await self._ensure_browser()
                    return await ComixAPI._get_chapter_images_async(
                        chapter_id,
                        manga_slug,
                        chapter_number,
                        self.headless,
                        browser=browser,
                        **({"only_pages": only_pages} if only_pages is not None else {}),
                    )
                except Exception as exc:
                    last_error = exc
                    await _close_comix_browser(self._browser)
                    self._browser = None
                    if isinstance(exc, (ComixVerificationRequiredError, ComixBlockedError)):
                        self._verification_error = exc
                        raise
                    if isinstance(exc, InterruptedError):
                        raise
                    if attempt == 0:
                        logger.warning(
                            "Chapter reader browser failed; restarting once: %s",
                            exc,
                        )
            raise last_error

    def fetch(self, chapter_id: int, manga_slug: str, chapter_number: str, only_pages=None, timeout=1200):
        if self._closed:
            raise RuntimeError("Chapter reader service is closed")
        future = asyncio.run_coroutine_threadsafe(
            self._fetch(chapter_id, manga_slug, chapter_number, only_pages),
            self._loop,
        )
        try:
            return future.result(timeout=max(0.01, timeout))
        except FutureTimeoutError:
            future.cancel()
            raise TimeoutError("Tempo limite ao preparar as páginas do capítulo.") from None

    async def _shutdown(self) -> None:
        await _close_comix_browser(self._browser)
        self._browser = None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
            future.result(timeout=10)
        except Exception as exc:
            logger.debug("Chapter reader shutdown failed: %s", exc)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=10)
