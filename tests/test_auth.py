"""WebUI 访问鉴权（HTTP Basic Auth）与鉴权配置持久化测试。

运行：python -m pytest tests/test_auth.py -v
"""

from __future__ import annotations

import base64
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cbm_manager as cm  # noqa: E402


def _b64(user: str, pwd: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------------------- #
# UiProxy.auth_header
# --------------------------------------------------------------------------- #
def test_auth_header_disabled_is_empty():
    p = cm.UiProxy("127.0.0.1", 1, 2, auth_enabled=False, auth_username="a", auth_password="b")
    assert p.auth_header == ""


def test_auth_header_enabled_encodes_basic():
    p = cm.UiProxy("127.0.0.1", 1, 2, auth_enabled=True, auth_username="admin", auth_password="secret")
    assert p.auth_header == _b64("admin", "secret")


# --------------------------------------------------------------------------- #
# UiProxy 真实监听 + 鉴权
# --------------------------------------------------------------------------- #
def _start_proxy(auth_enabled: bool, username: str = "", password: str = "") -> tuple[cm.UiProxy, int]:
    port = _free_port()
    proxy = cm.UiProxy(
        "127.0.0.1",
        port,
        1,  # 目标端口不存在也无妨：未授权请求会在转发前被拦截
        auth_enabled=auth_enabled,
        auth_username=username,
        auth_password=password,
    )
    proxy.start()
    time.sleep(0.2)
    return proxy, port


def test_proxy_no_auth_allows_request():
    proxy, port = _start_proxy(False)
    try:
        # 无鉴权时不会返回 401（目标不可达会返回 502，即已被放行到转发阶段）
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
            status = 200
        except urllib.error.HTTPError as exc:
            status = exc.code
        assert status != 401
    finally:
        proxy.stop()


def test_proxy_requires_auth_and_rejects_missing_credentials():
    proxy, port = _start_proxy(True, "admin", "pw")
    try:
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
        assert ei.value.code == 401
        assert "Basic" in (ei.value.headers.get("WWW-Authenticate") or "")
        ei.value.read()
    finally:
        proxy.stop()


def test_proxy_rejects_wrong_credentials():
    proxy, port = _start_proxy(True, "admin", "pw")
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/")
        req.add_header("Authorization", _b64("admin", "wrong"))
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req, timeout=5)
        assert ei.value.code == 401
        ei.value.read()
    finally:
        proxy.stop()


def test_proxy_accepts_correct_credentials():
    proxy, port = _start_proxy(True, "admin", "pw")
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/")
        req.add_header("Authorization", _b64("admin", "pw"))
        # 通过鉴权后进入转发阶段；目标端口不可达 -> 502，而非 401
        try:
            urllib.request.urlopen(req, timeout=5)
            status = 200
        except urllib.error.HTTPError as exc:
            status = exc.code
        assert status != 401
    finally:
        proxy.stop()


# --------------------------------------------------------------------------- #
# CbmManager 鉴权配置持久化
# --------------------------------------------------------------------------- #
def test_load_auth_config_absent_returns_none(tmp_path):
    m = cm.CbmManager(tmp_path / "cbm")
    assert m.load_auth_config() is None


def test_auth_config_roundtrip(tmp_path):
    m = cm.CbmManager(tmp_path / "cbm")
    m.save_auth_config(True, "admin", "s3cret")
    loaded = m.load_auth_config()
    assert loaded == {"enabled": True, "username": "admin", "password": "s3cret"}


def test_start_proxy_carries_auth_flags(tmp_path):
    m = cm.CbmManager(tmp_path / "cbm")
    m.root.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    info = m.start_proxy(
        "127.0.0.1", port, 1,
        auth={"enabled": True, "username": "u", "password": "p"},
    )
    try:
        assert info.get("running") is True
        assert info.get("auth_enabled") is True
        assert m.proxy_info().get("auth_enabled") is True
    finally:
        m.stop_proxy()
