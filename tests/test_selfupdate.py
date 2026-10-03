import hashlib
import os
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock, patch

from selfupdate import (
    MACOS_ZIP_NAME,
    WINDOWS_EXE_NAME,
    WINDOWS_ZIP_NAME,
    checksum_url,
    cleanup_staging,
    current_exe_path,
    current_install_dir,
    current_macos_app,
    download_update,
    extract_mac_app,
    extract_update,
    fetch_text,
    is_writable_dir,
    latest_asset_url,
    parse_checksum,
    release_asset_url,
    staging_dir,
    verify_bundle,
    verify_sha256,
    write_mac_update_script,
    write_update_batch,
)


class UrlTests(unittest.TestCase):
    def test_release_asset_url_points_to_tag(self):
        url = release_asset_url("owner/repo", "v1.15.0")
        self.assertEqual(
            url,
            "https://github.com/owner/repo/releases/download/v1.15.0/"
            + WINDOWS_ZIP_NAME,
        )

    def test_release_asset_url_accepts_mac_zip(self):
        url = release_asset_url("owner/repo", "v1.15.0", MACOS_ZIP_NAME)
        self.assertTrue(url.endswith(MACOS_ZIP_NAME))

    def test_latest_asset_url_uses_latest(self):
        url = latest_asset_url("owner/repo")
        self.assertIn("/releases/latest/download/", url)
        self.assertTrue(url.endswith(WINDOWS_ZIP_NAME))

    def test_checksum_url_appends_suffix(self):
        self.assertEqual(
            checksum_url("https://example.com/a.zip"),
            "https://example.com/a.zip.sha256",
        )


class DownloadTests(unittest.TestCase):
    def _urlopen_ok(self, payload: bytes):
        response = MagicMock()
        response.read.side_effect = [payload[:2], payload[2:], b""]
        response.info.return_value.get.return_value = str(len(payload))
        context_manager = MagicMock()
        context_manager.__enter__.return_value = response
        return context_manager

    def test_download_writes_file_and_reports_progress(self):
        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "sub", WINDOWS_ZIP_NAME)
            with patch("selfupdate.urllib.request.urlopen",
                       return_value=self._urlopen_ok(b"abcd")):
                result = download_update(
                    "https://example.com/a.zip", dest,
                    progress_callback=seen.append)

            self.assertEqual(result, dest)
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), b"abcd")
        self.assertEqual(seen, [(2, 4), (4, 4)])

    def test_fetch_text_returns_none_on_error(self):
        with patch("selfupdate.urllib.request.urlopen",
                   side_effect=Exception("offline")):
            self.assertIsNone(fetch_text("https://example.com/a.sha256"))

    def test_parse_checksum_takes_first_token(self):
        self.assertEqual(
            parse_checksum("abc123  KoreanFilenameFixer-Windows.zip\n"),
            "abc123",
        )
        self.assertEqual(parse_checksum(""), "")
        self.assertEqual(parse_checksum(None), "")


class VerifyTests(unittest.TestCase):
    def test_verify_sha256_matches(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"content")
            path = f.name
        try:
            expected = hashlib.sha256(b"content").hexdigest()
            self.assertTrue(verify_sha256(path, expected))
            self.assertTrue(verify_sha256(path, expected.upper()))
            self.assertFalse(verify_sha256(path, "0" * 64))
            self.assertFalse(verify_sha256(path, ""))
        finally:
            os.remove(path)


class InstallDirTests(unittest.TestCase):
    def test_current_install_dir_is_none_when_not_frozen(self):
        self.assertFalse(getattr(sys, "frozen", False))
        self.assertIsNone(current_install_dir())

    def test_current_install_dir_uses_executable_when_frozen(self):
        with patch.object(sys, "frozen", True, create=True):
            with patch.object(sys, "executable", "/x/y/app.exe"):
                self.assertEqual(
                    current_install_dir(),
                    os.path.dirname(os.path.abspath("/x/y/app.exe")))

    def test_current_exe_path_joins_exe_name(self):
        self.assertEqual(
            current_exe_path("/x/y"), os.path.join("/x/y", WINDOWS_EXE_NAME))

    def test_is_writable_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(is_writable_dir(tmp))
        self.assertFalse(is_writable_dir(os.path.join(tmp, "missing")))


class ExtractTests(unittest.TestCase):
    def _make_zip(self, tmp: str, with_exe: bool) -> str:
        zip_path = os.path.join(tmp, WINDOWS_ZIP_NAME)
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("KoreanFilenameFixer/readme.txt", "hi")
            if with_exe:
                archive.writestr(
                    f"KoreanFilenameFixer/{WINDOWS_EXE_NAME}", "exe")
        return zip_path

    def test_extract_returns_dir_containing_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = os.path.join(tmp, "staging")
            result = extract_update(self._make_zip(tmp, True), staging)
            self.assertTrue(os.path.isfile(os.path.join(result, WINDOWS_EXE_NAME)))

    def test_extract_raises_when_exe_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                extract_update(
                    self._make_zip(tmp, False), os.path.join(tmp, "staging"))

    def test_staging_and_cleanup(self):
        path = staging_dir()
        self.assertTrue(os.path.isdir(path))
        cleanup_staging(path)
        self.assertFalse(os.path.exists(path))
        cleanup_staging(os.path.join(path, "missing"))  # should not raise


class BatchTests(unittest.TestCase):
    def test_batch_waits_swaps_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch_path = os.path.join(tmp, "update.bat")
            write_update_batch(
                batch_path, 1234,
                r"C:\App\KoreanFilenameFixer",
                r"C:\Temp\KFF_new\KoreanFilenameFixer",
                r"C:\App\KoreanFilenameFixer\KoreanFilenameFixer.exe",
                cleanup_paths=[os.path.join(tmp, "a.zip")],
            )
            with open(batch_path, encoding="ascii") as f:
                content = f.read()

        self.assertIn("1234", content)
        self.assertIn(r"C:\App\KoreanFilenameFixer", content)
        self.assertIn("KoreanFilenameFixer.exe", content)
        self.assertIn(":rollback", content)
        self.assertIn("tasklist", content)
        self.assertIn("a.zip", content)

    def test_batch_uses_crlf_line_endings(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch_path = os.path.join(tmp, "update.bat")
            write_update_batch(
                batch_path, 1, r"C:\App", r"C:\New", r"C:\App\app.exe")
            with open(batch_path, "rb") as f:
                raw = f.read()
        self.assertIn(b"\r\n", raw)
        self.assertNotRegex(raw.replace(b"\r\n", b""), rb"\n")

    def test_batch_writes_diagnostic_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch_path = os.path.join(tmp, "update.bat")
            write_update_batch(
                batch_path, 1, r"C:\App", r"C:\New", r"C:\App\app.exe")
            with open(batch_path, encoding="ascii") as f:
                content = f.read()
        self.assertIn("KFF_LOG", content)
        self.assertIn(":waitcap", content)
        self.assertIn("rollback", content)

    def test_batch_reads_tasklist_from_file_not_pipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch_path = os.path.join(tmp, "update.bat")
            write_update_batch(
                batch_path, 14552,
                r"C:\App\KoreanFilenameFixer",
                r"C:\Temp\KFF_new\KoreanFilenameFixer",
                r"C:\App\KoreanFilenameFixer\KoreanFilenameFixer.exe",
            )
            with open(batch_path, encoding="ascii") as f:
                content = f.read()

        # find가 파이프가 아닌 파일을 읽어야 키보드 대기가 구조적으로 불가능하다
        self.assertNotIn("| find", content)
        self.assertIn("KFF_WAIT", content)
        self.assertIn('find "%KFF_PID%" "%KFF_WAIT%"', content)

    def test_batch_filters_by_image_and_caps_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch_path = os.path.join(tmp, "update.bat")
            write_update_batch(
                batch_path, 16836,
                r"C:\App\KoreanFilenameFixer",
                r"C:\Temp\KFF_new\KoreanFilenameFixer",
                r"C:\App\KoreanFilenameFixer\KoreanFilenameFixer.exe",
            )
            with open(batch_path, encoding="ascii") as f:
                content = f.read()

        # PID 재사용 오탐 방지: 이미지명 필터
        self.assertIn("IMAGENAME eq %KFF_IMAGE%", content)
        self.assertIn("KFF_IMAGE=KoreanFilenameFixer.exe", content)
        # 무한 대기 방지: 최대 시도 후 진행
        self.assertIn("KFF_TRIES", content)
        self.assertIn("GEQ 180", content)


class MacUpdateTests(unittest.TestCase):
    def test_current_macos_app_is_none_when_not_frozen(self):
        self.assertFalse(getattr(sys, "frozen", False))
        with patch("sys.platform", "darwin"):
            self.assertIsNone(current_macos_app())

    def test_current_macos_app_finds_bundle_ancestor(self):
        exe = "/Applications/KFF.app/Contents/MacOS/KFF"
        with patch("sys.platform", "darwin"):
            with patch.object(sys, "frozen", True, create=True):
                with patch.object(sys, "executable", exe):
                    with patch("os.path.isdir", return_value=True):
                        self.assertEqual(current_macos_app(), "/Applications/KFF.app")

    def test_current_macos_app_is_none_off_darwin(self):
        with patch("sys.platform", "win32"):
            with patch.object(sys, "frozen", True, create=True):
                self.assertIsNone(current_macos_app())

    def test_extract_mac_app_returns_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, MACOS_ZIP_NAME)
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("KoreanFilenameFixer.app/Contents/MacOS/KFF", "exe")
            result = extract_mac_app(zip_path, os.path.join(tmp, "staging"))
            self.assertTrue(result.endswith(".app"))
            self.assertTrue(os.path.isdir(result))

    def test_extract_mac_app_raises_without_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, MACOS_ZIP_NAME)
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("readme.txt", "hi")
            with self.assertRaises(FileNotFoundError):
                extract_mac_app(zip_path, os.path.join(tmp, "staging"))

    def test_verify_bundle_calls_codesign_strict(self):
        with patch("selfupdate.subprocess.run") as run:
            run.return_value.returncode = 0
            self.assertTrue(verify_bundle("/Applications/KFF.app"))
            args = run.call_args.args[0]
            self.assertEqual(args[:4], ["codesign", "--verify", "--deep", "--strict"])

    def test_verify_bundle_returns_false_on_failure(self):
        with patch("selfupdate.subprocess.run",
                   side_effect=Exception("no codesign")):
            self.assertFalse(verify_bundle("/Applications/KFF.app"))

    def test_mac_script_uses_lf_and_is_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            script_path = os.path.join(tmp, "update.sh")
            write_mac_update_script(
                script_path, 4242,
                "/Applications/KFF.app", "/tmp/staging/KFF.app")
            with open(script_path, "rb") as f:
                raw = f.read()
            self.assertTrue(os.access(script_path, os.X_OK))
        self.assertIn(b"\n", raw)
        self.assertNotIn(b"\r\n", raw)

    def test_mac_script_waits_swaps_strips_and_reopens(self):
        with tempfile.TemporaryDirectory() as tmp:
            script_path = os.path.join(tmp, "update.sh")
            write_mac_update_script(
                script_path, 4242,
                "/Applications/My KFF.app", "/tmp/staging/KFF.app",
                cleanup_paths=[os.path.join(tmp, "a.zip")],
            )
            with open(script_path, encoding="utf-8") as f:
                content = f.read()

        self.assertIn("#!/bin/bash", content)
        self.assertIn('kill -0 "$KFF_PID"', content)
        self.assertIn("4242", content)
        self.assertIn("com.apple.quarantine", content)
        self.assertIn('open "$KFF_CURRENT"', content)
        self.assertIn(".bak", content)
        self.assertIn("a.zip", content)


if __name__ == "__main__":
    unittest.main()
