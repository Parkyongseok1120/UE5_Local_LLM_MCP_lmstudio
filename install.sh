#!/usr/bin/env sh
# Requires host Python 3.10+ on PATH (or PYTHON=/path/to/python3.12) before bootstrap.
# On a clean macOS install, system /usr/bin/python3 is often 3.9 and is not enough.
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

resolve_python_candidate() {
  candidate=$1
  case "$candidate" in
    /*|*/*)
      if [ -x "$candidate" ]; then
        printf '%s\n' "$candidate"
        return 0
      fi
      return 1
      ;;
  esac
  command -v "$candidate" 2>/dev/null
}

is_usable_python() {
  candidate=$1
  resolved=$(resolve_python_candidate "$candidate" || true)
  if [ -z "${resolved}" ]; then
    append_checked "$candidate" "not found"
    return 1
  fi
  version_line=$("$resolved" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null) || {
    append_checked "$candidate" "failed version probe"
    return 1
  }
  if "$resolved" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    append_checked "$candidate" "Python ${version_line}"
    return 0
  fi
  append_checked "$candidate" "Python ${version_line} (too old)"
  return 1
}

try_exec_python() {
  # $1 is the interpreter candidate; remaining args are install.py argv.
  # shift so the candidate is never forwarded to install.py.
  candidate=$1
  shift
  if is_usable_python "$candidate"; then
    resolved=$(resolve_python_candidate "$candidate")
    exec "$resolved" "$INSTALL_PY" "$@"
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

# PATH may still only expose Apple /usr/bin/python3 (3.9). Probe common
# user-managed installs (uv, evidence-first, Codex, LM Studio, Homebrew) without requiring brew in PATH.
HOME_DIR=${HOME:-}
if [ -n "$HOME_DIR" ]; then
  for minor in 14 13 12 11 10; do
    global_homebrew_root=""
    global_usr_local_root=""
    global_framework_root=""
    # Hosted POSIX tests need to prove PATH/HOME discovery without inheriting
    # a runner's preinstalled global Python. This private test seam does not
    # change normal installer discovery.
    if [ "${_INSTALL_SH_TEST_HERMETIC:-0}" != "1" ]; then
      global_homebrew_root="/opt/homebrew/opt/python@3.${minor}"
      global_usr_local_root="/usr/local/opt/python@3.${minor}"
      global_framework_root="/Library/Frameworks/Python.framework/Versions/3.${minor}"
    fi
    for root in \
      "$HOME_DIR/.local/share/uv/python" \
      "$HOME_DIR/.evidence-first/runtimes/python" \
      "$HOME_DIR/.cache/codex-runtimes/codex-primary-runtime/dependencies/python" \
      "$HOME_DIR/.lmstudio/extensions/backends/vendor/_amphibian" \
      "$global_homebrew_root" \
      "$global_usr_local_root" \
      "$global_framework_root"
    do
      [ -n "$root" ] || continue
      [ -d "$root" ] || continue
      for candidate in \
        "$root/bin/python3.${minor}" \
        "$root/bin/python3" \
        "$root"/cpython-3."${minor}"*/bin/python3."${minor}" \
        "$root"/cpython3."${minor}"*/bin/python3."${minor}" \
        "$root"/*/bin/python3."${minor}"
      do
        case "$candidate" in
          *\*) continue ;;
          *-config) continue ;;
        esac
        [ -x "$candidate" ] || continue
        try_exec_python "$candidate" "$@" || true
      done
    done
  done
fi

echo "Python 3.10+ was not found." >&2
echo "" >&2
echo "Checked:${CHECKED_REPORT}" >&2
echo "" >&2
echo "PATH:" >&2
echo "${PATH:-}" >&2
echo "" >&2
echo "If Python 3.10+ is installed outside PATH:" >&2
echo "  PYTHON=/path/to/python3.12 \"$0\"" >&2
echo "" >&2
echo "macOS:" >&2
echo "  install Python 3.12+ from https://www.python.org/downloads/" >&2
echo "  or (when Homebrew exists): brew install python@3.12" >&2
echo "Ubuntu: sudo apt-get update && sudo apt-get install -y python3 ca-certificates" >&2
exit 127
