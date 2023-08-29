#!/bin/sh

download_asset() {
  URL="$1"
  DEST="$2"
  CHECKSUM="$3"

  if [ -n "$(dirname "$DEST")" ]; then
    mkdir -p "$(dirname "$DEST")" || return 1
  fi

  # If the file exists and has the correct checksum
  if [ -e "$2" ] && printf '%s  %s\n' "$CHECKSUM" "$DEST" | sha256sum -c >/dev/null 2>&1; then
    return 0
  fi

  # Download the file
  printf 'Downloading %s from %s\n' "$DEST" "$URL" >&2
  if ! curl -fsSLo "$DEST" "$URL"; then
    return 1
  fi

  # Check the checksum
  if ! printf '%s  %s\n' "$CHECKSUM" "$DEST" | sha256sum -c >/dev/null 2>&1; then
    printf 'Invalid checksum for %s\n' "$DEST"
    return 1
  fi

  return 0
}

set -eu

cd "$(dirname "$0")/.."

download_asset "https://cdn.jsdelivr.net/npm/@webrecorder/archivewebpage@0.11.1/dist/embed/ui.js" "reproserver/web/static/js/archivewebpage-ui.js" "f4749ddf3cf2cb35493f27ccd25fb9444bd3fdcf1fe87376c8f23bbf8b794e31"
download_asset "https://cdn.jsdelivr.net/npm/@webrecorder/archivewebpage@0.11.1/dist/embed/replay/sw.js" "reproserver/web/static/replay/sw.js" "83792628953de27586b61eae4e44121a23981a6539404730f3158e2cc48b8db5"
download_asset "https://cdn.jsdelivr.net/npm/replaywebpage@1.8.6/ui.js" "reproserver/web/static/js/replaywebpage-ui.js" "5644cd2a76b4c9ef0dd73067ba5c509e8246e328f56ef9ff8d8616caeb2f407a"
download_asset "https://cdn.jsdelivr.net/npm/replaywebpage@1.8.6/sw.js" "reproserver/web/static/replay/rwp-sw.js" "89af3d81c6e3a6c5d650a9fdcb412c416727dc2d954332e24d462cf0269b8952"
