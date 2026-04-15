import os
import tempfile
import unittest

from autostart import (
    build_launch_agent_plist,
    disable_autostart,
    enable_autostart,
    get_bundle_executable_path,
    get_registered_executable,
    is_autostart_enabled,
    needs_autostart_refresh,
)


class AutostartTests(unittest.TestCase):
    def test_get_bundle_executable_path_returns_none_for_non_app_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            script_path = os.path.join(tmp, "main.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write("print('hello')")

            self.assertIsNone(get_bundle_executable_path(script_path))

    def test_get_bundle_executable_path_accepts_app_bundle_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = os.path.join(tmp, "Test.app", "Contents", "MacOS", "Test")
            os.makedirs(os.path.dirname(executable))
            with open(executable, "w", encoding="utf-8") as f:
                f.write("")

            self.assertEqual(get_bundle_executable_path(executable), os.path.abspath(executable))

    def test_enable_autostart_writes_launch_agent_plist(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = os.path.join(tmp, "Test.app", "Contents", "MacOS", "Test")
            plist_path = os.path.join(tmp, "com.example.test.plist")
            os.makedirs(os.path.dirname(executable))
            with open(executable, "w", encoding="utf-8") as f:
                f.write("")

            enable_autostart(executable, plist_path=plist_path)

            self.assertEqual(get_registered_executable(plist_path), os.path.abspath(executable))
            self.assertTrue(is_autostart_enabled(executable, plist_path))
            self.assertFalse(needs_autostart_refresh(executable, plist_path))

    def test_needs_autostart_refresh_detects_moved_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_executable = os.path.join(tmp, "Old.app", "Contents", "MacOS", "Old")
            new_executable = os.path.join(tmp, "New.app", "Contents", "MacOS", "New")
            plist_path = os.path.join(tmp, "com.example.test.plist")

            os.makedirs(os.path.dirname(old_executable))
            os.makedirs(os.path.dirname(new_executable))
            for path in (old_executable, new_executable):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("")

            enable_autostart(old_executable, plist_path=plist_path)

            self.assertTrue(needs_autostart_refresh(new_executable, plist_path))
            self.assertFalse(is_autostart_enabled(new_executable, plist_path))

    def test_disable_autostart_removes_plist(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = os.path.join(tmp, "Test.app", "Contents", "MacOS", "Test")
            plist_path = os.path.join(tmp, "com.example.test.plist")
            os.makedirs(os.path.dirname(executable))
            with open(executable, "w", encoding="utf-8") as f:
                f.write("")

            enable_autostart(executable, plist_path=plist_path)
            disable_autostart(plist_path)

            self.assertFalse(os.path.exists(plist_path))

    def test_build_launch_agent_plist_uses_expected_arguments(self):
        executable = "/Applications/Test.app/Contents/MacOS/Test"
        plist = build_launch_agent_plist(executable)
        self.assertEqual(plist["ProgramArguments"], [os.path.abspath(executable)])
        self.assertTrue(plist["RunAtLoad"])


if __name__ == "__main__":
    unittest.main()
