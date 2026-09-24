"""Keep successful Comix pages by reader page number across failed attempts."""
from ..utils.page_cache import PageCache
from pathlib import Path


def download_available(downloader, config, manga, chapter, report, refresh, on_progress=None):
    from .downloader import ImageDownloadReport, is_cancelled
    from ..utils.comix_limits import comix_limits

    count = report.page_count or len(report.image_urls)
    # Stable reader positions, not the compact list of URLs: missing page 2
    # must never shift page 3 into its place. Also survives signed URL changes.
    cache = PageCache(config, manga, chapter, [f"reader-page/{n}" for n in range(1, count + 1)],
                     namespace=config.book_id or str(Path(config.download_path).resolve()))
    downloader._local.page_cache = cache
    skipped = set(report.skipped_pages)
    images = {}
    errors = {}
    for number in range(1, count + 1):
        if number not in skipped and cache.get(number) is not None:
            images[number] = cache.path(number)

    def notify():
        if on_progress:
            on_progress(len(images), count - len(skipped))

    def consume(current):
        numbers = current.page_numbers
        if not numbers:
            # Older providers omit ordinals only for complete, ordered reports.
            if current.failed_pages or len(current.image_urls) != current.expected_image_count:
                raise ValueError("A extração incompleta não informou os números das páginas.")
            numbers = [n for n in range(1, count + 1) if n not in current.skipped_pages]
        if len(numbers) != len(current.image_urls) or len(set(numbers)) != len(numbers):
            raise ValueError("Números de páginas inconsistentes na extração.")
        pending = {n: url for n, url in zip(numbers, current.image_urls)
                   if n not in images and n not in skipped and 1 <= n <= count}
        # The image pool writes each success to the durable cache immediately.
        def update(_processed, _total):
            for n in pending:
                if n not in images and cache.get(n) is not None:
                    images[n] = cache.path(n)
            notify()
        downloaded = downloader.download_indexed_images(pending, on_progress=update)
        images.update(dict(downloaded.images))
        errors.update(dict(downloaded.failed))
        notify()

    notify()
    consume(report)
    missing = set(range(1, count + 1)) - skipped - images.keys()
    if missing and not is_cancelled() and not downloader.expired() and not comix_limits.reason:
        try:
            renewed = refresh(sorted(missing))
            if (renewed.page_count or count) != count:
                raise ValueError("O total de páginas mudou durante a tentativa; retome o capítulo.")
            skipped.update(renewed.skipped_pages)
            consume(renewed)
        except Exception as exc:
            errors.update({n: str(exc) for n in missing})
    missing = set(range(1, count + 1)) - skipped - images.keys()
    notify()
    return ImageDownloadReport(
        images=sorted((n, data) for n, data in images.items() if n not in skipped),
        failed=[(n, errors.get(n, "Página indisponível na extração")) for n in sorted(missing)],
        total=count - len(skipped),
    )
