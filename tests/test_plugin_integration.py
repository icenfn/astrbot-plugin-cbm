"""AstrBot 集成冒烟测试：验证 main.py 的 Web API 接线是否正确。

通过注入一个极简的 astrbot 桩模块，验证：
- 插件能被导入（Star + Context 接线）
- 所有 Web API 路由都按 /{plugin_name} 前缀注册
- 各 handler 可被调用并返回合法响应对象
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN_NAME = "astrbot_plugin_cbm"

EXPECTED_ROUTES = {
    f"/{PLUGIN_NAME}/status",
    f"/{PLUGIN_NAME}/versions",
    f"/{PLUGIN_NAME}/install",
    f"/{PLUGIN_NAME}/install/progress",
    f"/{PLUGIN_NAME}/uninstall",
    f"/{PLUGIN_NAME}/start",
    f"/{PLUGIN_NAME}/stop",
    f"/{PLUGIN_NAME}/restart",
    f"/{PLUGIN_NAME}/logs",
    f"/{PLUGIN_NAME}/logs/clear",
    f"/{PLUGIN_NAME}/mcp",
    f"/{PLUGIN_NAME}/mcp/add",
}


def _install_astrbot_shim():
    if getattr(sys.modules.get("astrbot"), "_cbm_shim", False):
        return sys.modules["astrbot.api.web"]

    logger = logging.getLogger("astrbot")

    web = types.ModuleType("astrbot.api.web")
    responses: list = []

    def json_response(data=None, **kw):
        r = {"kind": "json", "data": data}
        responses.append(r)
        return r

    def error_response(message="", status_code=400, **kw):
        r = {"kind": "error", "message": message, "status_code": status_code}
        responses.append(r)
        return r

    class _Query:
        def get(self, key, default=None, type=None):
            return default

        def getlist(self, key):
            return []

    class _Request:
        def __init__(self):
            self.payload = {}
            self.query = _Query()
            self.username = "tester"

        async def json(self, default=None):
            return self.payload

    web.json_response = json_response
    web.error_response = error_response
    web.request = _Request()
    web.RESPONSES = responses

    star = types.ModuleType("astrbot.api.star")

    class Star:
        def __init__(self, context=None):
            self.context = context

        async def terminate(self):  # pragma: no cover
            pass

    class Context:
        def __init__(self):
            self.routes = []

        def register_web_api(self, route, handler, methods, desc):
            self.routes.append((route, handler, tuple(methods), desc))

    star.Star = Star
    star.Context = Context

    ab = types.ModuleType("astrbot")
    ab.logger = logger
    ab._cbm_shim = True
    api = types.ModuleType("astrbot.api")
    api.logger = logger
    api.star = star
    api.web = web
    ab.api = api

    core = types.ModuleType("astrbot.core")
    utils = types.ModuleType("astrbot.core.utils")
    ap = types.ModuleType("astrbot.core.utils.astrbot_path")

    def get_astrbot_plugin_data_path():
        return str(REPO / "_test_data")

    ap.get_astrbot_plugin_data_path = get_astrbot_plugin_data_path
    utils.astrbot_path = ap
    core.utils = utils
    ab.core = core

    for name, mod in [
        ("astrbot", ab),
        ("astrbot.api", api),
        ("astrbot.api.star", star),
        ("astrbot.api.web", web),
        ("astrbot.core", core),
        ("astrbot.core.utils", utils),
        ("astrbot.core.utils.astrbot_path", ap),
    ]:
        sys.modules[name] = mod
    return web


def _load_main():
    if "cbm_plugin_main" in sys.modules:
        return sys.modules["cbm_plugin_main"]
    spec = importlib.util.spec_from_file_location("cbm_plugin_main", REPO / "main.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cbm_plugin_main"] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_plugin():
    web = _install_astrbot_shim()
    main = _load_main()
    ctx = sys.modules["astrbot.api.star"].Context()
    plugin = main.CbmPlugin(ctx, config={})
    return plugin, ctx, web


def test_routes_registered():
    _, ctx, _ = _make_plugin()
    routes = {r[0] for r in ctx.routes}
    assert EXPECTED_ROUTES.issubset(routes), EXPECTED_ROUTES - routes
    # 所有路由都必须带插件名前缀
    assert all(r.startswith(f"/{PLUGIN_NAME}/") for r in routes)


def test_status_handler_returns_json():
    plugin, _, _ = _make_plugin()
    result = asyncio.run(plugin.api_status())
    assert result["kind"] == "json"
    assert result["data"]["installed"] is False
    assert result["data"]["running"] is False


def test_versions_handler():
    plugin, _, _ = _make_plugin()
    plugin.manager.get_latest_version = lambda: "0.11.0"
    plugin.manager.list_releases = lambda limit=30: [
        {"tag": "v0.11.0", "version": "0.11.0", "name": "v0.11.0", "prerelease": False},
        {"tag": "v0.10.8", "version": "0.10.8", "name": "v0.10.8", "prerelease": False},
    ]
    result = asyncio.run(plugin.api_versions())
    assert result["kind"] == "json"
    assert result["data"]["latest"] == "0.11.0"
    assert set(result["data"]["variants"]) == {"standard", "ui"}
    assert result["data"]["default_variant"] == "ui"
    assert len(result["data"]["releases"]) == 2
    assert result["data"]["releases"][0]["version"] == "0.11.0"


def test_default_variant_ui_in_plugin():
    plugin, _, _ = _make_plugin()
    assert plugin._cfg("variant", "ui") == "ui"


def test_install_invalid_variant_errors():
    plugin, _, web = _make_plugin()
    web.request.payload = {"variant": "pro"}
    result = asyncio.run(plugin.api_install())
    assert result["kind"] == "error"


def test_start_when_not_installed_errors():
    plugin, _, web = _make_plugin()
    web.request.payload = {}
    result = asyncio.run(plugin.api_start())
    assert result["kind"] == "error"


def test_logs_clear_handler_returns_json():
    plugin, _, _ = _make_plugin()
    result = asyncio.run(plugin.api_logs_clear())
    assert result["kind"] == "json"
    assert result["data"]["ok"] is True


def test_mcp_config_when_not_installed_errors():
    plugin, _, _ = _make_plugin()
    result = asyncio.run(plugin.api_mcp_config())
    assert result["kind"] == "error"


def test_logs_handler_returns_json():
    plugin, _, _ = _make_plugin()
    result = asyncio.run(plugin.api_logs())
    assert result["kind"] == "json"
    assert "lines" in result["data"]


def test_install_progress_handler():
    plugin, _, _ = _make_plugin()
    result = asyncio.run(plugin.api_install_progress())
    assert result["kind"] == "json"
    assert result["data"]["running"] is False
