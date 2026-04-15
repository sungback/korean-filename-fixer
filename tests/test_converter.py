import os
import tempfile
import unicodedata
import unittest

from converter import (
    clean_exclude_patterns,
    convert_file,
    convert_folder,
    is_nfd,
    should_exclude_path,
)


def nfd_name(text: str) -> str:
    return unicodedata.normalize("NFD", text)


class ConverterTests(unittest.TestCase):
    def test_clean_exclude_patterns_removes_empty_values_and_duplicates(self):
        patterns = clean_exclude_patterns([".git", "", " node_modules ", ".git", None])
        self.assertEqual(patterns, [".git", "node_modules"])

    def test_should_exclude_path_matches_directory_segments_only(self):
        path = os.path.join("/tmp", "project", "node_modules", "pkg", "file.txt")
        self.assertTrue(should_exclude_path(path, ["node_modules"], is_directory=False))
        self.assertFalse(should_exclude_path(path, ["file.txt"], is_directory=False))

    def test_convert_file_renames_nfd_filename_to_nfc(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_name = nfd_name("한글.txt")
            original_path = os.path.join(tmp, original_name)

            with open(original_path, "w", encoding="utf-8") as f:
                f.write("content")

            result = convert_file(original_path)
            expected_path = os.path.join(tmp, "한글.txt")

            self.assertEqual(result.status, "converted")
            self.assertEqual(result.path, expected_path)
            self.assertTrue(os.path.exists(expected_path))
            entry_names = [entry.name for entry in os.scandir(tmp)]
            self.assertIn("한글.txt", entry_names)

            with open(expected_path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "content")

    def test_convert_folder_skips_excluded_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            keep_dir = os.path.join(tmp, "keep")
            excluded_dir = os.path.join(tmp, "node_modules")
            os.makedirs(keep_dir)
            os.makedirs(excluded_dir)

            keep_path = os.path.join(keep_dir, nfd_name("변환.txt"))
            excluded_path = os.path.join(excluded_dir, nfd_name("제외.txt"))

            with open(keep_path, "w", encoding="utf-8") as f:
                f.write("keep")
            with open(excluded_path, "w", encoding="utf-8") as f:
                f.write("skip")

            results = convert_folder(tmp, exclude_patterns=["node_modules"])
            converted = [result for result in results if result.status == "converted"]

            self.assertEqual(len(converted), 1)
            self.assertEqual(converted[0].converted, "변환.txt")
            self.assertTrue(os.path.exists(os.path.join(keep_dir, "변환.txt")))
            self.assertTrue(os.path.exists(excluded_path))
            excluded_names = [entry.name for entry in os.scandir(excluded_dir)]
            self.assertTrue(any(is_nfd(name) for name in excluded_names))


if __name__ == "__main__":
    unittest.main()
