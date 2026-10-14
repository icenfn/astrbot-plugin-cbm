"""MCP Streamable-HTTP 端点（stdio ⇄ HTTP 桥 + Bearer Token 鉴权）测试。

覆盖：
- McpHttpGateway 的 Bearer Token 鉴权（缺 Token / 错误 Token -> 401，正确 -> 放行）
- 未设置 Token 时一律拒绝
- 通过网关完成 initialize / tools/list / tools/call 的端到端 JSON-RPC 往返
- 网关把 request 的 id 原样返回给客户端
- 错误方法返回 JSON-RPC error
- 路径不匹配 -> 404；GET -> 405
- CbmManager 的 mcp_http 配置持久化与启动校验（空 Token 拒绝、未安装拒绝）
- 端点启停与 info 状态

运行：python -m pytest tests/test_mcp_http.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cbm_manager as cm  # noqa: E402

FAKE_MCP_SRC = Path(__file__).resolve().parent / "fake_mcp_stdio.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _executable_fake(tmp_path: Path) -> str:
    """把假 MCP stdio 脚本复制为可执行文件，供 bridge 直接以二进制方式启动。"""
    dest = tmp_path / "fake_mcp_stdio.py"
    shutil.copy2(FAKE_MCP_SRC, dest)
    os.chmod(dest, 0o755)
    return str(dest)


def _post(port: int, body, token: str | None = None, path: str = "/mcp"):
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


@pytest.fixture()
def gateway(tmp_path):
    """启动一个指向 fake MCP 的网关（含 bridge），测试后清理。"""
    port = _free_port()
    bridge = cm.McpStdioBridge(_executable_fake(tmp_path))
    bridge.start()
    gw = cm.McpHttpGateway("127.0.0.1", port, bridge, "secret-token", cm.MCP_HTTP_PATH)
    gw.start()
    yield gw, port
    gw.stop()
    bridge.stop()


# --------------------------------------------------------------------------- #
# Bearer Token 鉴权
# --------------------------------------------------------------------------- #
def test_missing_token_rejected(gateway):
    _, port = gateway
    status, _ = _post(port, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token=None)
    assert status == 401


def test_wrong_token_rejected(gateway):
    _, port = gateway
    status, _ = _post(port, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token="nope")
    assert status == 401


def test_correct_token_accepted(gateway):
    _, port = gateway
    status, body = _post(port, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token="secret-token")
    assert status == 200
    assert body["result"] == {}


def test_empty_token_all_rejected(tmp_path):
    port = _free_port()
    bridge = cm.McpStdioBridge(_executable_fake(tmp_path))
    bridge.start()
    gw = cm.McpHttpGateway("127.0.0.1", port, bridge, "", cm.MCP_HTTP_PATH)
    gw.start()
    try:
        # 空 Token 时即便携带空 Bearer 也应拒绝。
        assert _post(port, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token="")[0] == 401
        assert _post(port, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token=None)[0] == 401
    finally:
        gw.stop()
        bridge.stop()


# --------------------------------------------------------------------------- #
# 端到端 JSON-RPC 往返（网关 -> bridge -> fake CBM）
# --------------------------------------------------------------------------- #
def test_initialize_and_tools_roundtrip(gateway):
    _, port = gateway
    status, body = _post(
        port,
        {
            "jsonrpc": "2.0",
            "id": "init-1",
            "method": "initialize",
            "params": {"protocolVersion": cm.MCP_PROTOCOL_VERSION, "capabilities": {}},
        },
        token="secret-token",
    )
    assert status == 200
    assert body["id"] == "init-1"
    assert body["result"]["serverInfo"]["name"] == "fake-cbm"

    status, body = _post(
        port,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        token="secret-token",
    )
    assert status == 200
    assert body["id"] == 2
    assert body["result"]["tools"][0]["name"] == "echo"

    status, body = _post(
        port,
        {
            "jsonrpc": "2.0",
            "id": "call-9",
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"text": "hello-cbm"}},
        },
        token="secret-token",
    )
    assert status == 200
    assert body["id"] == "call-9"
    assert body["result"]["content"][0]["text"] == "hello-cbm"


def test_notification_returns_202(gateway):
    _, port = gateway
    status, _ = _post(
        port,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        token="secret-token",
    )
    assert status == 202


def test_unknown_method_returns_error(gateway):
    _, port = gateway
    status, body = _post(
        port,
        {"jsonrpc": "2.0", "id": 7, "method": "no/such/method"},
        token="secret-token",
    )
    assert status == 200
    assert body["id"] == 7
    assert body["error"]["code"] == -32601


def test_bad_path_404_and_get_405(gateway):
    _, port = gateway
    assert _post(port, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token="secret-token", path="/wrong")[0] == 404
    req = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", method="GET")
    req.add_header("Authorization", "Bearer secret-token")
    try:
        urllib.request.urlopen(req, timeout=10)
        assert False, "expected HTTPError"
    except urllib.error.HTTPError as exc:
        assert exc.code == 405


# --------------------------------------------------------------------------- #
# CbmManager 配置持久化与启动校验
# --------------------------------------------------------------------------- #
@pytest.fixture()
def mgr(tmp_path):
    m = cm.CbmManager(tmp_path / "cbm")
    m.root.mkdir(parents=True, exist_ok=True)
    yield m
    try:
        m.stop_mcp_http()
    except Exception:
        pass


def _install_fake_mcp_binary(mgr: cm.CbmManager) -> None:
    mgr.bin_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FAKE_MCP_SRC, mgr.binary_path)
    os.chmod(mgr.binary_path, 0o755)
    mgr._write_installed_info(variant="standard", version="9.9.9")


def test_config_persist_roundtrip(mgr):
    assert mgr.load_mcp_http_config() is None
    saved = mgr.save_mcp_http_config(True, "0.0.0.0", 8765, "tok-123")
    assert saved["enabled"] is True and saved["token"] == "tok-123"
    loaded = mgr.load_mcp_http_config()
    assert loaded["enabled"] is True
    assert loaded["host"] == "0.0.0.0"
    assert loaded["port"] == 8765
    assert loaded["token"] == "tok-123"


def test_start_requires_token(mgr):
    _install_fake_mcp_binary(mgr)
    with pytest.raises(cm.CbmError):
        mgr.start_mcp_http("127.0.0.1", _free_port(), "")


def test_start_requires_installed(tmp_path):
    m = cm.CbmManager(tmp_path / "not-installed")
    m.root.mkdir(parents=True, exist_ok=True)
    with pytest.raises(cm.CbmError):
        m.start_mcp_http("127.0.0.1", _free_port(), "tok")


def test_manager_start_stop_and_info(mgr):
    _install_fake_mcp_binary(mgr)
    port = _free_port()
    info = mgr.start_mcp_http("127.0.0.1", port, "tok-abc")
    assert info["running"] is True
    assert info["port"] == port
    assert info["token_set"] is True
    try:
        status, body = _post(
            port,
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            token="tok-abc",
        )
        assert status == 200
        assert body["result"]["serverInfo"]["name"] == "fake-cbm"
    finally:
        mgr.stop_mcp_http()
    assert mgr.mcp_http_info()["running"] is False


def test_manager_reuses_running_gateway(mgr):
    _install_fake_mcp_binary(mgr)
    port = _free_port()
    first = mgr.start_mcp_http("127.0.0.1", port, "tok-abc")
    second = mgr.start_mcp_http("127.0.0.1", port, "tok-abc")
    try:
        assert first["port"] == second["port"]
        assert mgr.mcp_http_info()["running"] is True
    finally:
        mgr.stop_mcp_http()
