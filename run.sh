#!/usr/bin/env bash
#
# Launch the mbox viewer: build the Docker image and start the container,
# mounting your Google Takeout .mbox file (read-only) from the host.
#
# Usage:
#   ./run.sh /absolute/path/to/your.mbox      # point at the .mbox file
#   ./run.sh /absolute/path/to/folder         # folder containing exactly one .mbox
#   MBOX_FILE=/path/to/your.mbox ./run.sh     # or via environment variable
#
# Optional:
#   PORT=9500 ./run.sh /path/to/your.mbox     # change the host port (default 9000)
#
set -euo pipefail
cd "$(dirname "$0")"

# 1. Resolve the mbox path from the first argument or the MBOX_FILE env var.
MBOX_INPUT="${1:-${MBOX_FILE:-}}"
if [ -z "$MBOX_INPUT" ]; then
  cat >&2 <<'USAGE'
Error: no mbox path given.

Usage:
  ./run.sh /absolute/path/to/your.mbox      point at the .mbox file
  ./run.sh /absolute/path/to/folder         folder containing exactly one .mbox
  MBOX_FILE=/path/to/your.mbox ./run.sh     or via environment variable

Optional:
  PORT=9500 ./run.sh /path/to/your.mbox     change the host port (default 9000)
USAGE
  exit 1
fi

abspath() { cd "$(dirname "$1")" >/dev/null 2>&1 && printf '%s/%s\n' "$(pwd -P)" "$(basename "$1")"; }

# 2. Accept either a .mbox file directly, or a folder containing exactly one.
if [ -d "$MBOX_INPUT" ]; then
  shopt -s nullglob
  matches=("$MBOX_INPUT"/*.mbox)
  shopt -u nullglob
  if [ "${#matches[@]}" -eq 0 ]; then
    echo "Error: no .mbox file found in folder: $MBOX_INPUT" >&2
    exit 1
  elif [ "${#matches[@]}" -gt 1 ]; then
    echo "Error: multiple .mbox files in $MBOX_INPUT — pass the specific file instead:" >&2
    printf '  %s\n' "${matches[@]}" >&2
    exit 1
  fi
  RESOLVED="$(abspath "${matches[0]}")"
elif [ -f "$MBOX_INPUT" ]; then
  RESOLVED="$(abspath "$MBOX_INPUT")"
else
  echo "Error: path not found: $MBOX_INPUT" >&2
  exit 1
fi

# 3. Export for docker-compose and launch (build + run in the foreground).
export MBOX_FILE="$RESOLVED"
export PORT="${PORT:-9000}"
# Durable image archive: a host folder next to the mbox by default (override with ARCHIVE_HOST_DIR).
export ARCHIVE_HOST_DIR="${ARCHIVE_HOST_DIR:-$(dirname "$RESOLVED")/mbox-viewer-archive}"
mkdir -p "$ARCHIVE_HOST_DIR"
# Display the real host filename in the UI (the container only sees /data/mail.mbox).
export MBOX_NAME="$(basename "$RESOLVED")"

echo "mbox file : $MBOX_FILE"
echo "viewer    : http://localhost:${PORT}"
echo "archive   : $ARCHIVE_HOST_DIR"
echo "Building image and starting container..."
echo "(First run indexes the mbox — this can take a while for large files; watch the logs.)"
echo

exec docker compose up --build
