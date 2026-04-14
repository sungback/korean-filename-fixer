"""
diagnose_gdrive.py
Google Drive 폴더에서 NFD→NFC 변환이 실제로 적용되는지 단계별 진단
"""

import os
import shutil
import unicodedata
import time

GDRIVE = os.path.expanduser("~/내 드라이브")
TEST_NAME_NFC = "진단테스트.txt"
TEST_NAME_NFD = unicodedata.normalize("NFD", TEST_NAME_NFC)


def check(label, dirpath, target_nfc):
    """디렉토리를 직접 스캔해서 파일명 상태 출력"""
    for entry in os.scandir(dirpath):
        if unicodedata.normalize("NFC", entry.name) == target_nfc:
            is_nfd = unicodedata.normalize("NFC", entry.name) != entry.name
            form = "NFD" if is_nfd else "NFC"
            print(f"  [{label}] {form} | len={len(entry.name)} | {entry.name!r}")
            return entry.name
    print(f"  [{label}] 파일 없음")
    return None


print(f"=== Google Drive 진단 ===")
print(f"폴더: {GDRIVE}\n")

# 1. NFD 파일 생성
nfd_path = os.path.join(GDRIVE, TEST_NAME_NFD)
with open(nfd_path, "w") as f:
    f.write("test")
print("1. NFD 파일 생성")
check("생성 직후", GDRIVE, TEST_NAME_NFC)
time.sleep(1)

# 2. shutil.copy2 → 임시 파일
tmp_path = os.path.join(GDRIVE, "__nfc_tmp_diag__")
shutil.copy2(nfd_path, tmp_path)
print("\n2. shutil.copy2 → __nfc_tmp_diag__")
check("원본", GDRIVE, TEST_NAME_NFC)

# 3. 원본 삭제
os.remove(nfd_path)
print("\n3. 원본(NFD) 삭제")
time.sleep(0.5)

# 4. 임시 → NFC 이름으로 rename
nfc_path = os.path.join(GDRIVE, TEST_NAME_NFC)
os.rename(tmp_path, nfc_path)
print("\n4. __nfc_tmp_diag__ → NFC 이름으로 rename")
time.sleep(0.5)
result = check("rename 후", GDRIVE, TEST_NAME_NFC)

print("\n=== 결론 ===")
if result:
    is_nfd = unicodedata.normalize("NFC", result) != result
    if is_nfd:
        print("❌ rename 후에도 NFD → Google Drive 가상 FS가 NFC 저장을 거부함")
        print("   → Google Drive API로 서버 직접 rename 필요")
    else:
        print("✅ 로컬은 NFC로 저장됨 → Google Drive 동기화 지연 문제일 가능성")
        print("   → 동기화 완료 후 Windows에서 다시 확인 필요")
else:
    print("❌ 변환 후 파일을 찾을 수 없음")

# 정리
try:
    os.remove(nfc_path)
    print("\n테스트 파일 정리 완료")
except:
    pass
