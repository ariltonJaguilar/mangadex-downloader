"""MangaDex adapter, adapted from mangadex-downloader's feed and at-home flow.

Original project: Copyright (c) 2022-present Rahman Yusuf, MIT.
See licenses/mangadex-downloader.LICENSE.
"""

import math
import threading
import time

import requests

from ..core.models import Chapter, MangaInfo, MangaSummary, MangaBrowsePage
from .comix import ChapterImageFetchReport


class MangaDexAPI:
    BASE_URL = "https://api.mangadex.org"
    _lock = threading.Lock()
    _next_request = 0.0
    _next_at_home = 0.0

    def __init__(self, language="pt-br", data_saver=False, fallback_english=False):
        self.language = language
        self.data_saver = data_saver
        self.fallback_english = fallback_english

    @classmethod
    def _get(cls, path, params=None):
        # Share rate limiting across discovery and download worker threads.
        for attempt in range(4):
            with cls._lock:
                now = time.monotonic()
                target = max(now, cls._next_request)
                if path.startswith("/at-home/"):
                    target = max(target, cls._next_at_home)
                    cls._next_at_home = target + 1.6
                cls._next_request = target + 0.25
            time.sleep(max(0, target - time.monotonic()))
            try:
                response = requests.get(
                    cls.BASE_URL + path, params=params,
                    headers={"User-Agent": "MangaDownloader/1.0"}, timeout=(10, 30),
                )
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
                continue
            with response:
                if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                    try:
                        delay = float(response.headers.get("Retry-After", 2 ** attempt))
                    except ValueError:
                        delay = 2 ** attempt
                    if delay > 60:
                        raise RuntimeError("MangaDex rate limit reached. Try again later.")
                    time.sleep(max(0, delay))
                    continue
                response.raise_for_status()
                payload = response.json()
                if payload.get("result") == "error":
                    raise RuntimeError(f"MangaDex API error: {payload.get('errors')}")
                return payload

    def _localized(self, values):
        return values.get(self.language) or values.get("en") or next(iter(values.values()), "")

    def _manga(self, row):
        attr = row["attributes"]
        cover = next((r.get("attributes", {}).get("fileName")
                      for r in row.get("relationships", []) if r["type"] == "cover_art"), None)
        return MangaInfo(
            manga_id=row["id"], hash_id=row["id"], slug=row["id"], source="mangadex",
            title=self._localized(attr.get("title", {})) or "Unknown",
            author=", ".join(dict.fromkeys(r.get("attributes", {}).get("name", "")
                for r in row.get("relationships", []) if r["type"] == "author"
                and r.get("attributes", {}).get("name"))),
            alt_titles=[v for item in attr.get("altTitles", []) for v in item.values()],
            description=self._localized(attr.get("description", {})),
            poster_url=f"https://uploads.mangadex.org/covers/{row['id']}/{cover}.256.jpg" if cover else "",
            original_language=attr.get("originalLanguage"), status=attr.get("status"),
            manga_type="Manga", year=attr.get("year"), final_chapter=attr.get("lastChapter"),
            is_nsfw=attr.get("contentRating") in {"erotica", "pornographic"},
            genres=[self._localized(t["attributes"]["name"]) for t in attr.get("tags", [])],
        )

    def get_manga_info(self, manga_code, headless=None):
        return self._manga(self._get(f"/manga/{manga_code}", {"includes[]": ["cover_art", "author"]})["data"])

    def get_all_chapters(self, manga_code, headless=None):
        chapters, seen, offset = [], set(), 0
        while True:
            params = {
                "includes[]": ["scanlation_group"], "limit": 500, "offset": offset,
                "order[volume]": "asc", "order[chapter]": "asc", "order[readableAt]": "desc",
                "translatedLanguage[]": ([self.language, "en"] if self.fallback_english and self.language != "en" else [self.language]), "includeEmptyPages": 0,
                "contentRating[]": ["safe", "suggestive", "erotica", "pornographic"],
            }
            payload = self._get(f"/manga/{manga_code}/feed", params)
            rows = payload["data"]
            if not rows:
                break
            for row in rows:
                attr = row["attributes"]
                if row["id"] in seen or attr.get("externalUrl") or not attr.get("pages"):
                    continue
                seen.add(row["id"])
                groups = [r.get("attributes", {}).get("name", "Unknown")
                          for r in row.get("relationships", []) if r["type"] == "scanlation_group"]
                chapters.append(Chapter(
                    chapter_id=row["id"], number=attr.get("chapter") or "Oneshot",
                    title=attr.get("title"), volume=attr.get("volume"),
                    group_name=", ".join(groups) or "Unknown", pages_count=attr["pages"],
                    language=attr.get("translatedLanguage", self.language),
                ))
            offset += len(rows)
            if offset >= payload.get("total", offset):
                break
        if self.fallback_english and self.language != "en":
            # A translated release takes priority for the same volume/chapter.
            # Unnumbered releases cannot safely be matched across languages.
            preferred = {(ch.volume, ch.number) for ch in chapters
                         if ch.language == self.language and ch.number != "Oneshot"}
            chapters = [ch for ch in chapters if ch.language != "en"
                        or ch.number == "Oneshot" or (ch.volume, ch.number) not in preferred]
        return chapters

    def get_chapter_image_report(self, chapter_id, **kwargs):
        payload = self._get(f"/at-home/server/{chapter_id}", {"forcePort443": "true"})
        chapter = payload["chapter"]
        key, route = ("dataSaver", "data-saver") if self.data_saver else ("data", "data")
        urls = [f"{payload['baseUrl'].rstrip('/')}/{route}/{chapter['hash']}/{name}"
                for name in chapter[key]]
        return ChapterImageFetchReport(urls, len(urls), [], [], list(range(1, len(urls) + 1)))

    def search_manga(self, query, page=1, headless=None, order="relevance"):
        limit = 20
        page = min(500, max(1, int(page)))
        params = {"limit": limit, "offset": (page - 1) * limit,
                  "includes[]": ["cover_art"], "contentRating[]": ["safe", "suggestive"],
                  f"order[{order}]": "desc"}
        if query:
            params["title"] = query
        payload = self._get("/manga", params)
        items = []
        for row in payload["data"]:
            manga = self._manga(row)
            items.append(MangaSummary(
                manga_id=manga.manga_id, manga_code=manga.hash_id, title=manga.title,
                poster_url=manga.poster_url, manga_type=manga.manga_type, status=manga.status,
                year=manga.year, canonical_url=f"https://mangadex.org/title/{manga.hash_id}",
                content_rating=row["attributes"].get("contentRating", "safe"),
            ))
        total = payload.get("total", len(items))
        return MangaBrowsePage(items, page, max(1, math.ceil(min(total, 10000) / limit)), total)

    def get_manga_highlights(self, headless=None):
        return {
            "trending": self.search_manga("", order="followedCount").items,
            "latest": self.search_manga("", order="latestUploadedChapter").items,
        }
