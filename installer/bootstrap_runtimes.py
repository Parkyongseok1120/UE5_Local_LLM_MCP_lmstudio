"""Download and cache Python 3.12 and Node.js/npm for the integrated installer."""

from __future__ import annotations

import hashlib
import os
import platform
import posixpath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

REQUIRED_PYTHON = (3, 12)
PINNED_PYTHON_VERSION = "3.12.13"
REQUIRED_NODE_MAJOR = 20
PINNED_NODE_VERSION = "20.20.2"
PINNED_UV_VERSION = "0.12.1"
PINNED_PWSH_VERSION = "7.5.4"
TOOLCHAIN_DIRNAME = "evidence-first-runtimes"
BOOTSTRAP_ENV = "EVIDENCE_FIRST_RUNTIME_BOOTSTRAPPED"
MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024

# Pinned upstream SHA-256 values from uv's per-asset .sha256 files, Node's
# SHASUMS256.txt, and PowerShell's hashes.sha256. A download is never extracted
# until its digest matches this table. Coverage is x64/arm64 on Windows, macOS,
# and Ubuntu/glibc.
ARCHIVE_SHA256 = {
    "uv-aarch64-apple-darwin.tar.gz": "77d2906988e8074fd43f2f329ec452ebbf9b0c257ba1c66451c71de70a6baf42",
    "uv-x86_64-apple-darwin.tar.gz": "69d9f9a00337f25a50dcb13882052da08b8469bac11091c98c5694c3c6721467",
    "uv-aarch64-pc-windows-msvc.zip": "9bc7c18e616230fa2dc6fb24bc3afde18a95c2b5c9433de747e9502c66041568",
    "uv-x86_64-pc-windows-msvc.zip": "8fcb0cb46e1229065e344758980924e569bef5882ef45f46fada8fb24e06b74a",
    "uv-aarch64-unknown-linux-gnu.tar.gz": "769d373e146692c639b5fbaae33b331c297a32e03d30448772051902df52bbf4",
    "uv-x86_64-unknown-linux-gnu.tar.gz": "90b2f223fb69d19db49e117da601f64978593417988530aa733d456141b4bcbb",
    "node-v20.20.2-darwin-arm64.tar.gz": "466e05f3477c20dfb723054dfebffe55bc74660ee77f612166fca121dacb65b6",
    "node-v20.20.2-darwin-x64.tar.gz": "8be6f5e4bb128c82774f8a0b8d7a1cc1365a7977d9657cece0ca647b3fe04e61",
    "node-v20.20.2-linux-arm64.tar.gz": "47ef73d543ecf6eb19435f6c03a0ac4809b3bf0dd6b26c7c571efc2a6572a74d",
    "node-v20.20.2-linux-x64.tar.gz": "19e56f0825510207dd904f087fe52faa0a4eb6b2aab5f0ea7a33830d04888b8b",
    "node-v20.20.2-win-arm64.zip": "d5c5b1d56f7f9469830eb1f57efeec0a6a9078c0a9e88cd5b4b4b48f46c22069",
    "node-v20.20.2-win-x64.zip": "dc3700fdd57a63eedb8fd7e3c7baaa32e6a740a1b904167ff4204bc68ed8bf77",
    "powershell-7.5.4-linux-arm64.tar.gz": "4b32d4cb86a43dfb83d5602d0294295bf22fafbf9e0785d1aaef81938cda92f8",
    "powershell-7.5.4-linux-x64.tar.gz": "1fd7983fe56ca9e6233f126925edb24bf6b6b33e356b69996d925c4db94e2fef",
    "powershell-7.5.4-osx-arm64.tar.gz": "3aaadd7ca62f1e4dbe59145b6af24e926d61f8da8a4782bc535e500c184135f0",
    "powershell-7.5.4-osx-x64.tar.gz": "cd16a04c1b99cdacbdc0337b0fd0da50dbf1a8b4e8437bcb4ca9118ef729211a",
    "PowerShell-7.5.4-win-arm64.zip": "0c0b2bf04e853917508280531cd49bba8b3049837e3c805ebc042e2741ca52b3",
    "PowerShell-7.5.4-win-x64.zip": "b40d192ae95ba6ccc4cc362ff4e1b18ca6fb5055bebbcd3920684e12701fa8f6",
}


def toolchain_root(state_home: Path | None = None) -> Path:
    if state_home is not None:
        return Path(state_home).expanduser().resolve() / "runtimes"
    return (Path.home() / ".cache" / TOOLCHAIN_DIRNAME).resolve()


def _machine() -> str:
    return platform.machine().lower()


def host_os() -> str:
    system = platform.system()
    if system == "Darwin":
        return "darwin"
    if system == "Windows":
        return "windows"
    if system == "Linux":
        return "linux"
    raise RuntimeError(f"unsupported host for runtime bootstrap: {system}")


def cpu_arch() -> str:
    """Return normalized host CPU arch: 'arm64' or 'x64'.

    Prefer real hardware arch on Apple Silicon even when the current Python
    process is running under Rosetta (platform.machine() == x86_64).
    """
    system = host_os()
    machine = _machine()

    if system == "darwin":
        try:
            completed = subprocess.run(
                ["sysctl", "-n", "hw.optional.arm64"],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode == 0 and completed.stdout.strip() == "1":
                return "arm64"
        except OSError:
            pass

    if system == "windows":
        # WoW64 x86 process on ARM64 Windows still needs the arm64 toolchain.
        for key in ("PROCESSOR_ARCHITEW6432", "PROCESSOR_ARCHITECTURE"):
            value = os.environ.get(key, "").strip().lower()
            if value in {"arm64", "aarch64"}:
                return "arm64"
            if value in {"amd64", "x86_64", "x64"}:
                return "x64"

    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86_64", "amd64", "x64"}:
        return "x64"
    raise RuntimeError(f"unsupported CPU architecture for runtime bootstrap: {machine}")


def validate_host_runtime() -> dict[str, str]:
    """Reject known-incompatible hosts before downloading platform runtimes."""
    system = host_os()
    details = {"os": system, "arch": cpu_arch()}
    if system != "linux":
        return details
    libc_name, libc_version = platform.libc_ver()
    normalized_libc = str(libc_name or "").strip().lower()
    if not normalized_libc:
        try:
            completed = subprocess.run(
                ["ldd", "--version"],
                capture_output=True,
                text=True,
                check=False,
            )
            ldd_text = f"{completed.stdout}\n{completed.stderr}".lower()
            if "musl" in ldd_text:
                normalized_libc = "musl"
                libc_name = "musl"
        except OSError:
            pass
    if normalized_libc and normalized_libc not in {"glibc", "gnu libc"}:
        raise RuntimeError(
            f"unsupported Linux C library: {libc_name} {libc_version}. "
            "The Linux installer baseline is Ubuntu 22.04/24.04 with glibc; musl/Alpine needs a separate runtime build."
        )
    details["libc"] = f"{libc_name} {libc_version}".strip() or "unknown"
    os_release = Path("/etc/os-release")
    try:
        values = {}
        for line in os_release.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
        details["distribution"] = values.get("PRETTY_NAME") or values.get("ID") or "unknown"
        details["distributionId"] = values.get("ID", "")
    except OSError:
        details["distribution"] = "unknown"
        details["distributionId"] = ""
    if details["distributionId"] == "alpine":
        raise RuntimeError(
            "unsupported Linux distribution: Alpine/musl. "
            "The Linux installer baseline is Ubuntu 22.04/24.04 with glibc."
        )
    return details


def uv_asset_name() -> str:
    system = host_os()
    arch = cpu_arch()
    if system == "darwin":
        uv_arch = "aarch64" if arch == "arm64" else "x86_64"
        return f"uv-{uv_arch}-apple-darwin.tar.gz"
    if system == "windows":
        uv_arch = "aarch64" if arch == "arm64" else "x86_64"
        return f"uv-{uv_arch}-pc-windows-msvc.zip"
    uv_arch = "aarch64" if arch == "arm64" else "x86_64"
    return f"uv-{uv_arch}-unknown-linux-gnu.tar.gz"


def node_asset_name(version: str = PINNED_NODE_VERSION) -> str:
    system = host_os()
    arch = cpu_arch()
    if system == "darwin":
        return f"node-v{version}-darwin-{arch}.tar.gz"
    if system == "windows":
        return f"node-v{version}-win-{arch}.zip"
    return f"node-v{version}-linux-{arch if arch == 'arm64' else 'x64'}.tar.gz"


def pwsh_asset_name(version: str = PINNED_PWSH_VERSION) -> str:
    system = host_os()
    arch = cpu_arch()
    if system == "darwin":
        return f"powershell-{version}-osx-{arch}.tar.gz"
    if system == "windows":
        return f"PowerShell-{version}-win-{arch}.zip"
    return f"powershell-{version}-linux-{arch if arch == 'arm64' else 'x64'}.tar.gz"


def _absolute_path(path: Path) -> Path:
    """Absolute path without following the final symlink (keeps npm shims usable)."""
    return Path(os.path.abspath(str(path)))



Downloader = Callable[[str, Path], None]


def _default_download(url: str, destination: Path) -> None:
    print(f"  download: {url}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with urllib_request.urlopen(url, timeout=180) as response, temporary.open("wb") as handle:
            content_length = response.headers.get("Content-Length")
            try:
                declared_size = int(content_length) if content_length else 0
            except ValueError:
                declared_size = 0
            if declared_size > MAX_DOWNLOAD_BYTES:
                raise RuntimeError(
                    f"download exceeds the {MAX_DOWNLOAD_BYTES}-byte safety limit: {url}"
                )
            downloaded = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError(
                        f"download exceeds the {MAX_DOWNLOAD_BYTES}-byte safety limit: {url}"
                    )
                handle.write(chunk)
        temporary.replace(destination)
    except Exception as exc:
        if temporary.exists():
            temporary.unlink()
        if isinstance(exc, urllib_error.URLError):
            raise RuntimeError(f"failed to download {url}: {exc}") from exc
        raise


def _verify_archive(archive: Path) -> None:
    expected = ARCHIVE_SHA256.get(archive.name)
    if not expected:
        raise RuntimeError(f"no pinned SHA-256 is registered for runtime archive: {archive.name}")
    digest = hashlib.sha256()
    with archive.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual.lower() != expected.lower():
        raise RuntimeError(
            f"SHA-256 mismatch for {archive.name}: expected {expected}, got {actual}. "
            "The download was not extracted."
        )


def _safe_archive_parts(name: str) -> tuple[str, ...]:
    normalized = str(name or "").replace("\\", "/")
    if not normalized or "\x00" in normalized or normalized.startswith("/"):
        raise RuntimeError(f"unsafe archive member path: {name!r}")
    path = PurePosixPath(normalized)
    parts = path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise RuntimeError(f"unsafe archive member path: {name!r}")
    if any(":" in part for part in parts):
        raise RuntimeError(f"unsafe archive member drive path: {name!r}")
    return tuple(parts)


def _safe_link_target(member_name: str, link_name: str, *, hardlink: bool) -> None:
    link = str(link_name or "").replace("\\", "/")
    if not link or link.startswith("/"):
        raise RuntimeError(f"unsafe archive link target: {member_name!r} -> {link_name!r}")
    member = PurePosixPath(*_safe_archive_parts(member_name))
    base = PurePosixPath() if hardlink else member.parent
    normalized = posixpath.normpath(str(base / PurePosixPath(link)))
    _safe_archive_parts(normalized)


def _validate_archive_limits(member_count: int, total_size: int, archive: Path) -> None:
    if member_count > MAX_ARCHIVE_MEMBERS:
        raise RuntimeError(
            f"archive contains too many members ({member_count} > {MAX_ARCHIVE_MEMBERS}): {archive.name}"
        )
    if total_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        raise RuntimeError(
            f"archive expands beyond the {MAX_ARCHIVE_UNCOMPRESSED_BYTES}-byte safety limit: {archive.name}"
        )


def _node_search_dirs(root: Path, version: str = PINNED_NODE_VERSION) -> list[Path]:
    base = root / f"node-v{version}"
    if host_os() == "windows":
        return [base]
    return [base / "bin", base]


def _pwsh_search_dirs(root: Path, version: str = PINNED_PWSH_VERSION) -> list[Path]:
    return [root / f"powershell-{version}"]


def _clear_macos_quarantine(path: Path) -> None:
    if host_os() != "darwin" or not path.exists():
        return
    try:
        subprocess.run(
            ["xattr", "-dr", "com.apple.quarantine", str(path)],
            capture_output=True,
            check=False,
        )
    except OSError:
        pass


def _node_arch(executable: Path) -> str | None:
    try:
        completed = subprocess.run(
            [str(executable), "-p", "process.arch"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    text = completed.stdout.strip().lower()
    if text in {"arm64", "aarch64"}:
        return "arm64"
    if text in {"x64", "x86_64", "amd64"}:
        return "x64"
    return None


def _command_version(executable: Path, arguments: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            [str(executable), *arguments],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip() or completed.stderr.strip() or None


def _uv_is_usable(executable: Path) -> bool:
    version = _command_version(executable, ["--version"])
    fields = version.strip().lower().split() if version else []
    return len(fields) >= 2 and fields[:2] == ["uv", PINNED_UV_VERSION]


def _npm_is_usable(node: Path, npm: Path) -> bool:
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join(
        [str(node.parent), env.get("PATH", "")]
    ).rstrip(os.pathsep)
    command = [str(npm), "--version"]
    if os.name == "nt" and npm.suffix.lower() in {".cmd", ".bat"}:
        command = [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/s",
            "/c",
            subprocess.list2cmdline(command),
        ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return bool(completed.stdout.strip())


def _python_arch(executable: Path) -> str | None:
    try:
        completed = subprocess.run(
            [
                str(executable),
                "-c",
                "import platform; m=platform.machine().lower(); "
                "print('arm64' if m in {'arm64','aarch64'} else "
                "'x64' if m in {'x86_64','amd64','x64'} else m)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    text = completed.stdout.strip().lower()
    return text if text in {"arm64", "x64"} else None



def _extract_archive(archive: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip" or archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as handle:
            members = handle.infolist()
            _validate_archive_limits(
                len(members),
                sum(max(0, int(member.file_size)) for member in members),
                archive,
            )
            for member in members:
                parts = _safe_archive_parts(member.filename)
                if member.flag_bits & 0x1:
                    raise RuntimeError(f"encrypted archive member is not supported: {member.filename}")
                mode = (member.external_attr >> 16) & 0o170000
                if stat.S_ISLNK(mode):
                    raise RuntimeError(f"symbolic links are not allowed in zip archives: {member.filename}")
                target = destination.joinpath(*parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with handle.open(member, "r") as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
        return
    with tarfile.open(archive, "r:*") as handle:
        members = handle.getmembers()
        _validate_archive_limits(
            len(members),
            sum(max(0, int(member.size)) for member in members if member.isfile()),
            archive,
        )
        symbolic_paths = {
            _safe_archive_parts(member.name)
            for member in members
            if member.issym()
        }
        for member in members:
            parts = _safe_archive_parts(member.name)
            member.mode &= 0o777
            if member.isdev() or member.isfifo():
                raise RuntimeError(f"special archive member is not allowed: {member.name}")
            if member.issym() or member.islnk():
                _safe_link_target(member.name, member.linkname, hardlink=member.islnk())
            if any(
                len(parts) > len(link_parts) and parts[: len(link_parts)] == link_parts
                for link_parts in symbolic_paths
            ):
                raise RuntimeError(f"archive member is nested beneath a symbolic link: {member.name}")
        handle.extractall(destination)


def _mark_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _python_version(executable: Path) -> tuple[int, int] | None:
    try:
        completed = subprocess.run(
            [str(executable), "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    text = completed.stdout.strip()
    try:
        major_text, minor_text = text.split(".", 1)
        return int(major_text), int(minor_text)
    except ValueError:
        return None


def _node_major(executable: Path) -> int | None:
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    text = completed.stdout.strip().lstrip("v")
    try:
        return int(text.split(".", 1)[0])
    except ValueError:
        return None


def _candidate_python_names() -> list[str]:
    if host_os() == "windows":
        return ["python3.12.exe", "python312.exe", "python.exe"]
    return ["python3.12", "python3", "python"]


def find_python_312(extra_bin_dirs: list[Path] | None = None) -> Path | None:
    wanted = cpu_arch()
    current = Path(sys.executable).resolve()
    if sys.version_info[:2] == REQUIRED_PYTHON:
        current_arch = _python_arch(current)
        # Accept current interpreter when arch matches, or when arch cannot be probed.
        if current_arch in {None, wanted}:
            return current

    searched: list[Path] = []
    for directory in extra_bin_dirs or []:
        for name in _candidate_python_names():
            searched.append(directory / name)
    for name in ("python3.12", "python3", "python"):
        located = shutil.which(name)
        if located:
            searched.append(Path(located))
    if host_os() == "windows":
        py_launcher = shutil.which("py")
        if py_launcher:
            try:
                completed = subprocess.run(
                    [py_launcher, "-3.12", "-c", "import sys; print(sys.executable)"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                text = completed.stdout.strip()
                if text:
                    searched.append(Path(text))
            except (OSError, subprocess.CalledProcessError):
                pass

    seen: set[str] = set()
    for candidate in searched:
        resolved = candidate.expanduser()
        key = str(resolved).casefold()
        if key in seen or not resolved.is_file():
            continue
        seen.add(key)
        version = _python_version(resolved)
        if not version or version != REQUIRED_PYTHON:
            continue
        arch = _python_arch(resolved)
        if arch not in {None, wanted}:
            continue
        return resolved.resolve()
    return None


def find_node_npm(extra_bin_dirs: list[Path] | None = None) -> tuple[Path, Path] | None:
    wanted = cpu_arch()
    node_names = ["node.exe", "node"] if host_os() == "windows" else ["node"]
    npm_names = ["npm.cmd", "npm.exe", "npm"] if host_os() == "windows" else ["npm"]
    node_candidates: list[Path] = []
    for directory in extra_bin_dirs or []:
        for name in node_names:
            node_candidates.append(directory / name)
    which_node = shutil.which("node")
    if which_node:
        node_candidates.append(Path(which_node))

    seen: set[str] = set()
    for node in node_candidates:
        candidate = node.expanduser()
        key = str(candidate).casefold()
        if key in seen or not candidate.is_file():
            continue
        seen.add(key)
        major = _node_major(candidate)
        if major is None or major < REQUIRED_NODE_MAJOR:
            continue
        arch = _node_arch(candidate)
        if arch not in {None, wanted}:
            continue
        bin_dir = candidate.parent
        npm = None
        for name in npm_names:
            shim = bin_dir / name
            if shim.is_file():
                npm = shim
                break
        if npm is not None and npm.is_file() and _npm_is_usable(candidate, npm):
            return _absolute_path(candidate), _absolute_path(npm)
    return None


def _pwsh_major(executable: Path) -> int | None:
    try:
        completed = subprocess.run(
            [str(executable), "-NoProfile", "-Command", "$PSVersionTable.PSVersion.Major"],
            capture_output=True,
            text=True,
            check=True,
        )
        return int(completed.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.CalledProcessError, ValueError, IndexError):
        return None


def find_pwsh(extra_bin_dirs: list[Path] | None = None) -> Path | None:
    names = ["pwsh.exe", "pwsh"] if host_os() == "windows" else ["pwsh"]
    searched: list[Path] = []
    for directory in extra_bin_dirs or []:
        for name in names:
            searched.append(directory / name)
    # Only PowerShell 7+ is supported; Windows PowerShell 5.1 is not cross-platform
    # compatible with the same indexing scripts.
    for name in ("pwsh", "pwsh.exe"):
        located = shutil.which(name)
        if located:
            searched.append(Path(located))
    seen: set[str] = set()
    for candidate in searched:
        path = candidate.expanduser()
        key = str(path).casefold()
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        major = _pwsh_major(path)
        if major is not None and major >= 7:
            return _absolute_path(path)
    return None


def _ensure_uv(root: Path, *, dry_run: bool = False, download: Downloader = _default_download) -> Path:
    binary_name = "uv.exe" if host_os() == "windows" else "uv"
    uv_bin = root / "uv" / binary_name
    if uv_bin.is_file() and _uv_is_usable(uv_bin):
        return uv_bin

    asset = uv_asset_name()
    url = f"https://github.com/astral-sh/uv/releases/download/{PINNED_UV_VERSION}/{asset}"
    print(f"  Installing uv {PINNED_UV_VERSION} for Python bootstrap...")
    if dry_run:
        return uv_bin

    with tempfile.TemporaryDirectory(prefix="evidence-first-uv-") as tmp:
        archive = Path(tmp) / asset
        extract_root = Path(tmp) / "extracted"
        download(url, archive)
        _verify_archive(archive)
        _extract_archive(archive, extract_root)
        matches = list(extract_root.rglob(binary_name))
        if not matches:
            raise RuntimeError(f"uv binary missing from {asset}")
        uv_bin.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(matches[0], uv_bin)
        if host_os() != "windows":
            _mark_executable(uv_bin)
            _clear_macos_quarantine(uv_bin)
    if not _uv_is_usable(uv_bin):
        raise RuntimeError(f"uv failed its post-install execution check: {uv_bin}")
    return uv_bin


def install_python_312(
    root: Path,
    *,
    dry_run: bool = False,
    download: Downloader = _default_download,
) -> Path:
    uv = _ensure_uv(root, dry_run=dry_run, download=download)
    print(f"  Installing Python {PINNED_PYTHON_VERSION} via uv...")
    if dry_run:
        return root / "python3.12"
    env = os.environ.copy()
    python_dir = root / "python"
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    env["UV_PYTHON_INSTALL_DIR"] = str(python_dir)
    env["UV_PYTHON_BIN_DIR"] = str(bin_dir)
    # Keep uv from trying to write user-global shim directories.
    env["XDG_BIN_HOME"] = str(bin_dir)
    subprocess.run(
        [str(uv), "python", "install", PINNED_PYTHON_VERSION],
        check=True,
        env=env,
    )
    completed = subprocess.run(
        [str(uv), "python", "find", PINNED_PYTHON_VERSION],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    path = Path(completed.stdout.strip())
    if not path.is_file():
        raise RuntimeError("uv installed Python 3.12 but the interpreter path was not found")
    version = _python_version(path)
    architecture = _python_arch(path)
    if not version or version != REQUIRED_PYTHON or architecture not in {None, cpu_arch()}:
        raise RuntimeError(
            f"managed Python failed its post-install version/architecture check: {path}"
        )
    return path.resolve()


def install_node_npm(
    root: Path,
    *,
    dry_run: bool = False,
    download: Downloader = _default_download,
) -> tuple[Path, Path]:
    version = PINNED_NODE_VERSION
    asset = node_asset_name(version)
    target = root / f"node-v{version}"
    node_name = "node.exe" if host_os() == "windows" else "node"
    npm_name = "npm.cmd" if host_os() == "windows" else "npm"
    if host_os() == "windows":
        node_path = target / node_name
        npm_path = target / npm_name
    else:
        node_path = target / "bin" / node_name
        npm_path = target / "bin" / npm_name

    if node_path.is_file() and npm_path.is_file() and (_node_major(node_path) or 0) >= REQUIRED_NODE_MAJOR:
        if _node_arch(node_path) in {None, cpu_arch()} and _npm_is_usable(node_path, npm_path):
            return _absolute_path(node_path), _absolute_path(npm_path)

    url = f"https://nodejs.org/dist/v{version}/{asset}"
    print(f"  Installing Node.js {version} (includes npm)...")
    if dry_run:
        return node_path, npm_path

    with tempfile.TemporaryDirectory(prefix="evidence-first-node-") as tmp:
        archive = Path(tmp) / asset
        extract_root = Path(tmp) / "extracted"
        download(url, archive)
        _verify_archive(archive)
        _extract_archive(archive, extract_root)
        children = [path for path in extract_root.iterdir() if path.is_dir()]
        if len(children) != 1:
            raise RuntimeError(f"unexpected Node.js archive layout in {asset}")
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(children[0]), str(target))

    if host_os() != "windows":
        _mark_executable(node_path)
        if npm_path.is_file():
            _mark_executable(npm_path)
        _clear_macos_quarantine(target)
    if not node_path.is_file() or not npm_path.is_file():
        raise RuntimeError(f"Node.js/npm missing after extracting {asset}")
    if (_node_major(node_path) or 0) < REQUIRED_NODE_MAJOR or not _npm_is_usable(node_path, npm_path):
        raise RuntimeError(f"Node.js/npm failed its post-install execution check: {target}")
    # Keep the npm shim path (do not resolve through to npm-cli.js).
    return _absolute_path(node_path), _absolute_path(npm_path)


def install_pwsh(
    root: Path,
    *,
    dry_run: bool = False,
    download: Downloader = _default_download,
) -> Path:
    version = PINNED_PWSH_VERSION
    asset = pwsh_asset_name(version)
    target = root / f"powershell-{version}"
    pwsh_name = "pwsh.exe" if host_os() == "windows" else "pwsh"
    pwsh_path = target / pwsh_name
    if pwsh_path.is_file() and (_pwsh_major(pwsh_path) or 0) >= 7:
        return _absolute_path(pwsh_path)

    url = f"https://github.com/PowerShell/PowerShell/releases/download/v{version}/{asset}"
    print(f"  Installing PowerShell {version} (pwsh)...")
    if dry_run:
        return pwsh_path

    with tempfile.TemporaryDirectory(prefix="evidence-first-pwsh-") as tmp:
        archive = Path(tmp) / asset
        extract_root = Path(tmp) / "extracted"
        download(url, archive)
        _verify_archive(archive)
        _extract_archive(archive, extract_root)
        # Portable archives may extract flat or into a single top-level folder.
        matches = list(extract_root.rglob(pwsh_name))
        if not matches:
            raise RuntimeError(f"pwsh binary missing from {asset}")
        source_root = matches[0].parent
        if target.exists():
            shutil.rmtree(target)
        # Prefer rename when possible; fall back to copy for cross-device temp dirs.
        try:
            shutil.move(str(source_root), str(target))
        except OSError:
            shutil.copytree(source_root, target, symlinks=True)
            shutil.rmtree(source_root, ignore_errors=True)

    if host_os() != "windows":
        _mark_executable(pwsh_path)
        _clear_macos_quarantine(target)
    if not pwsh_path.is_file():
        raise RuntimeError(f"pwsh missing after extracting {asset}")
    if (_pwsh_major(pwsh_path) or 0) < 7:
        hint = (
            " On Ubuntu, install the runtime libraries with: "
            "sudo apt-get update && sudo apt-get install -y ca-certificates libicu-dev libssl3 zlib1g"
            if host_os() == "linux"
            else " Check host security policy and executable permissions, then retry."
        )
        raise RuntimeError(f"PowerShell 7 failed its post-install execution check: {pwsh_path}.{hint}")
    return _absolute_path(pwsh_path)


def prepend_path(directories: list[Path]) -> None:
    parts = [str(path) for path in directories if path.is_dir()]
    if not parts:
        return
    current = os.environ.get("PATH", "")
    os.environ["PATH"] = os.pathsep.join(parts + ([current] if current else []))


def reexec_with_python(python_exe: Path, script: Path, argv: list[str]) -> None:
    env = os.environ.copy()
    env[BOOTSTRAP_ENV] = "1"
    print(f"  Re-launching installer with {python_exe}")
    os.execve(str(python_exe), [str(python_exe), str(script), *argv], env)


def ensure_runtimes(
    *,
    state_home: Path | None = None,
    script_path: Path,
    argv: list[str],
    dry_run: bool = False,
    skip: bool = False,
    need_node: bool = True,
    need_pwsh: bool = True,
    reexec: bool = True,
    download: Downloader = _default_download,
) -> dict[str, str]:
    """Ensure Python 3.12, Node/npm, and pwsh exist for the host CPU arch."""
    if skip:
        python = Path(sys.executable).resolve()
        pair = find_node_npm()
        pwsh = find_pwsh()
        return {
            "python": str(python),
            "node": str(pair[0]) if pair else "",
            "npm": str(pair[1]) if pair else "",
            "pwsh": str(pwsh) if pwsh else "",
            "arch": cpu_arch(),
            "bootstrapped": "0",
        }

    host = validate_host_runtime()
    root = toolchain_root(state_home)
    root.mkdir(parents=True, exist_ok=True)
    arch = cpu_arch()
    print("\nRuntime bootstrap:")
    print(f"  Toolchain cache: {root}")
    print(f"  Host arch: {host_os()}-{arch}")
    if host_os() == "linux":
        print(f"  Linux host: {host.get('distribution', 'unknown')} ({host.get('libc', 'unknown')})")
        if host.get("distributionId") not in {"", "ubuntu"}:
            print("  Note: Ubuntu 22.04/24.04 is the supported Linux baseline; this glibc host is best-effort.")

    python = find_python_312([root / "bin", root / "python"])
    bootstrapped = False
    if python is None:
        if dry_run:
            print("  Would install Python 3.12 via uv")
            python = Path(sys.executable).resolve()
        else:
            python = install_python_312(root, dry_run=False, download=download)
            bootstrapped = True
    else:
        version = _python_version(python) or REQUIRED_PYTHON
        py_arch = _python_arch(python) or "unknown"
        print(f"  Python: {python} ({version[0]}.{version[1]}, {py_arch})")

    node = npm = None
    if need_node:
        pair = find_node_npm(_node_search_dirs(root))
        if pair is None:
            if dry_run:
                print(f"  Would install Node.js {PINNED_NODE_VERSION} (includes npm)")
                node = Path("node")
                npm = Path("npm")
            else:
                node, npm = install_node_npm(root, dry_run=False, download=download)
                bootstrapped = True
        else:
            node, npm = pair
            node_arch = _node_arch(node) or "unknown"
            print(f"  Node.js: {node} ({node_arch})")
            print(f"  npm: {npm}")
        if node is not None and node.parent.is_dir():
            prepend_path([node.parent])

    pwsh = None
    if need_pwsh:
        pwsh = find_pwsh(_pwsh_search_dirs(root))
        if pwsh is None:
            if dry_run:
                print(f"  Would install PowerShell {PINNED_PWSH_VERSION} (pwsh)")
                pwsh = Path("pwsh")
            else:
                pwsh = install_pwsh(root, dry_run=False, download=download)
                bootstrapped = True
        else:
            print(f"  PowerShell: {pwsh}")
        if pwsh is not None and pwsh.parent.is_dir():
            prepend_path([pwsh.parent])

    if (
        reexec
        and not dry_run
        and os.environ.get(BOOTSTRAP_ENV) != "1"
        and Path(sys.executable).resolve() != python.resolve()
        and (
            sys.version_info[:2] != REQUIRED_PYTHON
            or (_python_arch(Path(sys.executable)) not in {None, arch})
        )
    ):
        prepend_path([python.parent])
        reexec_with_python(python, script_path, argv)

    return {
        "python": str(python),
        "node": str(node) if node else "",
        "npm": str(npm) if npm else "",
        "pwsh": str(pwsh) if pwsh else "",
        "arch": arch,
        "bootstrapped": "1" if bootstrapped else "0",
    }
