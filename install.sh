#!/usr/bin/env sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if command -v python3.12 >/dev/null 2>&1; then
  exec python3.12 "$SCRIPT_DIR/install.py" "$@"
fi
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
  exec python3 "$SCRIPT_DIR/install.py" "$@"
fi
echo "Python 3.10 or newer is required to start the installer." >&2
echo "Ubuntu: sudo apt-get update && sudo apt-get install -y python3 ca-certificates" >&2
echo "macOS: install current Python from python.org or Homebrew, then retry." >&2
exit 127
