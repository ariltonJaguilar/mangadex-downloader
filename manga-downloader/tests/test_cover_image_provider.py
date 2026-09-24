import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    from PyQt6.QtCore import QSize, QBuffer, QIODevice
    from PyQt6.QtGui import QGuiApplication, QImage
    from gui.bridge.discovery_bridge import _summary_to_dict
    from gui.cover_image_provider import (
        ComixCoverImageProvider,
        _decode_cover_source,
        cover_image_source,
    )
    from src.core.models import MangaSummary
    from src.utils import comix_session
except ImportError:  # pragma: no cover - exercised only in minimal environments
    QGuiApplication = None
    QSize = None
    QBuffer = None
    QIODevice = None
    QImage = None
    _summary_to_dict = None
    ComixCoverImageProvider = None
    _decode_cover_source = None
    cover_image_source = None
    MangaSummary = None
    comix_session = None


@unittest.skipUnless(QGuiApplication is not None, "PyQt6 is not installed")
class CoverImageProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        comix_session._credential_store.clear_for_tests()

    def tearDown(self):
        comix_session._credential_store.clear_for_tests()

    @staticmethod
    def _png_bytes():
        image = QImage(4, 6, QImage.Format.Format_RGB32)
        image.fill(0xFFAA44)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.ReadWrite)
        image.save(buffer, "PNG")
        return bytes(buffer.data())

    def test_cover_source_round_trips_and_rejects_other_hosts(self):
        url = "https://static.comix.to/40e8/i/9/fa/cover.jpg"
        source = cover_image_source(url)

        self.assertTrue(source.startswith("image://comix-cover/"))
        self.assertEqual(_decode_cover_source(source.rsplit("/", 1)[-1]), url)
        self.assertEqual(cover_image_source("https://example.com/cover.jpg"), "")
        self.assertIsNone(_decode_cover_source("not-valid"))

    def test_credentials_filter_to_comix_domain(self):
        credentials = comix_session.publish_comix_credentials(
            "Mozilla/5.0 Chrome/150.0.0.0",
            [
                SimpleNamespace(name="cf_clearance", value="clear", domain=".comix.to"),
                SimpleNamespace(name="NID", value="secret", domain=".google.com"),
            ],
        )

        self.assertEqual(credentials.user_agent, "Mozilla/5.0 Chrome/150.0.0.0")
        self.assertEqual(credentials.cookie_dict, {"cf_clearance": "clear"})

    def test_provider_fetches_and_decodes_valid_cover(self):
        url = "https://static.comix.to/40e8/i/9/fa/cover.jpg"
        comix_session.publish_comix_credentials(
            "Mozilla/5.0 Chrome/150.0.0.0",
            [SimpleNamespace(name="cf_clearance", value="clear", domain=".comix.to")],
        )
        response = SimpleNamespace(
            status_code=200,
            url=url,
            headers={"content-type": "image/png"},
            content=self._png_bytes(),
        )
        provider = ComixCoverImageProvider()

        with patch("gui.cover_image_provider.requests.get", return_value=response, create=True) as get:
            image, size = provider.requestImage(
                cover_image_source(url).rsplit("/", 1)[-1], QSize()
            )

        self.assertEqual(image.size(), QSize(4, 6))
        self.assertEqual(size, QSize(4, 6))
        self.assertEqual(get.call_args.kwargs["headers"]["User-Agent"], "Mozilla/5.0 Chrome/150.0.0.0")
        self.assertEqual(get.call_args.kwargs["cookies"], {"cf_clearance": "clear"})

    def test_provider_rejects_cloudflare_response(self):
        url = "https://static.comix.to/40e8/i/9/fa/cover.jpg"
        comix_session.publish_comix_credentials("Mozilla/5.0 Chrome/150.0.0.0", [])
        response = SimpleNamespace(
            status_code=403,
            url=url,
            headers={"content-type": "text/html"},
            content=b"challenge",
        )
        provider = ComixCoverImageProvider()

        with patch("gui.cover_image_provider.requests.get", return_value=response, create=True):
            image, size = provider.requestImage(
                cover_image_source(url).rsplit("/", 1)[-1], QSize()
            )

        self.assertTrue(image.isNull())
        self.assertEqual(size, QSize())

    def test_gui_summary_preserves_url_and_adds_provider_source(self):
        url = "https://static.comix.to/40e8/i/9/fa/cover.jpg"
        summary = MangaSummary(manga_code="mn6wm", title="Example", poster_url=url)

        data = _summary_to_dict(summary)

        self.assertEqual(data["poster_url"], url)
        self.assertEqual(_decode_cover_source(data["poster_source"].rsplit("/", 1)[-1]), url)


if __name__ == "__main__":
    unittest.main()
