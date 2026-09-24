import asyncio
import json
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def load_comix_api():
    requests_stub = types.ModuleType("requests")

    class Session:
        def __init__(self):
            self.headers = {}

        def get(self, *args, **kwargs):
            raise AssertionError("requests.Session.get should not be called by these tests")

    requests_stub.Session = Session
    requests_stub.exceptions = types.SimpleNamespace(RequestException=Exception)
    sys.modules.setdefault("requests", requests_stub)

    module = importlib.import_module("src.api.comix")
    return module.ComixAPI


ComixAPI = load_comix_api()
comix_module = importlib.import_module("src.api.comix")


class FakePage:
    def __init__(self, result):
        self.result = result
        self.script = None
        self.evaluate_kwargs = None

    async def evaluate(self, script, **kwargs):
        self.script = script
        self.evaluate_kwargs = kwargs
        return self.result


class ChallengePage:
    def __init__(self, *, clears: bool):
        self.challenge = True
        self.clears = clears

    async def evaluate(self, script, **kwargs):
        if script == "document.title":
            return "Just a moment..." if self.challenge else "Comix - Read Comics online for free"
        if "#challenge-running" in script:
            return self.challenge
        return not self.challenge

    async def sleep(self, _seconds):
        if self.clears:
            self.challenge = False


class StreamingInitialDataPage:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.index = 0

    async def evaluate(self, script, **kwargs):
        if "textContent" in script:
            return self.payloads[min(self.index, len(self.payloads) - 1)]
        raise AssertionError(f"Unexpected script: {script}")

    async def sleep(self, _seconds):
        self.index += 1
        await asyncio.sleep(0)


class ComixChapterTests(unittest.TestCase):
    def test_wait_for_initial_data_ignores_truncated_stream(self):
        manga_code = "mn6wm"
        complete = json.dumps({
            "queries": {
                '["manga", "detail", "mn6wm"]': {
                    "id": 42,
                    "hasChapters": True,
                }
            }
        })
        page = StreamingInitialDataPage([
            complete[:19],
            complete[:97],
            complete,
        ])

        result = asyncio.run(comix_module._wait_for_initial_data(
            page,
            operation="manga details",
            manga_code=manga_code,
            timeout=0.5,
        ))

        self.assertEqual(result["queries"]['["manga", "detail", "mn6wm"]']["id"], 42)
        self.assertTrue(ComixAPI._manga_has_chapters(result, manga_code))

    def test_wait_for_initial_data_reports_persistent_truncation(self):
        page = StreamingInitialDataPage(['{"queries":{"manga":'])

        with self.assertRaisesRegex(
            comix_module.MangaInfoFetchError,
            r"initial data did not become valid.*last payload length: 20.*position",
        ):
            asyncio.run(comix_module._wait_for_initial_data(
                page,
                operation="manga details",
                manga_code="mn6wm",
                timeout=0.02,
            ))

    def test_wait_for_initial_data_requires_requested_detail_query(self):
        wrong_query = json.dumps({
            "queries": {
                '["manga", "detail", "other"]': {"id": 1},
            }
        })
        page = StreamingInitialDataPage([wrong_query])

        with self.assertRaisesRegex(comix_module.MangaInfoFetchError, "detail query for mn6wm"):
            asyncio.run(comix_module._wait_for_initial_data(
                page,
                operation="manga details",
                manga_code="mn6wm",
                timeout=0.02,
            ))

    def test_get_manga_info_maps_validated_parsed_payload(self):
        payload = {
            "queries": {
                '["manga", "detail", "mn6wm"]': {
                    "id": 42,
                    "hid": "hash42",
                    "title": "Validated Manga",
                    "authors": [{"name": "Author A"}, {"title": "Author B"}],
                    "url": "/title/mn6wm-validated-manga",
                }
            }
        }

        async def fetch(_cls, _manga_code, _headless):
            return payload

        with patch.object(ComixAPI, "_get_manga_info_async", new=classmethod(fetch)):
            manga = ComixAPI.get_manga_info("mn6wm", headless=True)

        self.assertEqual(manga.manga_id, 42)
        self.assertEqual(manga.hash_id, "hash42")
        self.assertEqual(manga.title, "Validated Manga")
        self.assertEqual(manga.author, "Author A, Author B")

    def test_get_manga_info_preserves_actionable_fetch_error(self):
        error = comix_module.MangaInfoFetchError("stream was incomplete")
        async def fail_fetch(_cls, _manga_code, _headless):
            raise error

        with patch.object(ComixAPI, "_get_manga_info_async", new=classmethod(fail_fetch)):
            with self.assertRaisesRegex(comix_module.MangaInfoFetchError, "stream was incomplete"):
                ComixAPI.get_manga_info("mn6wm", headless=True)

    def test_waits_for_transient_cloudflare_challenge_in_headless_mode(self):
        page = ChallengePage(clears=True)

        asyncio.run(comix_module._wait_for_comix_page(
            page,
            "Boolean(document.getElementById('initial-data'))",
            headless=True,
            operation="discovery",
            timeout=0.1,
        ))

        self.assertFalse(page.challenge)

    def test_cloudflare_timeout_is_actionable(self):
        page = ChallengePage(clears=False)

        with self.assertRaisesRegex(RuntimeError, "Cloudflare verification did not complete"):
            asyncio.run(comix_module._wait_for_comix_page(
                page,
                "Boolean(document.getElementById('initial-data'))",
                headless=True,
                operation="discovery",
                timeout=0.01,
            ))

    def test_not_found_reader_route_fails_immediately(self):
        class MissingPage:
            async def evaluate(self, script, **kwargs):
                if script == "document.title":
                    return "Comix"
                if "document.body" in script:
                    return True
                if "#challenge-running" in script:
                    return False
                return False

            async def sleep(self, _seconds):
                raise AssertionError("not-found route should not wait for timeout")

        with self.assertRaises(comix_module.ComixPageUnavailableError):
            asyncio.run(comix_module._wait_for_comix_page(
                MissingPage(),
                "document.querySelectorAll('.rpage-page').length > 0",
                headless=True,
                operation="chapter reader",
                timeout=1,
                unavailable_script="Boolean(document.body)",
            ))

    def test_reader_service_reuses_browser_and_closes_it(self):
        class Browser:
            def __init__(self):
                self.closed = 0
                self.stopped = 0

            async def aclose(self):
                self.closed += 1

            def stop(self):
                self.stopped += 1

        browser = Browser()
        reports = []

        async def start(_headless):
            return browser

        async def fetch(_cls, chapter_id, _slug, _number, _headless, browser=None):
            reports.append((chapter_id, browser))
            return comix_module.ChapterImageFetchReport(["data:image/webp;base64,AA=="], 1)

        with patch.object(comix_module, "_start_comix_browser", new=start), \
                patch.object(ComixAPI, "_get_chapter_images_async", new=classmethod(fetch)):
            service = comix_module.ChapterReaderService(True)
            try:
                service.fetch(1, "example", "1")
                service.fetch(2, "example", "2")
            finally:
                service.close()

        self.assertEqual([item[0] for item in reports], [1, 2])
        self.assertIs(reports[0][1], reports[1][1])
        self.assertEqual(browser.closed, 1)
        self.assertEqual(browser.stopped, 1)

    def test_title_containing_moment_is_not_misclassified(self):
        class NormalPage(ChallengePage):
            async def evaluate(self, script, **kwargs):
                if script == "document.title":
                    return "A Moment in Time"
                if "#challenge-running" in script:
                    return False
                return True

        asyncio.run(comix_module._wait_for_comix_page(
            NormalPage(clears=False),
            "Boolean(document.getElementById('initial-data'))",
            headless=True,
            operation="manga details",
            timeout=0.1,
        ))

    def test_cookie_save_is_atomic_and_uses_canonical_path(self):
        class CookieJar:
            async def save(self, path, pattern=".*"):
                Path(path).write_bytes(b"cookies")

        class Browser:
            cookies = CookieJar()

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "cf_cookies.dat"
            with patch.object(comix_module, "_CANONICAL_COOKIE_FILE", target):
                asyncio.run(comix_module._save_comix_cookies(Browser()))

            self.assertEqual(target.read_bytes(), b"cookies")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_normalizes_manga_summary_shape(self):
        summary = ComixAPI._normalize_manga_summary({
            "id": 42,
            "hid": "abc12",
            "title": "Example Manga",
            "type": "manhwa",
            "status": "releasing",
            "year": "2025",
            "latestChapter": 18,
            "ratedAvg": "8.7",
            "contentRating": "erotica",
            "poster": {"medium": "https://static.example/cover.jpg"},
            "url": "/title/abc12-example-manga",
        })

        self.assertEqual(summary.manga_id, 42)
        self.assertEqual(summary.manga_code, "abc12")
        self.assertEqual(summary.title, "Example Manga")
        self.assertEqual(summary.poster_url, "https://static.example/cover.jpg")
        self.assertEqual(summary.year, 2025)
        self.assertEqual(summary.latest_chapter, "18")
        self.assertEqual(summary.rated_avg, 8.7)
        self.assertEqual(summary.content_rating, "erotica")
        self.assertEqual(summary.canonical_url, "https://comix.to/title/abc12-example-manga")

    def test_normalizes_manga_summary_from_url_when_hash_is_missing(self):
        summary = ComixAPI._normalize_manga_summary({
            "title": "URL Only",
            "url": "https://comix.to/title/u77-url-only",
        })

        self.assertEqual(summary.manga_code, "u77")
        self.assertEqual(summary.canonical_url, "https://comix.to/title/u77-url-only")

    def test_rejects_manga_summary_without_identity(self):
        self.assertIsNone(ComixAPI._normalize_manga_summary({"title": "No URL"}))
        self.assertIsNone(ComixAPI._normalize_manga_summary({"hid": "abc"}))

    def test_normalizes_manga_browse_page_and_deduplicates(self):
        page = ComixAPI._normalize_manga_browse_page({
            "items": [
                {"hid": "a1", "title": "One", "url": "/title/a1-one"},
                {"hid": "a1", "title": "Duplicate", "url": "/title/a1-duplicate"},
                {"hid": "b2", "title": "Two", "url": "/title/b2-two"},
            ],
            "meta": {"page": 2, "lastPage": 5, "total": 41},
        }, requested_page=2)

        self.assertEqual([item.manga_code for item in page.items], ["a1", "b2"])
        self.assertEqual(page.page, 2)
        self.assertEqual(page.last_page, 5)
        self.assertEqual(page.total, 41)
        self.assertTrue(page.has_next)
        self.assertTrue(page.has_previous)

    def test_discovery_page_api_uses_current_client_and_all_ratings(self):
        page = FakePage(
            '{"ok":true,"data":{"items":['
            '{"hid":"a1","title":"One","url":"/title/a1-one"}'
            '],"meta":{"page":1,"lastPage":1,"total":1}}}'
        )

        result = asyncio.run(ComixAPI._fetch_discovery_via_page_api(
            page, keyword="one", page_number=2, limit=20
        ))

        self.assertTrue(result["ok"])
        self.assertIn("api.list", page.script)
        self.assertIn("relevance: 'desc'", page.script)
        self.assertIn("safe", page.script)
        self.assertIn("pornographic", page.script)
        self.assertIn('const pageNumber = 2', page.script)
        self.assertEqual(page.evaluate_kwargs, {"await_promise": True, "return_by_value": True})

    def test_discovery_page_api_reports_errors(self):
        page = FakePage('{"ok":false,"error":"catalog unavailable"}')

        with self.assertRaisesRegex(RuntimeError, "catalog unavailable"):
            asyncio.run(ComixAPI._fetch_discovery_via_page_api(page))

    def test_discovery_highlights_use_trending_and_latest_calls(self):
        page = FakePage(
            '{"ok":true,"trending":['
            '{"hid":"t1","title":"Trend","url":"/title/t1-trend"}],'
            '"latest":{"items":['
            '{"hid":"l1","title":"Latest","url":"/title/l1-latest"}]}}'
        )

        result = asyncio.run(ComixAPI._fetch_discovery_via_page_api(
            page, highlights=True, limit=8
        ))

        self.assertEqual(result["trending"][0]["title"], "Trend")
        self.assertIn("api.top", page.script)
        self.assertIn("days: 7", page.script)
        self.assertIn("chapter_updated_at: 'desc'", page.script)

    def test_normalizes_current_chapter_api_shape(self):
        row = ComixAPI._normalize_chapter_api_item({
            "id": "9744989",
            "number": 100,
            "name": "Finale",
            "volume": 2,
            "group": {"id": 9897, "name": "Official"},
            "pagesCount": 42,
        })

        self.assertEqual(row["chapter_id"], 9744989)
        self.assertEqual(row["number"], "100")
        self.assertEqual(row["title"], "Finale")
        self.assertEqual(row["volume"], 2)
        self.assertEqual(row["group_name"], "Official")
        self.assertEqual(row["pages_count"], 42)

    def test_normalizes_legacy_chapter_api_shape(self):
        row = ComixAPI._normalize_chapter_api_item({
            "chapter_id": 1537020,
            "number": "1",
            "title": "Start",
            "scanlation_group": {"name": "MagusManga"},
            "pages_count": 18,
            "votes": 7,
        })

        self.assertEqual(row["chapter_id"], 1537020)
        self.assertEqual(row["title"], "Start")
        self.assertEqual(row["group_name"], "MagusManga")
        self.assertEqual(row["pages_count"], 18)
        self.assertEqual(row["votes"], 7)

    def test_uses_official_group_when_api_has_no_group_name(self):
        row = ComixAPI._normalize_chapter_api_item({
            "id": 1,
            "number": "12",
            "isOfficial": True,
        })

        self.assertEqual(row["group_name"], "Official")

    def test_rejects_api_items_without_required_identity(self):
        self.assertIsNone(ComixAPI._normalize_chapter_api_item({"id": 1}))
        self.assertIsNone(ComixAPI._normalize_chapter_api_item({"number": "1"}))
        self.assertIsNone(ComixAPI._normalize_chapter_api_item({"id": "bad", "number": "1"}))

    def test_normalizes_dom_row(self):
        row = ComixAPI._normalize_chapter_dom_row({
            "href": "/title/y86v-i-became-a-level-999-demon-queen/1537020-chapter-1",
            "title": "Opening",
            "group": "",
            "group_official": True,
        })

        self.assertEqual(row["chapter_id"], 1537020)
        self.assertEqual(row["number"], "1")
        self.assertEqual(row["title"], "Opening")
        self.assertEqual(row["group_name"], "Official")

    def test_dedupes_chapter_rows_by_id(self):
        rows = ComixAPI._dedupe_chapter_rows([
            {"chapter_id": 1, "number": "1"},
            {"chapter_id": 1, "number": "1 duplicate"},
            {"chapter_id": 2, "number": "2"},
        ])

        self.assertEqual(rows, [
            {"chapter_id": 1, "number": "1"},
            {"chapter_id": 2, "number": "2"},
        ])

    def test_page_api_result_is_normalized_and_deduped(self):
        page = FakePage(
            '{"ok":true,"items":['
            '{"id":2,"number":"2","name":"Two","group":{"name":"A"}},'
            '{"id":2,"number":"2","name":"Two again","group":{"name":"A"}},'
            '{"id":1,"number":"1","isOfficial":true}'
            ']}'
        )

        rows = asyncio.run(ComixAPI._fetch_chapters_via_page_api(page, "y86v"))

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["chapter_id"], 2)
        self.assertEqual(rows[0]["title"], "Two")
        self.assertEqual(rows[1]["chapter_id"], 1)
        self.assertEqual(rows[1]["group_name"], "Official")
        self.assertIn("api.chapters", page.script)
        self.assertIn("order: { number: 'desc' }", page.script)
        self.assertEqual(
            page.evaluate_kwargs,
            {"await_promise": True, "return_by_value": True},
        )

    def test_page_api_error_raises_useful_exception(self):
        page = FakePage('{"ok":false,"error":"Comix API module not found"}')

        with self.assertRaisesRegex(RuntimeError, "Comix API module not found"):
            asyncio.run(ComixAPI._fetch_chapters_via_page_api(page, "y86v"))

    def test_page_api_rejects_missing_serialized_result(self):
        page = FakePage(None)

        with self.assertRaisesRegex(RuntimeError, "no serialized result"):
            asyncio.run(ComixAPI._fetch_chapters_via_page_api(page, "y86v"))

    def test_page_api_rejects_invalid_json_result(self):
        page = FakePage("not-json")

        with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
            asyncio.run(ComixAPI._fetch_chapters_via_page_api(page, "y86v"))


if __name__ == "__main__":
    unittest.main()
