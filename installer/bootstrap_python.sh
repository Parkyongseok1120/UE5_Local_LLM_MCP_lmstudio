#!/usr/bin/env sh
# Pre-Python bridge for install.sh. Keep the pinned constants synchronized with
# runtime-manifest.json; tests fail if the manifest and this POSIX mirror drift.
set -eu

PYTHON_VERSION=3.12.13
UV_VERSION=0.12.1
MAX_UV_ARCHIVE_BYTES=134217728

if [ "$#" -lt 1 ]; then
  echo "Initial Python bootstrap failed: install.py path is required." >&2
  exit 127
fi
INSTALL_PY=$1
shift
if [ ! -f "$INSTALL_PY" ]; then
  echo "Initial Python bootstrap failed: integrated installer is missing: $INSTALL_PY" >&2
  exit 127
fi

if [ -n "${HOME:-}" ]; then
  state_home=$HOME/.evidence-first
else
  state_home=""
fi
expect_state_home=0
for argument in "$@"; do
  if [ "$expect_state_home" = "1" ]; then
    state_home=$argument
    expect_state_home=0
    continue
  fi
  case "$argument" in
    --skip-runtime-bootstrap)
      echo "Initial Python bootstrap failed: Python is unavailable and --skip-runtime-bootstrap forbids an automatic download." >&2
      echo "Install Python 3.10+ or remove that flag." >&2
      exit 127
      ;;
    --state-home)
      expect_state_home=1
      ;;
    --state-home=*)
      state_home=${argument#--state-home=}
      ;;
  esac
done
if [ "$expect_state_home" = "1" ] || [ -z "$state_home" ]; then
  echo "Initial Python bootstrap failed: --state-home requires a non-empty path." >&2
  exit 127
fi
case "$state_home" in
  "~")
    if [ -z "${HOME:-}" ]; then
      echo "Initial Python bootstrap failed: HOME is unset; pass an explicit --state-home path." >&2
      exit 127
    fi
    state_home=$HOME
    ;;
  "~/"*)
    if [ -z "${HOME:-}" ]; then
      echo "Initial Python bootstrap failed: HOME is unset; pass an explicit --state-home path." >&2
      exit 127
    fi
    state_home=$HOME/${state_home#\~/}
    ;;
esac
case "$state_home" in
  /*) ;;
  *) state_home=$PWD/$state_home ;;
esac
mkdir -p "$state_home"
state_home=$(CDPATH= cd -- "$state_home" && pwd)
if [ "$state_home" = "/" ]; then
  echo "Initial Python bootstrap failed: the installer state-home cannot be the filesystem root." >&2
  exit 127
fi

host_name=$(uname -s 2>/dev/null || true)
machine_name=$(uname -m 2>/dev/null || true)
case "$machine_name" in
  arm64|aarch64) architecture=arm64 ;;
  x86_64|amd64|x64) architecture=x64 ;;
  *)
    echo "Initial Python bootstrap failed: unsupported CPU architecture: $machine_name" >&2
    exit 127
    ;;
esac

case "$host_name-$architecture" in
  Darwin-arm64)
    platform=darwin
    uv_asset=uv-aarch64-apple-darwin.tar.gz
    uv_sha256=77d2906988e8074fd43f2f329ec452ebbf9b0c257ba1c66451c71de70a6baf42
    ;;
  Darwin-x64)
    platform=darwin
    uv_asset=uv-x86_64-apple-darwin.tar.gz
    uv_sha256=69d9f9a00337f25a50dcb13882052da08b8469bac11091c98c5694c3c6721467
    ;;
  Linux-arm64)
    platform=linux
    uv_asset=uv-aarch64-unknown-linux-gnu.tar.gz
    uv_sha256=769d373e146692c639b5fbaae33b331c297a32e03d30448772051902df52bbf4
    ;;
  Linux-x64)
    platform=linux
    uv_asset=uv-x86_64-unknown-linux-gnu.tar.gz
    uv_sha256=90b2f223fb69d19db49e117da601f64978593417988530aa733d456141b4bcbb
    ;;
  *)
    echo "Initial Python bootstrap failed: unsupported host: $host_name-$architecture" >&2
    exit 127
    ;;
esac

if [ "$platform" = "linux" ] && command -v ldd >/dev/null 2>&1; then
  if ldd --version 2>&1 | grep -i musl >/dev/null 2>&1; then
    echo "Initial Python bootstrap failed: Alpine/musl is unsupported; use Ubuntu 22.04/24.04 with glibc." >&2
    exit 127
  fi
fi

runtime_root=$state_home/runtimes
uv_path=$runtime_root/uv/uv
mkdir -p "$runtime_root"

uv_is_usable() {
  [ -x "$uv_path" ] || return 1
  uv_version_output=$("$uv_path" --version 2>/dev/null || true)
  case "$uv_version_output" in
    "uv $UV_VERSION"|"uv $UV_VERSION "*) return 0 ;;
    *) return 1 ;;
  esac
}

download_archive() {
  download_url=$1
  download_path=$2
  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --retry 3 --connect-timeout 30 --max-time 300 --output "$download_path" "$download_url"
    return
  fi
  if command -v wget >/dev/null 2>&1; then
    wget --https-only --timeout=300 --output-document="$download_path" "$download_url"
    return
  fi
  echo "Initial Python bootstrap failed: curl or wget is required for the first runtime download." >&2
  exit 127
}

archive_sha256() {
  archive_path=$1
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$archive_path" | awk '{print $1}'
    return
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$archive_path" | awk '{print $1}'
    return
  fi
  if command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$archive_path" | sed 's/^.*= //'
    return
  fi
  echo "Initial Python bootstrap failed: no SHA-256 utility is available." >&2
  exit 127
}

seed_temporary_root=""
cleanup_seed() {
  case "$seed_temporary_root" in
    "$runtime_root"/.python-seed.*)
      [ ! -e "$seed_temporary_root" ] || rm -rf "$seed_temporary_root"
      ;;
  esac
}
trap cleanup_seed 0 1 2 3 15

echo "Initial Python bootstrap: $platform-$architecture" >&2
echo "  State home: $state_home" >&2
if ! uv_is_usable; then
  echo "  Installing pinned uv $UV_VERSION for the initial Python bootstrap..." >&2
  if ! command -v mktemp >/dev/null 2>&1 || ! command -v tar >/dev/null 2>&1; then
    echo "Initial Python bootstrap failed: mktemp and tar are required." >&2
    exit 127
  fi
  seed_temporary_root=$(mktemp -d "$runtime_root/.python-seed.XXXXXX")
  uv_archive=$seed_temporary_root/$uv_asset
  uv_extract_root=$seed_temporary_root/extracted
  mkdir -p "$uv_extract_root"
  uv_url=https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$uv_asset
  download_archive "$uv_url" "$uv_archive"
  archive_size=$(wc -c < "$uv_archive" | tr -d '[:space:]')
  case "$archive_size" in
    ''|*[!0-9]*)
      echo "Initial Python bootstrap failed: could not measure the uv archive." >&2
      exit 127
      ;;
  esac
  if [ "$archive_size" -le 0 ] || [ "$archive_size" -gt "$MAX_UV_ARCHIVE_BYTES" ]; then
    echo "Initial Python bootstrap failed: the uv archive size is outside the allowed range." >&2
    exit 127
  fi
  actual_sha256=$(archive_sha256 "$uv_archive" | tr 'A-F' 'a-f')
  if [ "$actual_sha256" != "$uv_sha256" ]; then
    echo "Initial Python bootstrap failed: SHA-256 mismatch for $uv_asset. The archive was not extracted." >&2
    exit 127
  fi
  tar -xzf "$uv_archive" -C "$uv_extract_root"
  extracted_uv=$(find "$uv_extract_root" -type f -name uv 2>/dev/null | sed -n '1p')
  if [ -z "$extracted_uv" ]; then
    echo "Initial Python bootstrap failed: uv is missing from $uv_asset." >&2
    exit 127
  fi
  chmod 700 "$extracted_uv"
  extracted_uv_version=$("$extracted_uv" --version 2>/dev/null || true)
  case "$extracted_uv_version" in
    "uv $UV_VERSION"|"uv $UV_VERSION "*) ;;
    *)
      echo "Initial Python bootstrap failed: the extracted uv executable failed its version probe." >&2
      exit 127
      ;;
  esac
  mkdir -p "$runtime_root/uv"
  pending_uv=$runtime_root/uv/uv.new.$$
  cp "$extracted_uv" "$pending_uv"
  chmod 700 "$pending_uv"
  mv -f "$pending_uv" "$uv_path"
  if [ "$platform" = "darwin" ] && command -v xattr >/dev/null 2>&1; then
    xattr -dr com.apple.quarantine "$uv_path" >/dev/null 2>&1 || true
  fi
fi
if ! uv_is_usable; then
  echo "Initial Python bootstrap failed: cached uv failed its post-install probe: $uv_path" >&2
  exit 127
fi

cleanup_seed
seed_temporary_root=""
python_install_root=$runtime_root/python
python_bin_root=$runtime_root/bin
mkdir -p "$python_install_root" "$python_bin_root"
export UV_PYTHON_INSTALL_DIR=$python_install_root
export UV_PYTHON_BIN_DIR=$python_bin_root
export XDG_BIN_HOME=$python_bin_root

echo "  Installing managed Python $PYTHON_VERSION..." >&2
"$uv_path" python install "$PYTHON_VERSION" >&2
python_path=$("$uv_path" python find "$PYTHON_VERSION")
if [ ! -x "$python_path" ]; then
  echo "Initial Python bootstrap failed: uv did not return a usable managed Python path." >&2
  exit 127
fi
python_version=$("$python_path" -c 'import sys; print(sys.version.split()[0])' 2>/dev/null || true)
python_architecture=$("$python_path" -c "import platform; m=platform.machine().lower(); print('arm64' if m in {'arm64','aarch64'} else 'x64' if m in {'x86_64','amd64','x64'} else m)" 2>/dev/null || true)
if [ "$python_version" != "$PYTHON_VERSION" ]; then
  echo "Initial Python bootstrap failed: managed Python version probe returned $python_version." >&2
  exit 127
fi
if [ "$python_architecture" != "$architecture" ]; then
  echo "Initial Python bootstrap failed: managed Python architecture probe returned $python_architecture." >&2
  exit 127
fi

echo "  Launching the integrated installer with $python_path" >&2
exec "$python_path" "$INSTALL_PY" "$@"
