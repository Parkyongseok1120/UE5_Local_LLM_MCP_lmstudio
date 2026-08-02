#!/usr/bin/env sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INSTALL_PY="$SCRIPT_DIR/install.py"

# Collect "candidate: detail" lines for the failure report (POSIX sh; no arrays).
CHECKED_REPORT=""

append_checked() {
  candidate=$1
  detail=$2
  CHECKED_REPORT="${CHECKED_REPORT}
- ${candidate}: ${detail}"
}

is_usable_python() {
  candidate=$1
  if ! command -v "$candidate" >/dev/null 2>&1; then
    append_checked "$candidate" "not found"
    return 1
  fi
  version_line=$("$candidate" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null) || {
    append_checked "$candidate" "failed version probe"
    return 1
  }
  if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    append_checked "$candidate" "Python ${version_line}"
    return 0
  fi
  append_checked "$candidate" "Python ${version_line} (too old)"
  return 1
}

try_exec_python() {
  candidate=$1
  if is_usable_python "$candidate"; then
    exec "$candidate" "$INSTALL_PY" "$@"
  fi
  return 1
}

# Prefer an explicit PYTHON override when it is actually 3.10+.
if [ -n "${PYTHON:-}" ]; then
  try_exec_python "$PYTHON" "$@" || true
fi

# Prefer versioned interpreters over a stale system python3 (common on macOS).
for candidate in \
  python3.14 \
  python3.13 \
  python3.12 \
  python3.11 \
  python3.10 \
  python3 \
  python
do
  try_exec_python "$candidate" "$@" || true
done

echo "Python 3.10+ was not found." >&2
echo "" >&2
echo "Checked:${CHECKED_REPORT}" >&2
echo "" >&2
echo "PATH:" >&2
echo "${PATH:-}" >&2
echo "" >&2
echo "macOS:" >&2
echo "  brew install python@3.12" >&2
echo "  or install Python from python.org" >&2
echo "Ubuntu: sudo apt-get update && sudo apt-get install -y python3 ca-certificates" >&2
exit 127
