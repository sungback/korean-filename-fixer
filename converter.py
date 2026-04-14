"""
converter.py
NFD(macOS) → NFC(Windows 호환) 파일명 변환 모듈

macOS는 한글 파일명을 NFD(자모 분해)로 저장하고, Windows/Linux는 NFC(완성형)를 사용한다.
이 모듈은 NFD 파일명을 NFC로 바꿔 크로스 플랫폼 호환성을 확보한다.
"""

import logging
import os
import re
import shutil
import time
import unicodedata
import uuid
from dataclasses import dataclass


@dataclass
class ConvertResult:
    path: str       # 변환 후 실제 경로 (실패 시 원본 경로)
    original: str   # 변환 전 파일명
    converted: str  # 변환 후 파일명
    status: str     # "converted" | "skipped" | "error"
    error: str = ""


_IGNORED_TEMP_NAME_RE = re.compile(r"\.sb-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+$")


def is_nfd(name: str) -> bool:
    """NFD는 NFC보다 코드포인트가 많으므로 NFC 변환 후 길이 변화로 판단한다."""
    return len(name) != len(unicodedata.normalize('NFC', name))


def should_ignore_name(name: str) -> bool:
    """저장 중 생성되는 임시 파일명은 변환 대상에서 제외한다."""
    if name.startswith("__nfc_tmp_") and name.endswith("__"):
        return True
    return _IGNORED_TEMP_NAME_RE.search(name) is not None


# 초성(U+1100~U+1112), 중성(U+1161~U+1175), 종성(U+11A8~U+11C2) →
# 호환 자모(U+3131~U+3163) 매핑 테이블
_JAMO_TO_COMPAT: dict[int, int] = {}

_CHO  = [0x3131,0x3132,0x3134,0x3137,0x3138,0x3139,0x3141,0x3142,0x3143,
         0x3145,0x3146,0x3147,0x3148,0x3149,0x314A,0x314B,0x314C,0x314D,0x314E]
_JONG = [0x3131,0x3132,0x3133,0x3134,0x3135,0x3136,0x3137,0x3139,0x313A,
         0x313B,0x313C,0x313D,0x313E,0x313F,0x3140,0x3141,0x3142,0x3144,
         0x3145,0x3146,0x3147,0x3148,0x314A,0x314B,0x314C,0x314D,0x314E]

for _i, _c in enumerate(_CHO):
    _JAMO_TO_COMPAT[0x1100 + _i] = _c          # 초성
for _i in range(21):
    _JAMO_TO_COMPAT[0x1161 + _i] = 0x314F + _i  # 중성
for _i, _c in enumerate(_JONG):
    _JAMO_TO_COMPAT[0x11A8 + _i] = _c           # 종성


def nfd_to_visual(name: str) -> str:
    """NFD 자모를 화면에 분리되어 보이는 호환 자모로 변환한다.

    macOS는 Hangul Jamo(U+1100~U+11FF)를 폰트 수준에서 합쳐 렌더링하므로
    NFD 파일명이 합쳐진 한글처럼 보인다. Compatibility Jamo(U+3131~U+3163)로
    바꾸면 macOS에서도 ㅎㅏㄴ처럼 분리된 형태로 표시된다.
    """
    return ''.join(chr(_JAMO_TO_COMPAT.get(ord(c), ord(c))) for c in name)


def convert_file(filepath: str, retry: int = 5, retry_interval: float = 1.0) -> ConvertResult:
    """
    파일/폴더 1개를 NFD → NFC로 변환한다. 이미 NFC면 'skipped'를 반환한다.

    단순 os.rename(NFD→NFC)은 macOS 파일시스템이 두 이름을 동일하게 취급해
    Google Drive 동기화 클라이언트가 변경을 감지하지 못한다.
    파일은 copy2→삭제→rename, 폴더는 2단계 rename으로 처리한다.
    """
    dirpath = os.path.dirname(filepath)
    name = os.path.basename(filepath)

    if should_ignore_name(name):
        return ConvertResult(filepath, name, name, "skipped")

    if not is_nfd(name):
        return ConvertResult(filepath, name, name, "skipped")

    nfc_name = unicodedata.normalize('NFC', name)
    new_path = os.path.join(dirpath, nfc_name)
    # UUID로 tmp 경로를 고유하게 만들어 동시 변환 시 충돌을 방지한다
    tmp_path = os.path.join(dirpath, f"__nfc_tmp_{uuid.uuid4().hex[:8]}__")

    for attempt in range(retry):
        try:
            if os.path.isdir(filepath):
                # 폴더: NFD↔NFC 동일 취급 우회를 위해 중간 이름을 거친다
                os.rename(filepath, tmp_path)
                os.rename(tmp_path, new_path)
            else:
                # 파일: Google Drive가 삭제(NFD)+생성(NFC)으로 인식해 서버에도 NFC로 반영된다
                shutil.copy2(filepath, tmp_path)
                os.remove(filepath)
                os.rename(tmp_path, new_path)

            logging.info(f"Converted: {name!r} → {nfc_name!r}")
            return ConvertResult(new_path, name, nfc_name, "converted")

        except PermissionError:
            logging.warning(f"File locked, retrying ({attempt + 1}/{retry}): {filepath}")
            time.sleep(retry_interval)

        except Exception as e:
            # 임시 파일만 남고 원본이 없으면 원본 이름으로 복구
            if os.path.exists(tmp_path) and not os.path.exists(filepath):
                try:
                    os.rename(tmp_path, filepath)
                except Exception:
                    pass
            logging.error(f"Error converting {filepath}: {e}")
            return ConvertResult(filepath, name, nfc_name, "error", str(e))

    msg = f"{retry}회 재시도 후 실패"
    logging.error(f"Failed to convert {filepath}: {msg}")
    return ConvertResult(filepath, name, nfc_name, "error", msg)


def convert_folder(folder: str) -> list[ConvertResult]:
    """
    폴더 하위의 모든 파일/폴더명을 NFD → NFC로 변환한다.
    깊은 경로부터 처리해 상위 폴더 rename 시 경로 충돌을 방지한다.
    """
    all_entries = []
    for root, dirs, files in os.walk(folder):
        for name in files:
            all_entries.append(os.path.join(root, name))
        for name in dirs:
            all_entries.append(os.path.join(root, name))

    all_entries.sort(key=lambda p: p.count(os.sep), reverse=True)

    results = []
    for entry in all_entries:
        if not os.path.exists(entry):
            continue
        if should_ignore_name(os.path.basename(entry)):
            continue
        results.append(convert_file(entry))

    return results
