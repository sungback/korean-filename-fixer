import os
import tempfile
import unicodedata
import unittest

from watcher import NFDHandler


def nfd_name(text: str) -> str:
    return unicodedata.normalize("NFD", text)


class WatcherTests(unittest.TestCase):
    def test_resolve_actual_path_finds_nfd_name_from_nfc_event_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            actual_path = os.path.join(tmp, nfd_name("감시.txt"))
            with open(actual_path, "w", encoding="utf-8") as f:
                f.write("watch")

            handler = NFDHandler(lambda result: None)
            resolved = handler._resolve_actual_path(os.path.join(tmp, "감시.txt"))

            self.assertEqual(resolved, actual_path)

    def test_handle_converts_nfd_file_and_calls_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            captured = []
            handler = NFDHandler(captured.append)

            original_path = os.path.join(tmp, nfd_name("한글.txt"))
            with open(original_path, "w", encoding="utf-8") as f:
                f.write("content")

            handler._handle(original_path, is_directory=False)

            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0].status, "converted")
            self.assertTrue(os.path.exists(os.path.join(tmp, "한글.txt")))

    def test_handle_ignores_excluded_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            captured = []
            excluded_dir = os.path.join(tmp, ".git")
            os.makedirs(excluded_dir)

            original_path = os.path.join(excluded_dir, nfd_name("무시.txt"))
            with open(original_path, "w", encoding="utf-8") as f:
                f.write("ignore")

            handler = NFDHandler(captured.append, exclude_patterns=[".git"])
            handler._handle(original_path, is_directory=False)

            self.assertEqual(captured, [])
            self.assertTrue(os.path.exists(original_path))

    def test_is_duplicate_returns_true_for_repeated_path_within_window(self):
        handler = NFDHandler(lambda result: None)
        path = "/tmp/example.txt"

        self.assertFalse(handler._is_duplicate(path))
        self.assertTrue(handler._is_duplicate(path))


if __name__ == "__main__":
    unittest.main()
