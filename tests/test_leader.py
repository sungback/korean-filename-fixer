import json
import os
import tempfile
import time
from typing import Callable
import unittest
from unittest.mock import Mock, patch

from leader import (
    LEADER_FILENAME,
    LeaderManager,
    get_hostname,
    get_machine_id,
    read_leader_file,
    release_leader_file,
    write_leader_file,
)


class LeaderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.folder = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_read_and_write_leader_file(self):
        ok = write_leader_file(
            self.folder,
            leader_id="mac-1234",
            hostname="MacBook-Pro",
            seq=1,
            status="active",
        )
        self.assertTrue(ok)

        data = read_leader_file(self.folder)
        self.assertIsNotNone(data)
        self.assertEqual(data["leader_id"], "mac-1234")
        self.assertEqual(data["hostname"], "MacBook-Pro")
        self.assertEqual(data["seq"], 1)
        self.assertEqual(data["status"], "active")
        self.assertIn("time", data)

    def test_read_leader_file_missing_or_corrupt(self):
        # 파일이 없을 때
        self.assertIsNone(read_leader_file(self.folder))

        # 손상된 JSON일 때
        path = os.path.join(self.folder, LEADER_FILENAME)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{invalid json")
        self.assertIsNone(read_leader_file(self.folder))

    def test_release_leader_file_removes_file_if_owned(self):
        write_leader_file(self.folder, "my-mac", "host1", 1)
        path = os.path.join(self.folder, LEADER_FILENAME)
        self.assertTrue(os.path.exists(path))

        # 다른 기기 ID로는 해제되지 않음
        release_leader_file(self.folder, "other-mac")
        self.assertTrue(os.path.exists(path))

        # 자신의 기기 ID로 해제하면 파일 삭제
        release_leader_file(self.folder, "my-mac")
        self.assertFalse(os.path.exists(path))

    def test_single_machine_becomes_leader(self):
        manager = LeaderManager(
            machine_id="mac-A",
            hostname="Host-A",
            heartbeat_interval=0.1,
            check_interval=0.05,
        )
        try:
            manager.start([self.folder])
            is_leader, host = manager.get_role_info(self.folder)
            self.assertTrue(is_leader)
            self.assertEqual(host, "Host-A")
            self.assertTrue(manager.is_folder_active(self.folder))
            self.assertTrue(manager.is_folder_active(os.path.join(self.folder, "sub", "file.txt")))
        finally:
            manager.stop()

        # stop 후 리더 파일이 정리되었는지 확인
        self.assertFalse(os.path.exists(os.path.join(self.folder, LEADER_FILENAME)))

    def test_two_machines_active_and_standby(self):
        # 기기 A가 먼저 리더 획득
        manager_a = LeaderManager(
            machine_id="mac-A",
            hostname="Host-A",
            heartbeat_interval=0.1,
            check_interval=0.05,
        )
        # 기기 B가 뒤이어 참여
        manager_b = LeaderManager(
            machine_id="mac-B",
            hostname="Host-B",
            heartbeat_interval=0.1,
            check_interval=0.05,
        )

        try:
            manager_a.start([self.folder])
            self.assertTrue(manager_a.is_folder_active(self.folder))

            manager_b.start([self.folder])
            # B는 A가 살아있으므로 Standby 상태여야 함
            is_leader_b, host_b = manager_b.get_role_info(self.folder)
            self.assertFalse(is_leader_b)
            self.assertEqual(host_b, "Host-A")
            self.assertFalse(manager_b.is_folder_active(self.folder))
        finally:
            manager_a.stop()
            manager_b.stop()

    @staticmethod
    def _wait_until(predicate: Callable[[], bool], timeout: float = 2.0, interval: float = 0.01) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return predicate()

    def test_standby_takes_over_when_leader_stops(self):
        manager_a = LeaderManager(
            machine_id="mac-A",
            hostname="Host-A",
            heartbeat_interval=0.1,
            check_interval=0.05,
        )
        manager_b = LeaderManager(
            machine_id="mac-B",
            hostname="Host-B",
            heartbeat_interval=0.1,
            check_interval=0.05,
        )

        try:
            manager_a.start([self.folder])
            self.assertTrue(manager_a.is_folder_active(self.folder))

            manager_b.start([self.folder])
            self.assertTrue(self._wait_until(lambda: not manager_b.is_folder_active(self.folder)))

            # A가 정상 종료 (리더 파일 삭제/해제)
            manager_a.stop()

            # B가 리더 승계할 때까지 대기
            self.assertTrue(
                self._wait_until(lambda: manager_b.is_folder_active(self.folder), timeout=2.0),
                "Standby machine failed to take over leadership after leader stopped",
            )
            is_leader, host = manager_b.get_role_info(self.folder)
            self.assertTrue(is_leader)
            self.assertEqual(host, "Host-B")
        finally:
            manager_b.stop()

    def test_standby_takes_over_on_heartbeat_timeout(self):
        # A가 리더 파일만 남기고 비정상 종료(스레드 사망 등)되었다고 가정
        write_leader_file(
            self.folder,
            leader_id="mac-A",
            hostname="Host-A",
            seq=10,
            status="active",
        )

        # 타임아웃을 0.2초로 짧게 설정
        manager_b = LeaderManager(
            machine_id="mac-B",
            hostname="Host-B",
            heartbeat_timeout=0.2,
            check_interval=0.05,
        )

        try:
            manager_b.start([self.folder])
            # 처음에는 A가 활성으로 보여 Standby
            self.assertFalse(manager_b.is_folder_active(self.folder))

            # 0.2초 타임아웃 경과 후 B가 리더 승계할 때까지 대기
            self.assertTrue(
                self._wait_until(lambda: manager_b.is_folder_active(self.folder), timeout=2.0),
                "Standby machine failed to take over leadership after heartbeat timeout",
            )
            is_leader, host = manager_b.get_role_info(self.folder)
            self.assertTrue(is_leader)
            self.assertEqual(host, "Host-B")
        finally:
            manager_b.stop()

    def test_on_role_change_callback_fires(self):
        changes = []

        def on_change(folder, is_leader, host):
            changes.append((folder, is_leader, host))

        manager = LeaderManager(
            machine_id="mac-A",
            hostname="Host-A",
            on_role_change=on_change,
        )
        try:
            manager.start([self.folder])
            self.assertTrue(len(changes) >= 1)
            self.assertEqual(changes[0], (os.path.abspath(self.folder), True, "Host-A"))
        finally:
            manager.stop()


if __name__ == "__main__":
    unittest.main()
