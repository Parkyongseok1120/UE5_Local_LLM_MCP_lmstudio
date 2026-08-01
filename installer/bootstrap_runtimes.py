"""Download and cache Python 3.12 and Node.js/npm for the integrated installer."""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

REQUIRED_PYTHON = (3, 12)
REQUIRED_NODE_MAJOR = 20
PINNED_NODE_VERSION = "20.20.2"
PINNED_UV_VERSION = "0.12.1"
PINNED_PWSH_VERSION = "7.5.4"
TOOLCHAIN_DIRNAME = "evidence-first-runtimes"
BOOTSTRAP_ENV = "EVIDENCE_FIRST_RUNTIME_BOOTSTRAPPED"


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
            shutil.copyfileobj(response, handle)
        temporary.replace(destination)
    except urllib_error.URLError as exc:
        if temporary.exists():
            temporary.unlink()
        raise RuntimeError(f"failed to download {url}: {exc}") from exc


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
            handle.extractall(destination)
        return
    with tarfile.open(archive, "r:*") as handle:
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
    if sys.version_info[:2] >= REQUIRED_PYTHON:
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
        if not version or version < REQUIRED_PYTHON:
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
        if npm is None:
            which_npm = shutil.which("npm")
            if which_npm:
                npm = Path(which_npm)
        if npm is not None and npm.is_file():
            return _absolute_path(candidate), _absolute_path(npm)
    return None


def find_pwsh(extra_bin_dirs: list[Path] | None = None) -> Path | None:
    names = ["pwsh.exe", "pwsh"] if host_os() == "windows" else ["pwsh"]
    searched: list[Path] = []
    for directory in extra_bin_dirs or []:
        for name in names:
            searched.append(directory / name)
    # Windows may already have Windows PowerShell; prefer pwsh (PowerShell 7+).
    for name in ("pwsh", "pwsh.exe"):
        located = shutil.which(name)
        if located:
            searched.append(Path(located))
    if host_os() == "windows":
        located = shutil.which("powershell")
        if located:
            searched.append(Path(located))

    seen: set[str] = set()
    for candidate in searched:
        path = candidate.expanduser()
        key = str(path).casefold()
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        try:
            completed = subprocess.run(
                [str(path), "-NoProfile", "-Command", "$PSVersionTable.PSVersion.Major"],
                capture_output=True,
                text=True,
                check=True,
            )
            major = int(completed.stdout.strip().splitlines()[-1])
        except (OSError, subprocess.CalledProcessError, ValueError):
            continue
        if major >= 7 or path.name.lower().startswith("powershell"):
            # Accept Windows PowerShell 5.1 as a last-resort Windows fallback.
            if major < 7 and host_os() != "windows":
                continue
            return _absolute_path(path)
    return None


def _ensure_uv(root: Path, *, dry_run: bool = False, download: Downloader = _default_download) -> Path:
    binary_name = "uv.exe" if host_os() == "windows" else "uv"
    uv_bin = root / "uv" / binary_name
    if uv_bin.is_file():
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
        _extract_archive(archive, extract_root)
        matches = list(extract_root.rglob(binary_name))
        if not matches:
            raise RuntimeError(f"uv binary missing from {asset}")
        uv_bin.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(matches[0], uv_bin)
        if host_os() != "windows":
            _mark_executable(uv_bin)
    return uv_bin


def install_python_312(
    root: Path,
    *,
    dry_run: bool = False,
    download: Downloader = _default_download,
) -> Path:
    uv = _ensure_uv(root, dry_run=dry_run, download=download)
    print("  Installing Python 3.12 via uv...")
    if dry_run:
        return root / "python3.12"
    env = os.environ.copy()
    python_dir = root / "python"
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    env["UV_PYTHON_INSTALL_DIR"] = str(python_dir)
    env["UV_PYTHON_BIN_DIR"] = str(bin_dir)
    # Keep uv from trying to write user-global shim directories.
    env.setdefault("XDG_BIN_HOME", str(bin_dir))
    subprocess.run([str(uv), "python", "install", "3.12"], check=True, env=env)
    completed = subprocess.run(
        [str(uv), "python", "find", "3.12"],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    path = Path(completed.stdout.strip())
    if not path.is_file():
        raise RuntimeError("uv installed Python 3.12 but the interpreter path was not found")
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
        if _node_arch(node_path) in {None, cpu_arch()}:
            return _absolute_path(node_path), _absolute_path(npm_path)

    url = f"https://nodejs.org/dist/v{version}/{asset}"
    print(f"  Installing Node.js {version} (includes npm)...")
    if dry_run:
        return node_path, npm_path

    with tempfile.TemporaryDirectory(prefix="evidence-first-node-") as tmp:
        archive = Path(tmp) / asset
        extract_root = Path(tmp) / "extracted"
        download(url, archive)
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
    if pwsh_path.is_file():
        return _absolute_path(pwsh_path)

    url = f"https://github.com/PowerShell/PowerShell/releases/download/v{version}/{asset}"
    print(f"  Installing PowerShell {version} (pwsh)...")
    if dry_run:
        return pwsh_path

    with tempfile.TemporaryDirectory(prefix="evidence-first-pwsh-") as tmp:
        archive = Path(tmp) / asset
        extract_root = Path(tmp) / "extracted"
        download(url, archive)
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

    root = toolchain_root(state_home)
    root.mkdir(parents=True, exist_ok=True)
    arch = cpu_arch()
    print("\nRuntime bootstrap:")
    print(f"  Toolchain cache: {root}")
    print(f"  Host arch: {host_os()}-{arch}")

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
            sys.version_info[:2] < REQUIRED_PYTHON
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
