import io
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from updater import (
    UPDATE_CHECK_INTERVAL,
    fetch_latest_release,
    is_newer,
    parse_version,
    should_check,
)


class VersionCompareTests(unittest.TestCase):
    def test_newer_patch_returns_true(self):
        self.assertTrue(is_newer("v1.13.2", "v1.13.1"))

    def test_newer_minor_returns_true(self):
        self.assertTrue(is_newer("v1.14.0", "v1.13.1"))

    def test_same_version_returns_false(self):
        self.assertFalse(is_newer("v1.13.1", "v1.13.1"))

    def test_older_version_returns_false(self):
        self.assertFalse(is_newer("v1.12.0", "v1.13.1"))

    def test_unparsable_tag_returns_false(self):
        self.assertFalse(is_newer("latest", "v1.13.1"))
        self.assertFalse(is_newer("v1.13.1", ""))

    def test_parse_version_handles_missing_v_prefix(self):
        self.assertEqual(parse_version("1.2.3"), (1, 2, 3))


class CheckIntervalTests(unittest.TestCase):
    def test_never_checked_returns_true(self):
        self.assertTrue(should_check(0.0, now=UPDATE_CHECK_INTERVAL + 1))

    def test_recent_check_returns_false(self):
        self.assertFalse(should_check(900.0, now=1000.0))

    def test_expired_check_returns_true(self):
        self.assertTrue(
            should_check(1000.0 - UPDATE_CHECK_INTERVAL - 1, now=1000.0))


class FetchReleaseTests(unittest.TestCase):
    def _urlopen_ok(self, payload: bytes):
        context_manager = MagicMock()
        context_manager.__enter__.return_value = io.BytesIO(payload)
        return context_manager

    def test_fetch_returns_tag_and_url(self):
        payload = b'{"tag_name": "v1.14.0", "html_url": "https://example.com/r"}'
        with patch("updater.urllib.request.urlopen",
                   return_value=self._urlopen_ok(payload)):
            self.assertEqual(
                fetch_latest_release("owner/repo"),
                ("v1.14.0", "https://example.com/r"),
            )

    def test_fetch_returns_none_on_network_error(self):
        with patch("updater.urllib.request.urlopen",
                   side_effect=URLError("offline")):
            self.assertIsNone(fetch_latest_release("owner/repo"))

    def test_fetch_returns_none_when_tag_missing(self):
        payload = b'{"html_url": "https://example.com/r"}'
        with patch("updater.urllib.request.urlopen",
                   return_value=self._urlopen_ok(payload)):
            self.assertIsNone(fetch_latest_release("owner/repo"))


if __name__ == "__main__":
    unittest.main()
