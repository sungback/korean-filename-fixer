import hashlib
import os
import subprocess
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
    same_drive,
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
            with open(batch_path, encoding="utf-8") as f:
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
            with open(batch_path, encoding="utf-8") as f:
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
            with open(batch_path, encoding="utf-8") as f:
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
            with open(batch_path, encoding="utf-8") as f:
                content = f.read()

        # PID 재사용 오탐 방지: 이미지명 필터
        self.assertIn("IMAGENAME eq %KFF_IMAGE%", content)
        self.assertIn("KFF_IMAGE=KoreanFilenameFixer.exe", content)
        # 무한 대기 방지: 최대 시도 후 진행
        self.assertIn("KFF_TRIES", content)
        self.assertIn("GEQ 180", content)

    def test_batch_retries_moves_and_verifies_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch_path = os.path.join(tmp, "update.bat")
            write_update_batch(
                batch_path, 7,
                r"C:\App\KoreanFilenameFixer",
                r"C:\Temp\KFF_new\KoreanFilenameFixer",
                r"C:\App\KoreanFilenameFixer\KoreanFilenameFixer.exe",
            )
            with open(batch_path, encoding="utf-8") as f:
                content = f.read()

        self.assertIn("for /L %%i in (1,1,5)", content)
        self.assertIn("KFF_NEW_EXE", content)
        self.assertIn("move-aside failed", content)
        self.assertIn("move-in failed", content)
        self.assertIn("rollback-ok exe restored", content)
        self.assertIn("rollback-FAILED", content)
        # move 실패 사유는 진단용으로 로그에 남긴다 (원인 추적).
        self.assertIn(
            'move "%KFF_CURRENT%" "%KFF_BAK%" >> "%KFF_LOG%" 2>&1',
            content)
        self.assertIn(
            'move "%KFF_NEW%" "%KFF_CURRENT%" >> "%KFF_LOG%" 2>&1',
            content)


class BatchSafetyTests(unittest.TestCase):
    def _write(self, tmp: str, current: str, new: str) -> str:
        batch_path = os.path.join(tmp, "update.bat")
        write_update_batch(
            batch_path, 7, current, new,
            current + "\\" + WINDOWS_EXE_NAME,
        )
        with open(batch_path, "rb") as f:
            raw = f.read()
        return raw.decode("utf-8")

    def test_aside_failed_path_never_touches_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = self._write(
                tmp,
                r"C:\App\KoreanFilenameFixer",
                r"C:\Temp\KFF_new\KoreanFilenameFixer",
            )

        self.assertIn(":aside_failed", content)
        self.assertIn(":movein_failed", content)
        section = content.split(":aside_failed")[1].split("exit /b 1")[0]
        self.assertNotIn('rmdir /s /q "%KFF_CURRENT%"', section)
        self.assertIn("CURRENT untouched", section)

    def test_bak_is_rotated_instead_of_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = self._write(
                tmp,
                r"C:\App\KoreanFilenameFixer",
                r"C:\Temp\KFF_new\KoreanFilenameFixer",
            )

        self.assertIn("KFF_BAK_PREV", content)
        self.assertNotIn(
            'if exist "%KFF_BAK%" rmdir /s /q "%KFF_BAK%"', content)

    def test_batch_is_utf8_without_bom_and_switches_codepage_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch_path = os.path.join(tmp, "update.bat")
            write_update_batch(
                batch_path, 7,
                r"C:\App\KoreanFilenameFixer",
                r"C:\Temp\KFF_new\KoreanFilenameFixer",
                r"C:\App\KoreanFilenameFixer\KoreanFilenameFixer.exe",
            )
            with open(batch_path, "rb") as f:
                raw = f.read()

        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        content = raw.decode("utf-8")
        self.assertLess(content.index("chcp 65001"),
                        content.index('set "KFF_CURRENT='))

    def test_korean_paths_survive_batch_roundtrip(self):
        current = r"C:\사용자\홍길동\KoreanFilenameFixer"
        with tempfile.TemporaryDirectory() as tmp:
            content = self._write(
                tmp, current, os.path.join(tmp, "새버전"))
            self.assertIn(current, content)
            self.assertIn("새버전", content)
            self.assertNotIn("?", content)

    def test_batch_uses_stdin_safe_waits(self):
        # timeout은 stdin이 없을 때 즉시 실패하므로 ping 대기를 쓴다.
        with tempfile.TemporaryDirectory() as tmp:
            batch_path = os.path.join(tmp, "update.bat")
            write_update_batch(
                batch_path, 7,
                r"C:\App\KoreanFilenameFixer",
                r"C:\Temp\KFF_new\KoreanFilenameFixer",
                r"C:\App\KoreanFilenameFixer\KoreanFilenameFixer.exe",
            )
            with open(batch_path, encoding="utf-8") as f:
                content = f.read()

        self.assertNotIn("timeout /t", content)
        self.assertIn("ping -n", content)


@unittest.skipUnless(sys.platform == "win32", "실제 cmd 실행은 Windows에서만")
class BatchLiveTests(unittest.TestCase):
    """생성된 배치를 cmd로 직접 실행해 삭제 버그 회귀를 검증한다."""

    def _write(self, tmp: str, current: str, new: str,
               cleanup: list[str]) -> str:
        batch_path = os.path.join(tmp, "kff_self_update.bat")
        write_update_batch(
            batch_path, 2147483647, current, new,
            current + "\\" + WINDOWS_EXE_NAME,
            cleanup_paths=cleanup,
        )
        return batch_path

    def _run(self, batch_path: str) -> tuple[int, str]:
        proc = subprocess.run(
            ["cmd", "/c", batch_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=120,
        )
        with open(batch_path + ".log", encoding="utf-8") as f:
            return proc.returncode, f.read()

    def test_aside_failure_keeps_current_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            current = os.path.join(tmp, "KoreanFilenameFixer")
            os.makedirs(current)
            marker = os.path.join(current, "marker.txt")
            with open(marker, "w", encoding="utf-8") as f:
                f.write("keep me")
            with open(os.path.join(current, WINDOWS_EXE_NAME), "w") as f:
                f.write("current exe")
            staging = os.path.join(tmp, "staging")
            new = os.path.join(staging, "KoreanFilenameFixer")
            os.makedirs(new)
            with open(os.path.join(new, WINDOWS_EXE_NAME), "w") as f:
                f.write("new")
            batch_path = self._write(tmp, current, new, [staging])
            old_cwd = os.getcwd()
            os.chdir(current)  # CWD 잠금으로 move-aside 강제 실패
            try:
                code, log = self._run(batch_path)
            finally:
                os.chdir(old_cwd)

            self.assertEqual(code, 1)
            self.assertIn("aside-failed CURRENT untouched", log)
            self.assertTrue(os.path.isfile(marker))
            with open(marker, encoding="utf-8") as f:
                self.assertEqual(f.read(), "keep me")

    def test_movein_failure_restores_bak(self):
        with tempfile.TemporaryDirectory() as tmp:
            current = os.path.join(tmp, "KoreanFilenameFixer")
            os.makedirs(current)
            marker = os.path.join(current, "marker.txt")
            with open(marker, "w", encoding="utf-8") as f:
                f.write("keep me")
            with open(os.path.join(current, WINDOWS_EXE_NAME), "w") as f:
                f.write("current exe")
            staging = os.path.join(tmp, "staging")
            os.makedirs(staging)
            batch_path = self._write(
                tmp, current, os.path.join(staging, "gone"), [staging])
            code, log = self._run(batch_path)

            self.assertEqual(code, 1)
            self.assertIn("rollback-ok", log)
            self.assertTrue(os.path.isfile(marker))
            with open(marker, encoding="utf-8") as f:
                self.assertEqual(f.read(), "keep me")


class SameDriveTests(unittest.TestCase):
    def test_same_dir_is_same_drive(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(same_drive(tmp, os.path.join(tmp, "sub")))

    def test_drive_letter_compare_is_case_insensitive(self):
        self.assertTrue(same_drive(r"C:\a", r"c:\b"))


class MacUpdateTests(unittest.TestCase):
    def test_current_macos_app_is_none_when_not_frozen(self):
        self.assertFalse(getattr(sys, "frozen", False))
        with patch("sys.platform", "darwin"):
            self.assertIsNone(current_macos_app())

    def test_current_macos_app_finds_bundle_ancestor(self):
        exe = "/Applications/KFF.app/Contents/MacOS/KFF"
        expected = os.path.abspath(exe)
        while not expected.endswith(".app"):
            expected = os.path.dirname(expected)
        with patch("sys.platform", "darwin"):
            with patch.object(sys, "frozen", True, create=True):
                with patch.object(sys, "executable", exe):
                    with patch("os.path.isdir", return_value=True):
                        self.assertEqual(current_macos_app(), expected)

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

    @unittest.skipUnless(sys.platform == "darwin", "ditto는 macOS에만 있음")
    def test_extract_mac_app_preserves_symlinks(self):
        import subprocess as sp
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = os.path.join(tmp, "src", "KFF.app", "Contents")
            os.makedirs(app_dir)
            with open(os.path.join(app_dir, "real.dylib"), "w") as f:
                f.write("x")
            os.symlink("real.dylib", os.path.join(app_dir, "link.dylib"))
            zip_path = os.path.join(tmp, MACOS_ZIP_NAME)
            sp.run(
                ["ditto", "-c", "-k", "--keepParent",
                 os.path.join(tmp, "src", "KFF.app"), zip_path],
                check=True,
            )
            result = extract_mac_app(zip_path, os.path.join(tmp, "staging"))
            self.assertTrue(os.path.islink(os.path.join(result, "Contents", "link.dylib")))

    def test_extract_mac_app_raises_when_ditto_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, MACOS_ZIP_NAME)
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("KFF.app/Contents/x", "x")
            with patch("sys.platform", "darwin"):
                with patch("selfupdate.subprocess.run") as run:
                    run.return_value.returncode = 1
                    with self.assertRaises(RuntimeError):
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
