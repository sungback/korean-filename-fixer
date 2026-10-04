"""
leader.py
다중 기기(Multi-Machine) 환경에서 동일 공유 폴더에 대해
단 1대의 기기만 실시간 감시를 수행하도록 조율하는 리더 선출(Active-Standby) 모듈.
"""

import json
import logging
import os
import socket
import threading
import time
import uuid
from typing import Callable

LEADER_FILENAME = ".kff_leader.json"
HEARTBEAT_INTERVAL = 10.0      # 리더가 하트비트를 갱신하는 주기 (초)
HEARTBEAT_TIMEOUT = 30.0       # 리더 무응답 시 사망으로 판정하는 시간 (초)
STANDBY_CHECK_INTERVAL = 5.0   # Standby 상태에서 리더 생존 여부를 확인하는 주기 (초)
STALE_FILE_AGE = 120.0         # 벽시계 기준 이 시간 이상 경과된 파일은 즉시 무효 판정 (초)


def get_machine_id() -> str:
    """기기별 고유 식별자를 반환한다 (호스트명 + MAC 주소 노드)."""
    try:
        return f"{socket.gethostname()}-{uuid.getnode()}"
    except Exception:
        return f"mac-{uuid.uuid4().hex[:8]}"


def get_hostname() -> str:
    """사람이 알아보기 쉬운 기기 호스트명을 반환한다."""
    try:
        return socket.gethostname() or "unknown-mac"
    except Exception:
        return "unknown-mac"


def read_leader_file(folder: str) -> dict | None:
    """폴더의 리더 파일을 안전하게 읽어온다."""
    path = os.path.join(folder, LEADER_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "leader_id" in data:
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError, PermissionError):
        pass
    return None


def write_leader_file(
    folder: str,
    leader_id: str,
    hostname: str,
    seq: int,
    status: str = "active",
) -> bool:
    """원자적(Atomic)으로 리더 파일을 기록한다 (임시 파일 작성 후 os.replace)."""
    path = os.path.join(folder, LEADER_FILENAME)
    tmp_path = os.path.join(folder, f"{LEADER_FILENAME}.tmp.{uuid.uuid4().hex[:6]}")
    payload = {
        "leader_id": leader_id,
        "hostname": hostname,
        "seq": seq,
        "status": status,
        "time": time.time(),
    }
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp_path, path)
        return True
    except OSError as e:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        logging.warning(f"Failed to write leader file in {folder}: {e}")
        return False


def release_leader_file(folder: str, leader_id: str):
    """자신이 리더인 경우 리더 파일을 즉시 정리하거나 released 상태로 변경한다."""
    path = os.path.join(folder, LEADER_FILENAME)
    try:
        data = read_leader_file(folder)
        if data and data.get("leader_id") == leader_id:
            try:
                os.remove(path)
            except OSError:
                write_leader_file(
                    folder,
                    leader_id,
                    get_hostname(),
                    int(data.get("seq", 0)) + 1,
                    status="released",
                )
    except Exception:
        pass


class _FolderLeaderState:
    def __init__(self, folder: str):
        self.folder = folder
        self.is_leader = False
        self.leader_host = ""
        self.leader_id = ""
        self.last_seq = -1
        self.last_seen_change = 0.0
        self.my_seq = 0


class LeaderManager:
    """여러 감시 폴더의 Active-Standby 리더 상태를 백그라운드에서 주기적으로 조율한다."""

    def __init__(
        self,
        on_role_change: Callable[[str, bool, str], None] | None = None,
        heartbeat_interval: float = HEARTBEAT_INTERVAL,
        heartbeat_timeout: float = HEARTBEAT_TIMEOUT,
        check_interval: float = STANDBY_CHECK_INTERVAL,
        machine_id: str | None = None,
        hostname: str | None = None,
    ):
        self.on_role_change = on_role_change
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.check_interval = check_interval
        self.machine_id = machine_id or get_machine_id()
        self.hostname = hostname or get_hostname()

        self._states: dict[str, _FolderLeaderState] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, folders: list[str]):
        """감시 폴더 목록을 등록하고 리더 선출 및 하트비트 스레드를 시작한다."""
        with self._lock:
            self.stop()
            self._stop_event.clear()
            self._states = {
                os.path.abspath(f): _FolderLeaderState(os.path.abspath(f))
                for f in folders if f and os.path.isdir(f)
            }
            # 첫 번째 평가를 즉시 동기 실행하여 빠른 역할 결정
            for folder, state in self._states.items():
                self._evaluate_folder(state, initial=True)

            self._thread = threading.Thread(
                target=self._run_loop,
                name="LeaderManagerThread",
                daemon=True,
            )
            self._thread.start()

    def stop(self):
        """하트비트 스레드를 중지하고, 자신이 리더였던 폴더의 리더 권한을 안전하게 해제한다."""
        with self._lock:
            self._stop_event.set()
            thread = self._thread
            states = list(self._states.values())
            self._states = {}
            self._thread = None

        if thread and thread.is_alive():
            thread.join(timeout=2.0)

        for state in states:
            if state.is_leader:
                release_leader_file(state.folder, self.machine_id)

    def is_folder_active(self, folder_or_path: str) -> bool:
        """해당 경로가 속한 폴더에서 자신이 Active(리더)인지 확인한다."""
        normalized = os.path.abspath(folder_or_path)
        with self._lock:
            if not self._states:
                # 등록된 폴더가 없으면 기본 True
                return True
            for folder, state in self._states.items():
                if normalized == folder or normalized.startswith(folder + os.sep):
                    return state.is_leader
        return True

    def get_role_info(self, folder: str) -> tuple[bool, str]:
        """(is_leader, leader_host) 튜플을 반환한다."""
        normalized = os.path.abspath(folder)
        with self._lock:
            state = self._states.get(normalized)
            if state:
                return state.is_leader, state.leader_host
        return True, self.hostname

    def _run_loop(self):
        while not self._stop_event.is_set():
            with self._lock:
                states = list(self._states.values())

            for state in states:
                if self._stop_event.is_set():
                    break
                self._evaluate_folder(state)

            self._stop_event.wait(self.check_interval)

    def _evaluate_folder(self, state: _FolderLeaderState, initial: bool = False):
        folder = state.folder
        if not os.path.isdir(folder):
            return

        data = read_leader_file(folder)
        now_mono = time.monotonic()
        now_wall = time.time()

        old_is_leader = state.is_leader
        old_leader_host = state.leader_host

        # Case 1: 리더 파일이 없거나 명시적으로 해제(released)된 경우 -> 즉시 리더 획득
        if data is None or data.get("status") == "released":
            state.my_seq += 1
            if write_leader_file(folder, self.machine_id, self.hostname, state.my_seq, status="active"):
                state.is_leader = True
                state.leader_host = self.hostname
                state.leader_id = self.machine_id
                state.last_seq = state.my_seq
                state.last_seen_change = now_mono

        # Case 2: 자신이 이미 리더인 경우 -> 하트비트 갱신
        elif data.get("leader_id") == self.machine_id:
            state.is_leader = True
            state.leader_host = self.hostname
            state.leader_id = self.machine_id
            state.my_seq += 1
            write_leader_file(folder, self.machine_id, self.hostname, state.my_seq, status="active")
            state.last_seq = state.my_seq
            state.last_seen_change = now_mono

        # Case 3: 다른 기기가 리더인 경우
        else:
            peer_id = str(data.get("leader_id", ""))
            peer_host = str(data.get("hostname", "other-mac"))
            peer_seq = int(data.get("seq", 0))
            peer_time = float(data.get("time", 0.0))

            # 3-1. 벽시계 기준 2분 이상 지난 고대 파일이면 사망으로 간주하여 즉시 승계
            is_stale_wall_clock = (now_wall - peer_time > STALE_FILE_AGE)

            # 3-2. 로컬 모노토닉 기준 시퀀스 변경 추적
            if peer_seq != state.last_seq:
                state.last_seq = peer_seq
                state.last_seen_change = now_mono
                is_timeout = False
            else:
                if state.last_seen_change == 0.0:
                    state.last_seen_change = now_mono
                is_timeout = (now_mono - state.last_seen_change >= self.heartbeat_timeout)

            # 만약 자신이 이전에 리더였는데 다른 기기가 리더 파일을 덮어쓴 경우(스플릿 브레인 경합):
            # 결정론적 기기 ID 비교로 타이브레이킹 (더 작은 ID가 승리)
            if state.is_leader and not is_timeout and not is_stale_wall_clock:
                if self.machine_id < peer_id:
                    # 자신이 승리 -> 리더 갱신
                    state.my_seq = max(state.my_seq, peer_seq) + 1
                    write_leader_file(folder, self.machine_id, self.hostname, state.my_seq, status="active")
                    state.is_leader = True
                    state.leader_host = self.hostname
                    state.leader_id = self.machine_id
                    state.last_seq = state.my_seq
                    state.last_seen_change = now_mono
                else:
                    # 상대방 승리 -> Standby로 전환
                    state.is_leader = False
                    state.leader_host = peer_host
                    state.leader_id = peer_id
            elif is_timeout or is_stale_wall_clock:
                # 상대방 무응답 사망 -> 승계
                logging.info(f"Leader timeout in {folder} (last: {peer_host}). Taking over leadership.")
                state.my_seq = max(state.my_seq, peer_seq) + 1
                if write_leader_file(folder, self.machine_id, self.hostname, state.my_seq, status="active"):
                    state.is_leader = True
                    state.leader_host = self.hostname
                    state.leader_id = self.machine_id
                    state.last_seq = state.my_seq
                    state.last_seen_change = now_mono
            else:
                # 상대방이 정상 활동 중 -> Standby 유지
                state.is_leader = False
                state.leader_host = peer_host
                state.leader_id = peer_id

        # 역할이 변경되었으면 콜백 호출
        if (state.is_leader != old_is_leader or state.leader_host != old_leader_host) and self.on_role_change:
            try:
                self.on_role_change(folder, state.is_leader, state.leader_host)
            except Exception as e:
                logging.warning(f"Error calling on_role_change callback: {e}")
