import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from urllib3.exceptions import IncompleteRead, ProtocolError

from mangadex_downloader.downloader import ChapterPageDownloader, FileDownloader


def response(chunks, length, status=200, **headers):
    result = Mock(status_code=status, headers={"Content-Length": str(length), **headers})
    result.raw.read.side_effect = chunks
    return result


def interrupted():
    return ProtocolError("Connection broken", IncompleteRead(3, 3))


class DownloadChecks(unittest.TestCase):
    def run_download(self, responses, expected, downloader_class=FileDownloader):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "page.jpg"
            with patch("mangadex_downloader.downloader.Net") as net, patch(
                "mangadex_downloader.downloader.pbm"
            ), patch.object(FileDownloader, "_register_keyboardinterrupt_handler"):
                net.mangadex.get.side_effect = responses
                downloader = downloader_class("https://example.org/page.jpg", path)
                success = downloader.download()
                self.assertEqual(success, expected is not None)
                if expected is not None:
                    self.assertEqual(path.read_bytes(), expected)
                    self.assertFalse(Path(str(path) + ".temp").exists())
                else:
                    self.assertFalse(path.exists())
                calls = net.mangadex.get.call_args_list
                for item in responses:
                    item.close.assert_called_once()
                return calls, net.mangadex.report.call_args

    def test_interrupted_stream_resumes(self):
        calls, _ = self.run_download([
            response([b"abc", interrupted()], 6),
            response([b"def", b""], 3, 206, **{"content-range": "bytes 3-5/6"}),
        ], b"abcdef")
        self.assertEqual(calls[1].kwargs["headers"]["Range"], "bytes=3-")
        self.assertEqual(calls[0].kwargs["headers"], {})

    def test_ignored_range_restarts_without_duplicate_bytes(self):
        self.run_download([
            response([b"abc", interrupted()], 6),
            response([b"abcdef", b""], 6, **{"accept-ranges": "bytes"}),
        ], b"abcdef")

    def test_short_stream_resumes(self):
        self.run_download([
            response([b"abc", b""], 6),
            response([b"def", b""], 3, 206, **{"content-range": "bytes 3-5/6"}),
        ], b"abcdef")

    def test_retry_exhaustion_reports_failure_without_reading_broken_body(self):
        responses = [response([interrupted()], 6) for _ in range(5)]
        calls, report = self.run_download(responses, None, ChapterPageDownloader)
        self.assertEqual(len(calls), 5)
        self.assertFalse(report.args[0]["success"])
        self.assertEqual(report.args[0]["bytes"], 0)


if __name__ == "__main__":
    unittest.main()
