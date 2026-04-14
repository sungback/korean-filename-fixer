"""
converter.py
NFD(macOS) → NFC(Windows 호환) 파일명 변환 모듈

[배경 지식]
- 한글 파일명은 두 가지 방식으로 저장될 수 있다.
  - NFC: '가' 를 하나의 글자(완성형)로 저장  → Windows/Linux 기본
  - NFD: '가' 를 ㄱ+ㅏ 처럼 자모로 분해해서 저장 → macOS 기본
- macOS에서 만든 파일을 Windows에서 열면 파일명이 깨지는 이유가 바로 이것.
- 이 모듈은 NFD 파일명을 NFC로 바꿔주는 역할을 한다.
"""

import logging
import os
import shutil
import time
import unicodedata
import uuid
from dataclasses import dataclass
from typing import List


# ── 변환 결과를 담는 데이터 클래스 ──────────────────────────────────────────
# dataclass는 __init__, __repr__ 등을 자동으로 만들어주는 편의 기능이다.
@dataclass
class ConvertResult:
    path: str       # 변환 후 실제 경로 (실패 시 원본 경로)
    original: str   # 변환 전 파일명
    converted: str  # 변환 후 파일명
    status: str     # "converted"(변환됨) | "skipped"(불필요) | "error"(실패)
    error: str = "" # 오류 메시지 (오류 없으면 빈 문자열)


# ── 유틸리티 함수 ────────────────────────────────────────────────────────────

def is_nfd(name: str) -> bool:
    """
    파일명이 NFD 형태인지 확인한다.

    원리: NFD는 한 글자를 여러 코드포인트로 분해하므로 NFC보다 문자열 길이가 길다.
    예) NFD '가' → 길이 2 (ㄱ + ㅏ),  NFC '가' → 길이 1
    따라서 NFC로 바꿨을 때 길이가 달라지면 원본은 NFD라고 판단한다.
    """
    return len(name) != len(unicodedata.normalize('NFC', name))


def convert_name(name: str) -> str:
    """NFD 파일명을 NFC로 변환해서 반환한다."""
    return unicodedata.normalize('NFC', name)


# ── 파일 1개 변환 ────────────────────────────────────────────────────────────

def convert_file(filepath: str, retry: int = 5, retry_interval: float = 1.0) -> ConvertResult:
    """
    파일 또는 폴더 1개의 이름을 NFD → NFC로 변환한다.
    이미 NFC면 'skipped'를 반환하고 아무것도 하지 않는다.

    [왜 단순 rename을 안 쓰나?]
    Google Drive 같은 클라우드 폴더에서는 os.rename(NFD → NFC)을 해도
    동기화 클라이언트가 이름 변경을 감지하지 못한다.
    (macOS 파일시스템이 내부적으로 NFD/NFC를 같은 파일로 취급하기 때문)
    그래서 파일은 '복사 → 원본 삭제 → 임시파일 rename' 방식으로 처리한다.
    Google Drive는 이걸 '파일 삭제(NFD) + 새 파일 생성(NFC)'으로 인식해
    서버에도 NFC 이름으로 올바르게 동기화된다.
    """
    dirpath = os.path.dirname(filepath)  # 파일이 있는 폴더 경로
    name = os.path.basename(filepath)    # 파일명만 추출

    # NFD가 아니면 변환할 필요 없음
    if not is_nfd(name):
        return ConvertResult(filepath, name, name, "skipped")

    nfc_name = convert_name(name)
    new_path = os.path.join(dirpath, nfc_name)

    # 임시 파일명: 충돌 방지를 위해 UUID 8자리를 붙인다.
    # (같은 폴더에서 여러 파일을 동시에 변환할 때 tmp 경로가 겹치지 않도록)
    tmp_path = os.path.join(dirpath, f"__nfc_tmp_{uuid.uuid4().hex[:8]}__")

    # 파일이 잠겨 있을 수 있으므로 최대 retry 번 재시도한다
    for attempt in range(retry):
        try:
            if os.path.isdir(filepath):
                # 폴더는 복사가 불필요하므로 2단계 rename으로 처리
                # (NFD → 임시이름 → NFC : macOS가 NFD↔NFC를 동일 취급해
                #  한 번에 rename하면 무시될 수 있어서 중간 단계를 거친다)
                os.rename(filepath, tmp_path)
                os.rename(tmp_path, new_path)
            else:
                # 파일: 복사 → 원본 삭제 → rename
                shutil.copy2(filepath, tmp_path)  # 메타데이터(수정시각 등)까지 복사
                os.remove(filepath)               # 원본(NFD) 삭제
                os.rename(tmp_path, new_path)     # 임시파일을 NFC 이름으로 변경

            logging.info(f"Converted: {name!r} → {nfc_name!r}")
            return ConvertResult(new_path, name, nfc_name, "converted")

        except PermissionError:
            # 다른 프로세스가 파일을 열고 있으면 잠시 기다렸다가 재시도
            logging.warning(f"File locked, retrying ({attempt + 1}/{retry}): {filepath}")
            time.sleep(retry_interval)

        except Exception as e:
            # 예상치 못한 오류 발생 시:
            # 임시 파일만 남고 원본이 없는 상태라면 임시 파일을 원본 이름으로 복구
            if os.path.exists(tmp_path) and not os.path.exists(filepath):
                try:
                    os.rename(tmp_path, filepath)
                except Exception:
                    pass
            logging.error(f"Error converting {filepath}: {e}")
            return ConvertResult(filepath, name, nfc_name, "error", str(e))

    # retry 횟수를 모두 소진하면 실패 처리
    msg = f"{retry}회 재시도 후 실패"
    logging.error(f"Failed to convert {filepath}: {msg}")
    return ConvertResult(filepath, name, nfc_name, "error", msg)


# ── 폴더 전체 변환 ───────────────────────────────────────────────────────────

def convert_folder(folder: str) -> List[ConvertResult]:
    """
    지정된 폴더 아래의 모든 파일/폴더명을 NFD → NFC로 변환한다.

    [왜 깊은 경로부터 처리하나?]
    예) /드라이브/한글폴더/파일.txt  (폴더명, 파일명 모두 NFD)
    - 만약 '한글폴더'를 먼저 rename하면, 그 안의 '파일.txt' 경로가
      /드라이브/한글폴더_nfc/파일.txt로 바뀌어 기존에 수집한 경로가 깨진다.
    - 반대로 '파일.txt'를 먼저 처리한 뒤 '한글폴더'를 rename하면 문제없다.
    따라서 경로 깊이(/ 개수)가 많은 것부터 처리한다.
    """
    results: List[ConvertResult] = []

    # os.walk: 폴더를 재귀적으로 순회하며 (현재경로, 하위폴더목록, 파일목록)을 반환
    all_entries = []
    for root, dirs, files in os.walk(folder):
        for name in files:
            all_entries.append(os.path.join(root, name))
        for name in dirs:
            all_entries.append(os.path.join(root, name))

    # 경로 구분자(/)가 많을수록 깊은 경로 → 내림차순 정렬
    all_entries.sort(key=lambda p: p.count(os.sep), reverse=True)

    for entry in all_entries:
        # 상위 폴더가 이미 rename되어 이 경로가 더 이상 존재하지 않으면 건너뜀
        if not os.path.exists(entry):
            continue
        result = convert_file(entry)
        results.append(result)

    return results
