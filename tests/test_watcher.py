import os
import tempfile
import unicodedata
import unittest
from unittest.mock import patch

from converter import ConvertResult
from watcher import NFDHandler


def nfd_name(text: str) -> str:
    return unicodedata.normalize("NFD", text)


class FakeEvent:
    def __init__(self, path: str, is_directory: bool):
        self.src_path = path
        self.is_directory = is_directory


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

    def test_on_created_handles_nfd_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_path = os.path.join(tmp, nfd_name("새폴더"))
            converted_path = os.path.join(tmp, "새폴더")
            os.makedirs(original_path)
            captured = []

            handler = NFDHandler(captured.append)

            handler.on_created(FakeEvent(original_path, is_directory=True))

            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0].status, "converted")
            self.assertEqual(captured[0].path, converted_path)
            self.assertFalse(os.path.exists(original_path))
            self.assertTrue(os.path.isdir(converted_path))

    def test_handle_passes_conflict_result_to_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            captured = []
            handler = NFDHandler(captured.append)

            original_path = os.path.join(tmp, nfd_name("충돌.txt"))
            with open(original_path, "w", encoding="utf-8") as f:
                f.write("conflict")

            conflict_result = ConvertResult(
                original_path,
                os.path.basename(original_path),
                "충돌.txt",
                "conflict",
                "충돌.txt 이미 존재",
            )
            with patch("watcher.convert_file", return_value=conflict_result):
                handler._handle(original_path, is_directory=False)

            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0].status, "conflict")

    def test_is_duplicate_returns_true_for_repeated_path_within_window(self):
        handler = NFDHandler(lambda result: None)
        path = "/tmp/example.txt"

        self.assertFalse(handler._is_duplicate(path))
        self.assertTrue(handler._is_duplicate(path))


if __name__ == "__main__":
    unittest.main()
