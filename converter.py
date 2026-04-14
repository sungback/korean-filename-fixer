"""
converter.py
NFD(macOS) → NFC(Windows 호환) 파일명 변환 모듈
"""

import os
import unicodedata
from dataclasses import dataclass
from typing import List


@dataclass
class ConvertResult:
    path: str
    original: str
    converted: str
    status: str  # "converted" | "skipped" | "error"
    error: str = ""


def is_nfd(name: str) -> bool:
    """파일명이 NFD 형태인지 확인"""
    return unicodedata.normalize('NFC', name) != name


def convert_name(name: str) -> str:
    """NFD 파일명을 NFC로 변환"""
    return unicodedata.normalize('NFC', name)


def convert_file(filepath: str) -> ConvertResult:
    """단일 파일 변환. 변환이 불필요하면 skipped 반환."""
    dirpath = os.path.dirname(filepath)
    name = os.path.basename(filepath)

    if not is_nfd(name):
        return ConvertResult(filepath, name, name, "skipped")

    nfc_name = convert_name(name)
    new_path = os.path.join(dirpath, nfc_name)

    try:
        os.rename(filepath, new_path)
        return ConvertResult(new_path, name, nfc_name, "converted")
    except Exception as e:
        return ConvertResult(filepath, name, nfc_name, "error", str(e))


def convert_folder(folder: str) -> List[ConvertResult]:
    """
    지정된 폴더 하위의 모든 파일/폴더명을 NFD→NFC 변환.
    깊은 경로부터 처리해 상위 폴더 rename 시 충돌 방지.
    """
    results: List[ConvertResult] = []

    # os.walk로 전체 경로 수집 후 깊이 역순 처리
    all_entries = []
    for root, dirs, files in os.walk(folder):
        for name in files:
            all_entries.append(os.path.join(root, name))
        for name in dirs:
            all_entries.append(os.path.join(root, name))

    # 깊은 경로 먼저 처리 (부모 폴더 rename 전에 자식 먼저)
    all_entries.sort(key=lambda p: p.count(os.sep), reverse=True)

    for entry in all_entries:
        if not os.path.exists(entry):
            # 상위 폴더가 이미 rename되어 경로가 바뀐 경우 건너뜀
            continue
        result = convert_file(entry)
        results.append(result)

    return results
