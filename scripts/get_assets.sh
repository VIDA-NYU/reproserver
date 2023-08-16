#!/bin/sh

download_asset() {
  URL="$1"
  DEST="$2"
  CHECKSUM="$3"

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

download_asset "https://cdn.jsdelivr.net/npm/@webrecorder/archivewebpage@0.9.8/dist/embed/ui.js" "reproserver/web/static/js/archivewebpage-ui.js" "30e28e10b1afcf7587405a03aa9bcff1abc827aa5351d51ffdb555d5440e0945"
download_asset "https://cdn.jsdelivr.net/npm/@webrecorder/archivewebpage@0.9.8/dist/embed/replay/sw.js" "reproserver/web/static/replay/sw.js" "32e0fe19718935f93a2599ed9a2f5daed52cb6237e388841f4f56e2453c7f728"
