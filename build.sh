#!/bin/bash
# build.sh — PyInstaller 빌드 (macOS: onedir, Windows: onefile)

set -e

APP_NAME="KoreanFilenameFixer"
DIST_DIR="dist"
BUILD_DIR="build"

echo "=== 의존성 설치 ==="
pip install -r requirements.txt

echo "=== PyInstaller 빌드 ==="
if [[ "$(uname)" == "Darwin" ]]; then
  # macOS: onedir — 압축 해제 없이 즉시 실행
  BUNDLE_OPT="--onedir"
  TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/${APP_NAME}.XXXXXX")"
  TMP_DIST="$TMP_ROOT/dist"
  TMP_BUILD="$TMP_ROOT/build"
  TMP_SPEC="$TMP_ROOT/spec"
  trap 'rm -rf "$TMP_ROOT"' EXIT

  pyinstaller \
    -y \
    --windowed \
    $BUNDLE_OPT \
    --name "$APP_NAME" \
    --clean \
    --distpath "$TMP_DIST" \
    --workpath "$TMP_BUILD" \
    --specpath "$TMP_SPEC" \
    main.py

  echo "=== macOS 번들 검증 ==="
  codesign --verify --deep --strict "$TMP_DIST/$APP_NAME.app"

  mkdir -p "$DIST_DIR" "$BUILD_DIR"
  rm -rf "$DIST_DIR/$APP_NAME" "$DIST_DIR/$APP_NAME.app" \
         "$DIST_DIR/$APP_NAME.app.zip" "$BUILD_DIR/$APP_NAME"

  # 현재 작업 폴더가 iCloud/File Provider 경로면 .app 디렉터리에
  # Finder 메타데이터가 붙어 strict codesign 검증이 깨질 수 있다.
  # 배포용은 zip으로 보존하고, 로컬 실행 편의를 위해 app/folder도 같이 복사한다.
  ditto "$TMP_DIST/$APP_NAME" "$DIST_DIR/$APP_NAME"
  ditto "$TMP_DIST/$APP_NAME.app" "$DIST_DIR/$APP_NAME.app"
  (
    cd "$TMP_DIST"
    COPYFILE_DISABLE=1 ditto -c -k --norsrc --keepParent \
      "$APP_NAME.app" "$OLDPWD/$DIST_DIR/$APP_NAME.app.zip"
  )
else
  # Windows: onefile — 단일 .exe 배포
  BUNDLE_OPT="--onefile"
  pyinstaller \
    -y \
    --windowed \
    $BUNDLE_OPT \
    --name "$APP_NAME" \
    --clean \
    main.py
fi

echo ""
echo "=== 빌드 완료 ==="
echo "실행파일: $DIST_DIR/$APP_NAME"
echo ""
if [[ "$(uname)" == "Darwin" ]]; then
  echo "앱 번들: $DIST_DIR/$APP_NAME.app"
  echo "배포용 zip: $DIST_DIR/$APP_NAME.app.zip"
  echo "※ strict codesign 검증 기준의 깨끗한 배포본은 zip 기준입니다."
  echo "※ 첫 실행 시 Gatekeeper 경고가 뜨면:"
  echo "   시스템 설정 → 개인 정보 보호 및 보안 → '확인 없이 열기' 클릭"
  echo "   또는 터미널에서: xattr -cr $DIST_DIR/$APP_NAME.app"
fi
