from __future__ import annotations

import importlib.util
import io
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "installer" / "bootstrap_runtimes.py"


def _load():
    spec = importlib.util.spec_from_file_location("bootstrap_runtimes", MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("system", "arch", "uv_part", "node_part", "pwsh_part"),
    [
        ("darwin", "arm64", "aarch64-apple-darwin", "darwin-arm64", "osx-arm64"),
        ("darwin", "x64", "x86_64-apple-darwin", "darwin-x64", "osx-x64"),
        ("windows", "arm64", "aarch64-pc-windows-msvc", "win-arm64", "win-arm64"),
        ("windows", "x64", "x86_64-pc-windows-msvc", "win-x64", "win-x64"),
        ("linux", "arm64", "aarch64-unknown-linux-gnu", "linux-arm64", "linux-arm64"),
        ("linux", "x64", "x86_64-unknown-linux-gnu", "linux-x64", "linux-x64"),
    ],
)
def test_runtime_asset_names_cover_os_and_cpu_matrix(
    monkeypatch: pytest.MonkeyPatch,
    system: str,
    arch: str,
    uv_part: str,
    node_part: str,
    pwsh_part: str,
) -> None:
    module = _load()
    monkeypatch.setattr(module, "host_os", lambda: system)
    monkeypatch.setattr(module, "cpu_arch", lambda: arch)
    assert uv_part in module.uv_asset_name()
    assert node_part in module.node_asset_name()
    assert pwsh_part in module.pwsh_asset_name()
    assert module.uv_asset_name() in module.ARCHIVE_SHA256
    assert module.node_asset_name() in module.ARCHIVE_SHA256
    assert module.pwsh_asset_name() in module.ARCHIVE_SHA256
    sys.modules.pop("bootstrap_runtimes", None)


def test_uv_and_node_asset_names_for_current_host() -> None:
    module = _load()
    uv = module.uv_asset_name()
    node = module.node_asset_name()
    pwsh = module.pwsh_asset_name()
    assert "uv-" in uv
    assert node.startswith("node-v20.")
    assert "powershell" in pwsh.lower()
    if module.host_os() == "darwin":
        assert "apple-darwin" in uv
        assert "darwin" in node
        assert "osx-" in pwsh
    elif module.host_os() == "windows":
        assert "windows" in uv or "msvc" in uv
        assert "win-" in node
        assert "win-" in pwsh
    else:
        assert "linux" in uv
        assert "linux" in node
        assert "linux-" in pwsh
    sys.modules.pop("bootstrap_runtimes", None)


def test_ensure_runtimes_skip_uses_current_interpreter() -> None:
    module = _load()
    result = module.ensure_runtimes(
        script_path=ROOT / "install.py",
        argv=[],
        skip=True,
        reexec=False,
    )
    assert Path(result["python"]).resolve() == Path(sys.executable).resolve()
    assert result["bootstrapped"] == "0"
    assert "arch" in result
    sys.modules.pop("bootstrap_runtimes", None)


def test_ensure_runtimes_dry_run_without_downloads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load()

    def boom(url: str, destination: Path) -> None:
        raise AssertionError(f"download should not run in dry-run: {url}")

    monkeypatch.setattr(module, "find_python_312", lambda extra_bin_dirs=None: None)
    monkeypatch.setattr(module, "find_node_npm", lambda extra_bin_dirs=None: None)
    monkeypatch.setattr(module, "find_pwsh", lambda extra_bin_dirs=None: None)

    result = module.ensure_runtimes(
        state_home=tmp_path,
        script_path=ROOT / "install.py",
        argv=["--dry-run"],
        dry_run=True,
        reexec=False,
        download=boom,
    )
    assert Path(result["python"]).resolve() == Path(sys.executable).resolve()
    assert result["node"]
    assert result["npm"]
    assert result["pwsh"]
    sys.modules.pop("bootstrap_runtimes", None)


def test_find_node_npm_keeps_npm_shim_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load()
    bin_dir = tmp_path / "node-v20.20.2" / "bin"
    bin_dir.mkdir(parents=True)
    node = bin_dir / "node"
    npm = bin_dir / "npm"
    node.write_text("#!/bin/sh\necho v20.20.2\n", encoding="utf-8")
    npm.write_text("#!/bin/sh\necho 10.0.0\n", encoding="utf-8")
    node.chmod(0o755)
    npm.chmod(0o755)

    monkeypatch.setattr(module, "host_os", lambda: "darwin")
    monkeypatch.setattr(module, "cpu_arch", lambda: "arm64")
    monkeypatch.setattr(module, "_node_arch", lambda _path: "arm64")
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)

    found = module.find_node_npm([bin_dir])
    assert found is not None
    assert found[0] == module._absolute_path(node)
    assert found[1] == module._absolute_path(npm)
    assert "npm-cli.js" not in str(found[1])
    sys.modules.pop("bootstrap_runtimes", None)


def test_cpu_arch_prefers_apple_silicon_over_rosetta_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    monkeypatch.setattr(module, "host_os", lambda: "darwin")
    monkeypatch.setattr(module, "_machine", lambda: "x86_64")

    class Result:
        returncode = 0
        stdout = "1\n"

    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Result())
    assert module.cpu_arch() == "arm64"
    sys.modules.pop("bootstrap_runtimes", None)


def test_runtime_archive_checksum_mismatch_fails_closed(tmp_path: Path) -> None:
    module = _load()
    archive = tmp_path / module.node_asset_name()
    archive.write_bytes(b"corrupted")
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        module._verify_archive(archive)
    sys.modules.pop("bootstrap_runtimes", None)


def test_runtime_download_rejects_oversized_declared_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load()

    class Response:
        headers = {"Content-Length": str(module.MAX_DOWNLOAD_BYTES + 1)}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read(_size):
            return b""

    monkeypatch.setattr(module.urllib_request, "urlopen", lambda *_args, **_kwargs: Response())
    destination = tmp_path / "runtime.zip"
    with pytest.raises(RuntimeError, match="download exceeds"):
        module._default_download("https://example.invalid/runtime.zip", destination)
    assert not destination.exists()
    assert not destination.with_suffix(".zip.tmp").exists()
    sys.modules.pop("bootstrap_runtimes", None)


def test_uv_probe_accepts_pinned_version_with_build_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    monkeypatch.setattr(
        module,
        "_command_version",
        lambda _path, _args: f"uv {module.PINNED_UV_VERSION} (build arch)",
    )
    assert module._uv_is_usable(Path("uv")) is True
    monkeypatch.setattr(module, "_command_version", lambda _path, _args: "uv 0.11.0")
    assert module._uv_is_usable(Path("uv")) is False
    sys.modules.pop("bootstrap_runtimes", None)


def test_zip_path_traversal_is_rejected_before_extraction(tmp_path: Path) -> None:
    module = _load()
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../escaped.txt", "no")
    destination = tmp_path / "extract"
    with pytest.raises(RuntimeError, match="unsafe archive member"):
        module._extract_archive(archive, destination)
    assert not (tmp_path / "escaped.txt").exists()
    sys.modules.pop("bootstrap_runtimes", None)


def test_tar_symlink_escape_is_rejected_before_extraction(tmp_path: Path) -> None:
    module = _load()
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        member = tarfile.TarInfo("runtime/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "../../outside"
        handle.addfile(member, io.BytesIO())
    with pytest.raises(RuntimeError, match="unsafe archive"):
        module._extract_archive(archive, tmp_path / "extract")
    sys.modules.pop("bootstrap_runtimes", None)


def test_find_node_npm_never_pairs_node_with_unrelated_global_npm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load()
    node_dir = tmp_path / "node-only"
    global_dir = tmp_path / "global-npm"
    node_dir.mkdir()
    global_dir.mkdir()
    node = node_dir / "node"
    npm = global_dir / "npm"
    node.write_text("#!/bin/sh\necho v20.20.2\n", encoding="utf-8")
    npm.write_text("#!/bin/sh\necho 10.0.0\n", encoding="utf-8")
    node.chmod(0o755)
    npm.chmod(0o755)
    monkeypatch.setattr(module, "cpu_arch", lambda: "x64")
    monkeypatch.setattr(module, "_node_major", lambda _path: 20)
    monkeypatch.setattr(module, "_node_arch", lambda _path: "x64")
    monkeypatch.setattr(
        module.shutil,
        "which",
        lambda name: str(npm) if name == "npm" else None,
    )
    assert module.find_node_npm([node_dir]) is None
    sys.modules.pop("bootstrap_runtimes", None)


def test_ubuntu_runtime_guard_rejects_musl_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load()
    monkeypatch.setattr(module, "host_os", lambda: "linux")
    monkeypatch.setattr(module, "cpu_arch", lambda: "x64")
    monkeypatch.setattr(module.platform, "libc_ver", lambda: ("musl", "1.2.5"))
    with pytest.raises(RuntimeError, match="Ubuntu 22.04/24.04"):
        module.validate_host_runtime()
    sys.modules.pop("bootstrap_runtimes", None)
