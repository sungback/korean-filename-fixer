import tempfile
import unittest

from gui import should_run_startup_scan


class GuiTests(unittest.TestCase):
    def test_should_run_startup_scan_requires_existing_folder_and_enabled_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(should_run_startup_scan(tmp, True))
            self.assertFalse(should_run_startup_scan(tmp, False))

    def test_should_run_startup_scan_rejects_missing_folder(self):
        self.assertFalse(should_run_startup_scan("", True))
        self.assertFalse(should_run_startup_scan("/path/does/not/exist", True))


if __name__ == "__main__":
    unittest.main()
