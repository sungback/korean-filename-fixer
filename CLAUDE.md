# Korean Filename Fixer — 프로젝트 규칙

## 개요
macOS에서 한글 파일명을 NFD → NFC로 변환해 Windows/Linux와의 호환성을 확보하는 데스크톱 앱.

## 기술 스택
- **언어**: Python 3.12
- **GUI**: tkinter (scrolledtext, ttk)
- **파일 감시**: watchdog (`Observer`, macOS에서는 FSEvents 기반 / PollingObserver 폴백)
- **tray 아이콘**: AppKit (pyobjc), macOS 전용
- **빌드**: PyInstaller → `bash build.sh`
- **플랫폼**: macOS 중심 (GitHub Actions에서 Windows 보조 빌드도 생성)

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
- **미리보기(드라이런)**: 실제 변환 전에 예정 이름과 충돌 여부를 계산해 로그로 확인 가능
- **시작 시 자동 스캔**: 저장된 감시 폴더가 있으면 앱 시작 직후 누락된 NFD 파일을 한 번 정리한 뒤 감시 시작
- **로그인 시 자동 시작**: macOS는 LaunchAgent plist, Windows는 Run 레지스트리로 로그인 후 앱 자동 실행 지원 (`.app` / `.exe` 배포 실행 기준)
- **제외 패턴**: `.git`, `node_modules`, `venv` 등 디렉터리 패턴은 일괄 변환과 실시간 감시 모두에서 공통 적용
- **이벤트 중복 방지**: FSEvents가 동일 파일 이벤트를 연속 발생시키므로 `_DEDUP_WINDOW=0.2s` 적용
- **스레드 안전**: GUI 업데이트는 Queue → `after(100ms)` 폴링으로 메인 스레드에서만 처리
- **경로 정규화**: FSEventsObserver가 경로를 NFC로 반환할 수 있어 `os.scandir`로 실제 NFD 경로를 재탐색

## 배포 (GitHub Actions)
`v*` 태그 푸시 시 macOS/Windows 자동 빌드 및 GitHub Release 생성 (`.github/workflows/build.yml`)

```bash
git tag vX.X.X && git push origin main && git push origin vX.X.X
```

- 태그 규칙: `v{major}.{minor}.{patch}` — 최신 `v1.10.7`
- 기능 추가: minor 버전 업, 버그 수정/리팩토링: patch 버전 업
- **소스 기능 변경이 없을 때(문서, 설정 등)는 태그 없이 push만**

## 대화 관리
- 재시작: `exit` → `claude .` (`/clear`는 CLAUDE.md·메모리를 재로드하지 않으므로 비권장)
- 컨텍스트 85% 이상이거나 작업 단위가 끝나면 새 대화 시작
- 대화 종료: "마무리해줘" → 새 대화 시작: "메모리 읽고 현재 상태 파악해줘"

## 작업 원칙
- 리팩토링 시 기능/동작 변경 금지
- 가독성 우선 (성능 최적화는 필요할 때만)
- UI 변경 후 반드시 빌드(`bash build.sh`) 및 앱 실행으로 확인
- 불필요한 추상화, 미래 대비 설계 금지
- **요청이 애매하면 추측으로 진행하지 말고 먼저 질문할 것**
- **자율 모드**: 파일 읽기/수정/빌드/커밋은 확인 없이 바로 진행. git push·태그 push·삭제 등 되돌리기 어려운 작업은 확인 후 진행
