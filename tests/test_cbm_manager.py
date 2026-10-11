"""cbm_manager 功能测试（使用内置假二进制，快速且不依赖网络/大文件）。

真实二进制的端到端验证见 tests/test_integration_real.py（需设置 CBM_REAL_BINARY）。

运行：python -m pytest tests/ -v
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tarfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cbm_manager as cm  # noqa: E402

FAKE_SRC = Path(__file__).resolve().parent / "fake_cbm.py"


def _install_fake_binary(mgr: cm.CbmManager) -> None:
    mgr.bin_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FAKE_SRC, mgr.binary_path)
    os.chmod(mgr.binary_path, 0o755)
    mgr._write_installed_info(
        variant="standard", version="0.11.0", tag="v0.11.0",
        asset="codebase-memory-mcp-linux-amd64-portable.tar.gz",
        os="linux", arch="amd64", sha256_verified=True,
    )


@pytest.fixture()
def mgr(tmp_path):
    m = cm.CbmManager(tmp_path / "cbm")
    m.root.mkdir(parents=True, exist_ok=True)
    yield m
    try:
        m.stop()
    except Exception:
        pass


@pytest.fixture()
def mgr_with_bin(tmp_path):
    m = cm.CbmManager(tmp_path / "cbm")
    _install_fake_binary(m)
    yield m
    try:
        m.stop()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 纯逻辑
# --------------------------------------------------------------------------- #
def test_asset_names_standard_linux():
    names = cm.build_asset_names("linux", "amd64", "standard")
    assert names[0] == "codebase-memory-mcp-linux-amd64-portable.tar.gz"
    assert "codebase-memory-mcp-linux-amd64.tar.gz" in names


def test_asset_names_ui_linux():
    names = cm.build_asset_names("linux", "amd64", "ui")
    assert names[0] == "codebase-memory-mcp-ui-linux-amd64-portable.tar.gz"


def test_asset_names_macos_and_windows():
    assert cm.build_asset_names("darwin", "arm64", "standard")[0] == \
        "codebase-memory-mcp-darwin-arm64.tar.gz"
    assert cm.build_asset_names("windows", "amd64", "ui") == \
        ["codebase-memory-mcp-ui-windows-amd64.zip"]


def test_asset_names_invalid_variant():
    with pytest.raises(cm.CbmError):
        cm.build_asset_names("linux", "amd64", "pro")


def test_mirror_placeholder_and_prefix():
    url = "https://github.com/x/y/releases/download/v1/a.tgz"
    assert cm.apply_mirror(url, "") == url
    assert cm.apply_mirror(url, "https://ghproxy.net/") == "https://ghproxy.net/" + url
    assert cm.apply_mirror(url, "https://m/{url}?x=1") == "https://m/" + url + "?x=1"


def test_build_args():
    m = cm.CbmManager("/tmp/nope")
    assert m._build_args("standard", 1234) == []
    assert m._build_args("ui", 1234) == ["--ui=true", "--port=1234"]


def test_default_mirror_constant():
    assert cm.DEFAULT_MIRROR == "https://gh-proxy.com/"


def test_apply_default_mirror():
    url = "https://github.com/DeusData/codebase-memory-mcp/releases/download/v0.11.0/a.tgz"
    assert cm.apply_mirror(url, cm.DEFAULT_MIRROR) == "https://gh-proxy.com/" + url


def test_default_variant_is_ui(mgr):
    # 未显式指定 variant 时，start/install 默认使用 ui
    import inspect

    assert inspect.signature(cm.CbmManager.install).parameters["variant"].default == "ui"
    assert inspect.signature(cm.CbmManager.start).parameters["variant"].default == "ui"


def test_list_releases_parsing(monkeypatch):
    m = cm.CbmManager("/tmp/nope")
    fake = [
        {"tag_name": "v0.11.0", "name": "v0.11.0", "prerelease": False, "published_at": "x"},
        {"tag_name": "v0.10.7", "name": "0.10.7", "prerelease": True, "published_at": "y"},
        {"tag_name": "", "name": "bad"},
    ]

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            import json as _j

            return _j.dumps(fake).encode()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    releases = m.list_releases(10)
    assert len(releases) == 2
    assert releases[0]["version"] == "0.11.0"
    assert releases[1]["prerelease"] is True


def test_state_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    st = cm.CbmState(pid=123, variant="ui", port=9749, cache_dir="/x", started_at=1.0)
    st.save(p)
    loaded = cm.CbmState.load(p)
    assert loaded.pid == 123 and loaded.variant == "ui" and loaded.port == 9749


def test_resolve_cache_dir(mgr, monkeypatch):
    monkeypatch.delenv("CBM_CACHE_DIR", raising=False)
    assert mgr.resolve_cache_dir("/tmp/xx") == Path("/tmp/xx").resolve()
    monkeypatch.setenv("CBM_CACHE_DIR", "/tmp/envcache")
    assert mgr.resolve_cache_dir(None) == Path("/tmp/envcache").resolve()


def test_binary_filename_windows():
    assert cm.binary_filename("windows") == "codebase-memory-mcp.exe"
    assert cm.binary_filename("linux") == "codebase-memory-mcp"


# --------------------------------------------------------------------------- #
# 状态 & 安装识别
# --------------------------------------------------------------------------- #
def test_status_not_installed(mgr):
    s = mgr.status()
    assert s["installed"] is False
    assert s["running"] is False
    assert s["platform"]["os"] in ("linux", "darwin", "windows")


def test_install_info_and_version(mgr_with_bin):
    m = mgr_with_bin
    assert m.is_installed() is True
    ver = m._version_of_binary()
    assert ver and "codebase-memory-mcp" in ver
    assert m.installed_info()["version"] == "0.11.0"


def test_status_installed_not_running(mgr_with_bin):
    s = mgr_with_bin.status()
    assert s["installed"] is True and s["running"] is False
    assert s["binary_version"] == "0.11.0"


# --------------------------------------------------------------------------- #
# 运行控制
# --------------------------------------------------------------------------- #
def test_start_stop_standard(mgr_with_bin):
    m = mgr_with_bin
    assert m.is_running() is False
    info = m.start(variant="standard", cache_dir=str(m.root / "cache"))
    assert info["running"] is True and info["pid"]
    assert m.is_running() is True
    s = m.status()
    assert s["running"] is True and s["pid"] == info["pid"]
    assert s["uptime"] is not None

    with pytest.raises(cm.CbmError):
        m.start(variant="standard", cache_dir=str(m.root / "cache"))

    res = m.stop()
    assert res["running"] is False
    time.sleep(0.4)
    assert m.is_running() is False
    assert m.status()["running"] is False


def test_start_ui_sets_port_and_url(mgr_with_bin):
    m = mgr_with_bin
    info = m.start(variant="ui", port=9751, cache_dir=str(m.root / "cache"))
    assert info["port"] == 9751
    s = m.status()
    assert s["variant"] == "ui" and s["port"] == 9751
    assert s["ui_url"] == "http://127.0.0.1:9751"
    m.stop()


def test_restart_changes_pid(mgr_with_bin):
    m = mgr_with_bin
    m.start(variant="standard", cache_dir=str(m.root / "cache"))
    pid1 = m.status()["pid"]
    info = m.restart()
    assert info["running"] is True
    assert info["pid"] != pid1
    m.stop()


def test_start_when_not_installed_raises(mgr):
    with pytest.raises(cm.CbmError):
        mgr.start(variant="standard")


def test_read_logs(mgr_with_bin):
    m = mgr_with_bin
    m.start(variant="standard", cache_dir=str(m.root / "cache"))
    time.sleep(0.8)
    logs = m.read_logs(50)
    assert isinstance(logs, str)
    assert "fake server" in logs
    m.stop()


# --------------------------------------------------------------------------- #
# MCP 接入
# --------------------------------------------------------------------------- #
def test_mcp_server_config_requires_install(mgr):
    with pytest.raises(cm.CbmError):
        mgr.mcp_server_config()


def test_mcp_server_config_shape(mgr_with_bin):
    m = mgr_with_bin
    cfg = m.mcp_server_config()
    assert cm.BIN_NAME in cfg
    entry = cfg[cm.BIN_NAME]
    # AstrBot 对 stdio command 有白名单，必须使用受信任的 python3 启动器
    assert entry["command"] == "python3"
    assert entry["args"] == [str(m.mcp_launcher_path)]
    assert "CBM_CACHE_DIR" in entry["env"]
    assert m.mcp_launcher_path.exists()
    launcher = m.mcp_launcher_path.read_text("utf-8")
    assert str(m.binary_path) in launcher


# --------------------------------------------------------------------------- #
# 安装（下载 + 校验 + 解包）
# --------------------------------------------------------------------------- #
def _make_tarball(path: Path, name: str) -> str:
    with tarfile.open(path, "w:gz") as tf:
        tf.add(str(FAKE_SRC), arcname=name)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_install_download_verify_extract(mgr, tmp_path, monkeypatch):
    asset = cm.build_asset_names(*cm.detect_platform(), "standard")[0]
    archive = tmp_path / ("dl-" + asset)
    digest = _make_tarball(archive, "codebase-memory-mcp")

    monkeypatch.setattr(
        cm.CbmManager, "_download",
        lambda self, url, dest, progress=None: (
            dest.parent.mkdir(parents=True, exist_ok=True),
            shutil.copy2(archive, dest),
            progress and progress(1, 1),
        )[-1],
    )
    monkeypatch.setattr(
        cm.CbmManager, "_fetch_checksums",
        lambda self, tag, mirror: {asset: digest},
    )
    info = mgr.install(variant="standard", version="0.11.0")
    assert info["variant"] == "standard" and info["version"] == "0.11.0"
    assert info["sha256_verified"] is True
    assert mgr.is_installed() is True
    assert os.access(mgr.binary_path, os.X_OK)


def test_install_checksum_mismatch_rejected(mgr, tmp_path, monkeypatch):
    asset = cm.build_asset_names(*cm.detect_platform(), "standard")[0]
    archive = tmp_path / ("dl-" + asset)
    _make_tarball(archive, "codebase-memory-mcp")

    monkeypatch.setattr(
        cm.CbmManager, "_download",
        lambda self, url, dest, progress=None: (
            dest.parent.mkdir(parents=True, exist_ok=True),
            shutil.copy2(archive, dest),
        )[-1],
    )
    monkeypatch.setattr(
        cm.CbmManager, "_fetch_checksums",
        lambda self, tag, mirror: {asset: "0" * 64},
    )
    with pytest.raises(cm.CbmError):
        mgr.install(variant="standard", version="0.11.0")
    assert mgr.is_installed() is False


def test_install_replaces_running_binary_stops_first(mgr_with_bin, tmp_path, monkeypatch):
    m = mgr_with_bin
    m.start(variant="standard", cache_dir=str(m.root / "cache"))
    assert m.is_running() is True

    asset = cm.build_asset_names(*cm.detect_platform(), "standard")[0]
    archive = tmp_path / ("dl-" + asset)
    digest = _make_tarball(archive, "codebase-memory-mcp")
    monkeypatch.setattr(
        cm.CbmManager, "_download",
        lambda self, url, dest, progress=None: (
            dest.parent.mkdir(parents=True, exist_ok=True),
            shutil.copy2(archive, dest),
        )[-1],
    )
    monkeypatch.setattr(
        cm.CbmManager, "_fetch_checksums",
        lambda self, tag, mirror: {asset: digest},
    )
    m.install(variant="standard", version="0.11.0")
    # 安装前应已停止旧进程
    assert m.is_running() is False
    assert m.is_installed() is True


# --------------------------------------------------------------------------- #
# 卸载
# --------------------------------------------------------------------------- #
def test_uninstall(mgr_with_bin):
    m = mgr_with_bin
    assert m.is_installed() is True
    res = m.uninstall(purge_data=False)
    assert res["binary"] is True
    assert m.is_installed() is False


def test_uninstall_stops_running_process(mgr_with_bin):
    m = mgr_with_bin
    m.start(variant="standard", cache_dir=str(m.root / "cache"))
    m.uninstall(purge_data=False)
    time.sleep(0.4)
    assert m.is_running() is False
    assert m.is_installed() is False


def test_uninstall_purge_data(mgr_with_bin):
    m = mgr_with_bin
    cache = m.root / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "db.sqlite").write_text("x")
    info = m.installed_info()
    info["cache_dir"] = str(cache)
    m._write_installed_info(**info)
    res = m.uninstall(purge_data=True)
    assert res["data"] is True
    assert not cache.exists()
