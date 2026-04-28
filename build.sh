#!/bin/bash
# build.sh — PyInstaller 빌드 (macOS: onedir, Windows: onedir)

set -e

APP_NAME="KoreanFilenameFixer"
DIST_DIR="dist"
BUILD_DIR="build"
PYTHON_BIN="${PYTHON:-python}"
MACOS_BUNDLE_ID="${MACOS_BUNDLE_ID:-KoreanFilenameFixer}"
MACOS_SIGN_IDENTITY="${MACOS_SIGN_IDENTITY:-}"
MACOS_ENTITLEMENTS_FILE="${MACOS_ENTITLEMENTS_FILE:-}"
MACOS_NOTARIZE="${MACOS_NOTARIZE:-0}"
MACOS_NOTARY_PROFILE="${MACOS_NOTARY_PROFILE:-}"
MACOS_NOTARY_KEYCHAIN="${MACOS_NOTARY_KEYCHAIN:-}"

if [[ "$(uname)" == "Darwin" && "$MACOS_NOTARIZE" == "1" ]]; then
  if [[ -z "$MACOS_SIGN_IDENTITY" ]]; then
    echo "오류: MACOS_NOTARIZE=1 requires MACOS_SIGN_IDENTITY." >&2
    exit 1
  fi
  if [[ -z "$MACOS_NOTARY_PROFILE" ]]; then
    echo "오류: MACOS_NOTARIZE=1 requires MACOS_NOTARY_PROFILE." >&2
    exit 1
  fi
  xcrun --find notarytool >/dev/null
  xcrun --find stapler >/dev/null
fi

echo "=== 의존성 설치 ==="
"$PYTHON_BIN" -m pip install -r requirements.txt

echo "=== Tkinter 확인 ==="
"$PYTHON_BIN" -c "import tkinter"

echo "=== 테스트 실행 ==="
"$PYTHON_BIN" -m unittest discover -s tests -v

echo "=== PyInstaller 빌드 ==="
if [[ "$(uname)" == "Darwin" ]]; then
  # macOS: onedir — 압축 해제 없이 즉시 실행
  BUNDLE_OPT="--onedir"
  TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/${APP_NAME}.XXXXXX")"
  TMP_DIST="$TMP_ROOT/dist"
  TMP_BUILD="$TMP_ROOT/build"
  TMP_SPEC="$TMP_ROOT/spec"
  trap 'rm -rf "$TMP_ROOT"' EXIT

  PYINSTALLER_ARGS=(
    -y \
    --windowed \
    "$BUNDLE_OPT" \
    --name "$APP_NAME" \
    --clean \
    --osx-bundle-identifier "$MACOS_BUNDLE_ID" \
    --distpath "$TMP_DIST" \
    --workpath "$TMP_BUILD" \
    --specpath "$TMP_SPEC"
  )

  if [[ -n "$MACOS_SIGN_IDENTITY" ]]; then
    echo "=== Developer ID 서명 활성화 ==="
    PYINSTALLER_ARGS+=(--codesign-identity "$MACOS_SIGN_IDENTITY")
  fi

  if [[ -n "$MACOS_ENTITLEMENTS_FILE" ]]; then
    PYINSTALLER_ARGS+=(--osx-entitlements-file "$MACOS_ENTITLEMENTS_FILE")
  fi

  "$PYTHON_BIN" -m PyInstaller "${PYINSTALLER_ARGS[@]}" main.py

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

  if [[ "$MACOS_NOTARIZE" == "1" ]]; then
    NOTARY_ARGS=(--keychain-profile "$MACOS_NOTARY_PROFILE")
    if [[ -n "$MACOS_NOTARY_KEYCHAIN" ]]; then
      NOTARY_ARGS+=(--keychain "$MACOS_NOTARY_KEYCHAIN")
    fi

    echo "=== Apple notarization 제출 ==="
    xcrun notarytool submit "$DIST_DIR/$APP_NAME.app.zip" \
      "${NOTARY_ARGS[@]}" \
      --wait

    echo "=== Notarization ticket staple ==="
    xcrun stapler staple "$DIST_DIR/$APP_NAME.app"
    xcrun stapler validate "$DIST_DIR/$APP_NAME.app"

    (
      cd "$DIST_DIR"
      rm -f "$APP_NAME.app.zip"
      COPYFILE_DISABLE=1 ditto -c -k --norsrc --keepParent \
        "$APP_NAME.app" "$APP_NAME.app.zip"
    )
  fi
else
  # Windows: onedir — 디렉터리 배포 (바이러스 오진 방지)
  BUNDLE_OPT="--onedir"
  "$PYTHON_BIN" -m PyInstaller \
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
