import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from autostart import (
    WINDOWS_RUN_VALUE_NAME,
    build_launch_agent_plist,
    disable_autostart,
    enable_autostart,
    get_autostart_executable_path,
    get_bundle_executable_path,
    get_registered_executable,
    get_windows_executable_path,
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

    @patch("autostart.sys.platform", "darwin")
    def test_get_autostart_executable_path_returns_bundle_on_macos(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = os.path.join(tmp, "Test.app", "Contents", "MacOS", "Test")
            os.makedirs(os.path.dirname(executable))
            with open(executable, "w", encoding="utf-8") as f:
                f.write("")

            self.assertEqual(get_autostart_executable_path(executable), os.path.abspath(executable))

    def test_get_windows_executable_path_accepts_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = os.path.join(tmp, "KoreanFilenameFixer.exe")
            with open(executable, "w", encoding="utf-8") as f:
                f.write("")

            self.assertEqual(get_windows_executable_path(executable), os.path.abspath(executable))

    @patch("autostart.sys.platform", "win32")
    def test_get_autostart_executable_path_returns_exe_on_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = os.path.join(tmp, "KoreanFilenameFixer.exe")
            with open(executable, "w", encoding="utf-8") as f:
                f.write("")

            self.assertEqual(get_autostart_executable_path(executable), os.path.abspath(executable))

    @patch("autostart.sys.platform", "darwin")
    def test_enable_autostart_writes_launch_agent_plist(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = os.path.join(tmp, "Test.app", "Contents", "MacOS", "Test")
            plist_path = os.path.join(tmp, "com.example.test.plist")
            os.makedirs(os.path.dirname(executable))
            with open(executable, "w", encoding="utf-8") as f:
                f.write("")

            enable_autostart(executable, plist_path=plist_path)

            self.assertEqual(get_registered_executable(plist_path=plist_path), os.path.abspath(executable))
            self.assertTrue(is_autostart_enabled(executable, plist_path=plist_path))
            self.assertFalse(needs_autostart_refresh(executable, plist_path=plist_path))

    @patch("autostart.sys.platform", "darwin")
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

            self.assertTrue(needs_autostart_refresh(new_executable, plist_path=plist_path))
            self.assertFalse(is_autostart_enabled(new_executable, plist_path=plist_path))

    @patch("autostart.sys.platform", "darwin")
    def test_disable_autostart_removes_plist(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = os.path.join(tmp, "Test.app", "Contents", "MacOS", "Test")
            plist_path = os.path.join(tmp, "com.example.test.plist")
            os.makedirs(os.path.dirname(executable))
            with open(executable, "w", encoding="utf-8") as f:
                f.write("")

            enable_autostart(executable, plist_path=plist_path)
            disable_autostart(plist_path=plist_path)

            self.assertFalse(os.path.exists(plist_path))

    def test_build_launch_agent_plist_uses_expected_arguments(self):
        executable = "/Applications/Test.app/Contents/MacOS/Test"
        plist = build_launch_agent_plist(executable)
        self.assertEqual(plist["ProgramArguments"], [os.path.abspath(executable)])
        self.assertTrue(plist["RunAtLoad"])

    @patch("autostart.sys.platform", "win32")
    @patch("autostart.winreg")
    def test_enable_autostart_writes_windows_run_value(self, mock_winreg):
        with tempfile.TemporaryDirectory() as tmp:
            executable = os.path.join(tmp, "KoreanFilenameFixer.exe")
            with open(executable, "w", encoding="utf-8") as f:
                f.write("")

            mock_winreg.HKEY_CURRENT_USER = object()
            mock_winreg.REG_SZ = object()
            mock_winreg.CreateKey.return_value = "key"

            result = enable_autostart(executable)

            self.assertEqual(result, os.path.abspath(executable))
            mock_winreg.SetValueEx.assert_called_once_with(
                "key",
                WINDOWS_RUN_VALUE_NAME,
                0,
                mock_winreg.REG_SZ,
                f'"{os.path.abspath(executable)}"',
            )
            mock_winreg.CloseKey.assert_called_once_with("key")

    @patch("autostart.sys.platform", "win32")
    @patch("autostart.winreg")
    def test_windows_autostart_state_reads_registered_run_value(self, mock_winreg):
        with tempfile.TemporaryDirectory() as tmp:
            current = os.path.join(tmp, "Current.exe")
            moved = os.path.join(tmp, "Moved.exe")
            for path in (current, moved):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("")

            mock_winreg.HKEY_CURRENT_USER = object()
            mock_winreg.OpenKey.return_value = "key"
            mock_winreg.QueryValueEx.return_value = (f'"{os.path.abspath(current)}"', Mock())

            self.assertEqual(get_registered_executable(), os.path.abspath(current))
            self.assertTrue(is_autostart_enabled(current))
            self.assertFalse(is_autostart_enabled(moved))
            self.assertTrue(needs_autostart_refresh(moved))
            self.assertFalse(needs_autostart_refresh(current))

    @patch("autostart.sys.platform", "win32")
    @patch("autostart.winreg")
    def test_disable_autostart_removes_windows_run_value(self, mock_winreg):
        mock_winreg.HKEY_CURRENT_USER = object()
        mock_winreg.KEY_SET_VALUE = object()
        mock_winreg.OpenKey.return_value = "key"

        disable_autostart()

        mock_winreg.DeleteValue.assert_called_once_with("key", WINDOWS_RUN_VALUE_NAME)
        mock_winreg.CloseKey.assert_called_once_with("key")


if __name__ == "__main__":
    unittest.main()
