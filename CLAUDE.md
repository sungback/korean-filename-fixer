# Korean Filename Fixer — 프로젝트 규칙

## 개요
macOS에서 한글 파일명을 NFD → NFC로 변환해 Windows/Linux와의 호환성을 확보하는 데스크톱 앱.

## 기술 스택
- **언어**: Python 3.12
- **GUI**: tkinter (scrolledtext, ttk)
- **파일 감시**: watchdog (FSEventsObserver → PollingObserver 폴백)
- **tray 아이콘**: AppKit (pyobjc), macOS 전용
- **빌드**: PyInstaller → `bash build.sh`
- **플랫폼**: macOS 전용

## 파일 구조
| 파일 | 역할 |
|---|---|
| `main.py` | 진입점 — 로깅 초기화 후 App 실행 |
| `converter.py` | NFD→NFC 변환 로직 (파일/폴더) |
| `watcher.py` | 실시간 폴더 감시 (NFDHandler, FolderWatcher) |
| `gui.py` | tkinter GUI, 트레이 아이콘, 설정 저장 |

## 빌드
```bash
bash build.sh
# 결과물: dist/KoreanFilenameFixer.app
#         dist/KoreanFilenameFixer.app.zip  (배포용)
```

## 핵심 설계 결정
- **NFD 변환 전략**: 단순 `os.rename(NFD→NFC)` 은 macOS HFS+가 동일하게 취급해 Google Drive가 감지 못함
  - 파일: `copy2 → 삭제 → rename` (Drive가 삭제+생성으로 인식)
  - 폴더: 임시 이름 경유 2단계 rename
- **이벤트 중복 방지**: FSEvents가 동일 파일 이벤트를 연속 발생시키므로 `_DEDUP_WINDOW=0.2s` 적용
- **스레드 안전**: GUI 업데이트는 Queue → `after(100ms)` 폴링으로 메인 스레드에서만 처리
- **경로 정규화**: FSEventsObserver가 경로를 NFC로 반환할 수 있어 `os.scandir`로 실제 NFD 경로를 재탐색

## 작업 원칙
- 리팩토링 시 기능/동작 변경 금지
- 가독성 우선 (성능 최적화는 필요할 때만)
- UI 변경 후 반드시 빌드(`bash build.sh`) 및 앱 실행으로 확인
- 불필요한 추상화, 미래 대비 설계 금지
