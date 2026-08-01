"""Download and cache Python 3.12 and Node.js/npm for the integrated installer."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import posixpath
import re
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

RUNTIME_MANIFEST_PATH = Path(__file__).with_name("runtime-manifest.json")


def _load_runtime_manifest(path: Path = RUNTIME_MANIFEST_PATH) -> dict:
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise RuntimeError(f"runtime manifest is missing or oversized: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"runtime manifest is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("runtime manifest root must be a JSON object")
    return payload


RUNTIME_MANIFEST = _load_runtime_manifest()
RUNTIME_DEFINITIONS = RUNTIME_MANIFEST.get("runtimes") or {}


def _runtime_definition(name: str) -> dict:
    value = RUNTIME_DEFINITIONS.get(name)
    if not isinstance(value, dict):
        raise RuntimeError(f"runtime manifest definition is missing: {name}")
    return value


PINNED_PYTHON_VERSION = str(_runtime_definition("python").get("version") or "")
REQUIRED_PYTHON = tuple(int(part) for part in PINNED_PYTHON_VERSION.split(".")[:2])
PINNED_NODE_VERSION = str(_runtime_definition("node").get("version") or "")
REQUIRED_NODE_MAJOR = int(PINNED_NODE_VERSION.split(".", 1)[0])
PINNED_UV_VERSION = str(_runtime_definition("uv").get("version") or "")
PINNED_PWSH_VERSION = str(_runtime_definition("pwsh").get("version") or "")
TOOLCHAIN_DIRNAME = "evidence-first-runtimes"
BOOTSTRAP_ENV = "EVIDENCE_FIRST_RUNTIME_BOOTSTRAPPED"
MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024

ARCHIVE_SHA256 = {
    str(asset.get("filename")): str(asset.get("sha256"))
    for definition in RUNTIME_DEFINITIONS.values()
    if isinstance(definition, dict)
    for asset in definition.get("assets") or []
    if isinstance(asset, dict)
}


def validate_runtime_manifest(manifest: dict | None = None) -> dict[str, int]:
    payload = manifest or RUNTIME_MANIFEST
    if payload.get("schemaVersion") != 1:
        raise RuntimeError("runtime manifest schemaVersion must be 1")
    platforms = tuple(payload.get("supportedPlatforms") or [])
    architectures = tuple(payload.get("supportedArchitectures") or [])
    if set(platforms) != {"darwin", "windows", "linux"}:
        raise RuntimeError("runtime manifest must cover darwin/windows/linux")
    if set(architectures) != {"arm64", "x64"}:
        raise RuntimeError("runtime manifest must cover arm64/x64")
    runtimes = payload.get("runtimes")
    if not isinstance(runtimes, dict) or set(runtimes) != {"python", "uv", "node", "pwsh"}:
        raise RuntimeError("runtime manifest must define python, uv, node, and pwsh")
    filenames: set[str] = set()
    asset_count = 0
    for name, definition in runtimes.items():
        if not isinstance(definition, dict) or not str(definition.get("version") or ""):
            raise RuntimeError(f"runtime version is missing: {name}")
        probe = definition.get("executableProbe")
        if not isinstance(probe, dict) or not isinstance(probe.get("args"), list):
            raise RuntimeError(f"runtime executable probe is missing: {name}")
        if name == "python":
            if definition.get("delivery") != "uv-managed":
                raise RuntimeError("python runtime delivery must be uv-managed")
            continue
        template = str(definition.get("urlTemplate") or "")
        if not template.startswith("https://") or "{version}" not in template or "{asset}" not in template:
            raise RuntimeError(f"runtime URL template is invalid: {name}")
        assets = definition.get("assets")
        if not isinstance(assets, list):
            raise RuntimeError(f"runtime assets must be an array: {name}")
        expected = {(platform, arch) for platform in platforms for arch in architectures}
        observed: set[tuple[str, str]] = set()
        for asset in assets:
            if not isinstance(asset, dict):
                raise RuntimeError(f"runtime asset must be an object: {name}")
            pair = (str(asset.get("platform") or ""), str(asset.get("architecture") or ""))
            if pair in observed:
                raise RuntimeError(f"duplicate runtime platform/architecture: {name} {pair}")
            observed.add(pair)
            filename = str(asset.get("filename") or "")
            digest = str(asset.get("sha256") or "")
            executable = str(asset.get("executable") or "")
            if not filename or filename in filenames:
                raise RuntimeError(f"runtime asset filename is missing or duplicated: {filename}")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise RuntimeError(f"runtime SHA-256 is invalid: {filename}")
            if not executable or PurePosixPath(executable).is_absolute() or ".." in PurePosixPath(executable).parts:
                raise RuntimeError(f"runtime executable probe path is invalid: {filename}")
            template.format(version=definition["version"], asset=filename)
            filenames.add(filename)
            asset_count += 1
        if observed != expected:
            raise RuntimeError(f"runtime asset matrix is incomplete: {name}")
    return {"runtimeCount": len(runtimes), "assetCount": asset_count}


validate_runtime_manifest()


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


def _runtime_asset(name: str, system: str | None = None, arch: str | None = None) -> dict:
    target = (
        str(system or host_os()),
        str(arch or cpu_arch()),
    )
    for asset in _runtime_definition(name).get("assets") or []:
        if not isinstance(asset, dict):
            continue
        if (str(asset.get("platform")), str(asset.get("architecture"))) == target:
            return asset
    raise RuntimeError(f"runtime asset is missing for {name} {target[0]}-{target[1]}")


def runtime_download_url(name: str, asset_name: str | None = None) -> str:
    definition = _runtime_definition(name)
    asset = asset_name or str(_runtime_asset(name).get("filename") or "")
    return str(definition.get("urlTemplate") or "").format(
        version=str(definition.get("version") or ""),
        asset=asset,
    )


def uv_asset_name() -> str:
    return str(_runtime_asset("uv").get("filename") or "")


def node_asset_name(version: str = PINNED_NODE_VERSION) -> str:
    if str(version) != PINNED_NODE_VERSION:
        raise ValueError("node version must be updated in runtime-manifest.json first")
    return str(_runtime_asset("node").get("filename") or "")


def pwsh_asset_name(version: str = PINNED_PWSH_VERSION) -> str:
    if str(version) != PINNED_PWSH_VERSION:
        raise ValueError("PowerShell version must be updated in runtime-manifest.json first")
    return str(_runtime_asset("pwsh").get("filename") or "")


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
    arguments = list(
        (_runtime_definition("node").get("architectureProbe") or {}).get("args")
        or ["-p", "process.arch"]
    )
    try:
        completed = subprocess.run(
            [str(executable), *arguments],
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
    arguments = list(
        (_runtime_definition("uv").get("executableProbe") or {}).get("args")
        or ["--version"]
    )
    version = _command_version(executable, arguments)
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
    arguments = list(
        (_runtime_definition("python").get("architectureProbe") or {}).get("args")
        or []
    )
    try:
        completed = subprocess.run(
            [str(executable), *arguments],
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
    arguments = list(
        (_runtime_definition("python").get("executableProbe") or {}).get("args")
        or []
    )
    try:
        completed = subprocess.run(
            [str(executable), *arguments],
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
    arguments = list(
        (_runtime_definition("node").get("executableProbe") or {}).get("args")
        or ["--version"]
    )
    try:
        completed = subprocess.run(
            [str(executable), *arguments],
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
    arguments = list(
        (_runtime_definition("pwsh").get("executableProbe") or {}).get("args")
        or ["-NoProfile", "-Command", "$PSVersionTable.PSVersion.Major"]
    )
    try:
        completed = subprocess.run(
            [str(executable), *arguments],
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
    url = runtime_download_url("uv", asset)
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

    url = runtime_download_url("node", asset)
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

    url = runtime_download_url("pwsh", asset)
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
