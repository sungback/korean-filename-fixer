import os
import sys
import tempfile
import threading
import unicodedata
import unittest
from unittest.mock import patch

from converter import (
    clean_exclude_patterns,
    ConvertResult,
    convert_file,
    convert_folder,
    folder_after_results,
    is_nfd,
    plan_file,
    preview_folder,
    should_exclude_path,
    should_ignore_name,
    should_run_startup_scan,
    startup_scan_skip_reason,
    STARTUP_SCAN_ENTRY_LIMIT,
)


def nfd_name(text: str) -> str:
    return unicodedata.normalize("NFD", text)


def has_distinct_normalized_entries(folder: str) -> bool:
    return len(os.listdir(folder)) >= 2


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

    def test_convert_file_updates_mtime_to_force_drive_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_path = os.path.join(tmp, nfd_name("동기화.txt"))
            with open(original_path, "w", encoding="utf-8") as f:
                f.write("content")
            old_timestamp = 946684800.0
            os.utime(original_path, (old_timestamp, old_timestamp))

            result = convert_file(original_path)

            self.assertEqual(result.status, "converted")
            self.assertGreater(os.stat(result.path).st_mtime, old_timestamp + 3600)

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

    def test_convert_folder_stops_after_cancel_event_is_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for index in range(3):
                path = os.path.join(tmp, f"file-{index}.txt")
                with open(path, "w", encoding="utf-8") as f:
                    f.write("content")
                paths.append(path)

            cancel_event = threading.Event()

            def convert_and_cancel(path):
                cancel_event.set()
                return ConvertResult(path, os.path.basename(path), os.path.basename(path), "skipped")

            with patch("converter.convert_file", side_effect=convert_and_cancel) as convert:
                results = convert_folder(tmp, cancel_event=cancel_event)

            self.assertEqual(len(results), 1)
            convert.assert_called_once()

    def test_convert_folder_skips_walk_when_cancelled_before_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, nfd_name("취소.txt"))
            with open(path, "w", encoding="utf-8") as f:
                f.write("content")
            cancel_event = threading.Event()
            cancel_event.set()

            results = convert_folder(tmp, cancel_event=cancel_event)

            self.assertEqual(results, [])

    def test_convert_folder_yields_periodically_during_large_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index in range(205):
                path = os.path.join(tmp, f"file-{index}.txt")
                with open(path, "w", encoding="utf-8") as f:
                    f.write("content")

            def skip(path):
                return ConvertResult(path, os.path.basename(path), os.path.basename(path), "skipped")

            with patch("converter.convert_file", side_effect=skip):
                with patch("converter.time.sleep") as sleep:
                    convert_folder(tmp)

            self.assertTrue(any(call.args == (0,) for call in sleep.call_args_list))

    def test_convert_folder_reports_collection_and_processing_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index in range(3):
                path = os.path.join(tmp, f"file-{index}.txt")
                with open(path, "w", encoding="utf-8") as f:
                    f.write("content")
            progress_events = []

            def skip(path):
                return ConvertResult(path, os.path.basename(path), os.path.basename(path), "skipped")

            with patch("converter.convert_file", side_effect=skip):
                convert_folder(tmp, progress_callback=progress_events.append)

            self.assertIn(("collect", 3, None), progress_events)
            self.assertIn(("convert", 0, 3), progress_events)
            self.assertIn(("convert", 3, 3), progress_events)

    def test_plan_file_returns_preview_for_convertible_nfd_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_path = os.path.join(tmp, nfd_name("미리보기.txt"))
            with open(original_path, "w", encoding="utf-8") as f:
                f.write("preview")

            result = plan_file(original_path)

            self.assertEqual(result.status, "preview")
            self.assertEqual(result.converted, "미리보기.txt")
            self.assertTrue(os.path.exists(original_path))

    def test_plan_file_previews_drivefs_cloud_nfd_when_local_name_is_nfc(self):
        with tempfile.TemporaryDirectory() as tmp:
            local_path = os.path.join(tmp, "서버이름.txt")
            with open(local_path, "w", encoding="utf-8") as f:
                f.write("content")

            with patch("converter._drivefs_cloud_filename", return_value=nfd_name("서버이름.txt"), create=True):
                result = plan_file(local_path)

            self.assertEqual(result.status, "preview")
            self.assertEqual(result.original, nfd_name("서버이름.txt"))
            self.assertEqual(result.converted, "서버이름.txt")
            self.assertEqual(result.path, local_path)

    def test_convert_file_waits_for_drivefs_stage_before_final_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            local_path = os.path.join(tmp, "서버반영.txt")
            with open(local_path, "w", encoding="utf-8") as f:
                f.write("content")
            original_cloud_name = nfd_name("서버반영.txt")
            expected_path = local_path

            real_rename = os.rename
            rename_calls = []

            def record_rename(src, dst):
                rename_calls.append((os.path.basename(src), os.path.basename(dst)))
                return real_rename(src, dst)

            def wait_for_cloud_name(path, expected_name, timeout=None):
                if expected_name.startswith("__nfc_tmp_"):
                    self.assertTrue(os.path.exists(path))
                    self.assertFalse(os.path.exists(expected_path))
                else:
                    self.assertEqual(expected_name, "서버반영.txt")
                    self.assertTrue(os.path.exists(expected_path))
                return True

            with patch("converter._drivefs_cloud_filename", return_value=original_cloud_name, create=True):
                with patch("converter._wait_for_drivefs_cloud_filename", side_effect=wait_for_cloud_name, create=True) as wait:
                    with patch("converter.os.rename", side_effect=record_rename):
                        result = convert_file(local_path)

            self.assertEqual(result.status, "converted")
            self.assertEqual(result.path, expected_path)
            self.assertEqual(len(rename_calls), 2)
            self.assertEqual(wait.call_count, 2)

    def test_convert_directory_waits_for_drivefs_stage_before_final_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            local_path = os.path.join(tmp, "서버폴더")
            os.makedirs(local_path)
            original_cloud_name = nfd_name("서버폴더")

            with patch("converter._drivefs_cloud_filename", return_value=original_cloud_name, create=True):
                with patch("converter._wait_for_drivefs_cloud_filename", return_value=True, create=True) as wait:
                    result = convert_file(local_path)

            self.assertEqual(result.status, "converted")
            self.assertEqual(result.path, local_path)
            self.assertTrue(os.path.isdir(local_path))
            self.assertEqual(wait.call_count, 2)

    def test_plan_file_reports_conflict_when_target_name_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_path = os.path.join(tmp, nfd_name("한글.txt"))
            with open(original_path, "w", encoding="utf-8") as f:
                f.write("content")
            conflict_path = os.path.join(tmp, "한글.txt")
            with open(conflict_path, "w", encoding="utf-8") as f:
                f.write("existing")
            if not has_distinct_normalized_entries(tmp):
                self.skipTest("filesystem treats NFD/NFC names as the same entry")

            result = plan_file(original_path)

            self.assertEqual(result.status, "conflict")
            self.assertIn("이미 존재", result.error)
            self.assertTrue(os.path.exists(original_path))
            self.assertTrue(os.path.exists(conflict_path))

    def test_convert_file_preserves_conflict_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_path = os.path.join(tmp, nfd_name("충돌.txt"))
            with open(original_path, "w", encoding="utf-8") as f:
                f.write("content")
            conflict_path = os.path.join(tmp, "충돌.txt")
            with open(conflict_path, "w", encoding="utf-8") as f:
                f.write("existing")
            if not has_distinct_normalized_entries(tmp):
                self.skipTest("filesystem treats NFD/NFC names as the same entry")

            result = convert_file(original_path)

            self.assertEqual(result.status, "conflict")
            self.assertIn("이미 존재", result.error)
            self.assertTrue(os.path.exists(original_path))
            self.assertTrue(os.path.exists(conflict_path))

    def test_convert_file_restores_original_when_final_rename_permission_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_path = os.path.join(tmp, nfd_name("실패.txt"))
            expected_path = os.path.join(tmp, "실패.txt")
            with open(original_path, "w", encoding="utf-8") as f:
                f.write("content")
            expected_path_is_alias = os.path.exists(expected_path)

            real_rename = os.rename

            def fail_final_rename(src, dst):
                if os.path.basename(src).startswith("__nfc_tmp_") and dst == expected_path:
                    raise PermissionError("simulated final rename failure")
                return real_rename(src, dst)

            with patch("converter.os.rename", side_effect=fail_final_rename):
                result = convert_file(original_path, retry=1, retry_interval=0)

            self.assertEqual(result.status, "error")
            self.assertTrue(os.path.exists(original_path))
            if not expected_path_is_alias:
                self.assertFalse(os.path.exists(expected_path))
            self.assertFalse(any(name.startswith("__nfc_tmp_") for name in os.listdir(tmp)))

    def test_convert_directory_restores_original_when_final_rename_permission_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_path = os.path.join(tmp, nfd_name("실패폴더"))
            expected_path = os.path.join(tmp, "실패폴더")
            os.makedirs(original_path)
            expected_path_is_alias = os.path.exists(expected_path)

            real_rename = os.rename

            def fail_final_rename(src, dst):
                if os.path.basename(src).startswith("__nfc_tmp_") and dst == expected_path:
                    raise PermissionError("simulated final rename failure")
                return real_rename(src, dst)

            with patch("converter.os.rename", side_effect=fail_final_rename):
                result = convert_file(original_path, retry=1, retry_interval=0)

            self.assertEqual(result.status, "error")
            self.assertTrue(os.path.isdir(original_path))
            if not expected_path_is_alias:
                self.assertFalse(os.path.exists(expected_path))
            self.assertFalse(any(name.startswith("__nfc_tmp_") for name in os.listdir(tmp)))

    @unittest.skipIf(sys.platform == "win32", "Windows symlink behavior varies by runner")
    def test_convert_file_preserves_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target_path = os.path.join(tmp, "target.txt")
            with open(target_path, "w", encoding="utf-8") as f:
                f.write("target")

            original_path = os.path.join(tmp, nfd_name("링크.txt"))
            try:
                os.symlink(target_path, original_path)
            except (OSError, NotImplementedError) as e:
                self.skipTest(f"symlink unavailable: {e}")

            result = convert_file(original_path)
            expected_path = os.path.join(tmp, "링크.txt")

            self.assertEqual(result.status, "converted")
            self.assertTrue(os.path.islink(expected_path))
            self.assertTrue(os.path.samefile(os.readlink(expected_path), target_path))

    @unittest.skipIf(sys.platform == "win32", "Windows symlink behavior varies by runner")
    def test_convert_folder_handles_broken_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target_path = os.path.join(tmp, "missing-target.txt")
            original_path = os.path.join(tmp, nfd_name("깨진링크.txt"))
            try:
                os.symlink(target_path, original_path)
            except (OSError, NotImplementedError) as e:
                self.skipTest(f"symlink unavailable: {e}")

            results = convert_folder(tmp)
            expected_path = os.path.join(tmp, "깨진링크.txt")
            converted = [result for result in results if result.status == "converted"]

            self.assertEqual(len(converted), 1)
            self.assertTrue(os.path.islink(expected_path))
            self.assertEqual(os.readlink(expected_path), target_path)

    def test_convert_folder_can_include_selected_root_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_path = os.path.join(tmp, nfd_name("루트폴더"))
            os.makedirs(root_path)
            child_path = os.path.join(root_path, nfd_name("하위.txt"))
            with open(child_path, "w", encoding="utf-8") as f:
                f.write("child")

            results = convert_folder(root_path, include_root=True)
            expected_root = os.path.join(tmp, "루트폴더")
            expected_child = os.path.join(expected_root, "하위.txt")
            converted_names = {
                result.converted for result in results if result.status == "converted"
            }

            self.assertIn("루트폴더", converted_names)
            self.assertIn("하위.txt", converted_names)
            self.assertTrue(os.path.isdir(expected_root))
            self.assertTrue(os.path.exists(expected_child))

    def test_preview_folder_reports_preview_and_skipped_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            preview_path = os.path.join(tmp, nfd_name("예정.txt"))
            skipped_path = os.path.join(tmp, "already-nfc.txt")

            with open(preview_path, "w", encoding="utf-8") as f:
                f.write("preview")
            with open(skipped_path, "w", encoding="utf-8") as f:
                f.write("skip")

            results = preview_folder(tmp)
            statuses = {result.converted: result.status for result in results}

            self.assertEqual(statuses["예정.txt"], "preview")
            self.assertEqual(statuses["already-nfc.txt"], "skipped")


class IgnoreNameTests(unittest.TestCase):
    def test_ignores_nfc_tmp_names(self):
        self.assertTrue(should_ignore_name("__nfc_tmp_abc123__"))
        self.assertTrue(should_ignore_name("__nfc_tmp_00000000__"))
        self.assertFalse(should_ignore_name("__nfc_tmp_abc123"))  # 끝 __ 없음
        self.assertFalse(should_ignore_name("normal.txt"))

    def test_ignores_sb_temp_names(self):
        self.assertTrue(should_ignore_name("document.sb-abc123-def456"))
        self.assertTrue(should_ignore_name("file.sb-A1-B2-C3"))
        self.assertFalse(should_ignore_name("file.sb-abc"))   # 세그먼트 1개
        self.assertFalse(should_ignore_name("file.sb"))
        self.assertFalse(should_ignore_name("normal.txt"))


class ScanPolicyTests(unittest.TestCase):
    def test_should_run_startup_scan_requires_existing_folder_and_enabled_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(should_run_startup_scan(tmp, True))
            self.assertFalse(should_run_startup_scan(tmp, False))

    def test_should_run_startup_scan_rejects_missing_folder(self):
        self.assertFalse(should_run_startup_scan("", True))
        self.assertFalse(should_run_startup_scan("/path/does/not/exist", True))

    def test_should_run_startup_scan_skips_likely_sync_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            drive_root = os.path.join(
                tmp,
                "Library",
                "CloudStorage",
                "GoogleDrive-user@example.com",
                "내 드라이브",
            )
            nested_folder = os.path.join(drive_root, "Project")
            os.makedirs(nested_folder)

            self.assertFalse(should_run_startup_scan(drive_root, True))
            self.assertIn("동기화", startup_scan_skip_reason(drive_root, True))
            self.assertTrue(should_run_startup_scan(nested_folder, True))

    def test_should_run_startup_scan_skips_large_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index in range(3):
                open(os.path.join(tmp, f"file-{index}.txt"), "w").close()

            with patch("converter.STARTUP_SCAN_ENTRY_LIMIT", 2):
                self.assertFalse(should_run_startup_scan(tmp, True))
                self.assertIn("항목", startup_scan_skip_reason(tmp, True))


class FolderAfterResultsTests(unittest.TestCase):
    def _result(self, path, original, status="converted"):
        return ConvertResult(path, original, os.path.basename(path), status, "")

    def test_returns_new_nfc_path_when_nfd_root_folder_was_converted(self):
        nfd = nfd_name("한글")
        nfc = "한글"
        results = [ConvertResult(f"/tmp/{nfc}", nfd, nfc, "converted", "")]
        self.assertEqual(folder_after_results(f"/tmp/{nfd}", results), f"/tmp/{nfc}")

    def test_returns_original_when_no_matching_result(self):
        results = [self._result("/tmp/다른폴더", "다른폴더")]
        self.assertEqual(folder_after_results("/tmp/한글", results), "/tmp/한글")

    def test_returns_original_when_result_status_is_not_converted(self):
        results = [self._result("/tmp/한글", "한글", status="skipped")]
        self.assertEqual(folder_after_results("/tmp/한글", results), "/tmp/한글")

    def test_returns_original_when_result_is_in_different_parent(self):
        results = [self._result("/other/한글", "한글")]
        self.assertEqual(folder_after_results("/tmp/한글", results), "/tmp/한글")

    def test_returns_original_when_results_empty(self):
        self.assertEqual(folder_after_results("/tmp/폴더", []), "/tmp/폴더")


if __name__ == "__main__":
    unittest.main()
