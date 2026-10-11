"""astrbot_plugin_cbm —— 在 AstrBot WebUI 中管理 Codebase Memory MCP (CBM)。

功能：
1. 安装 / 卸载 CBM（可选标准版 / UI 版与版本）
2. 运行 / 停止 / 重启 CBM
3. CBM 配置管理
4. CBM 运行状态显示

核心逻辑位于 ``cbm_manager.py``，本文件只负责 AstrBot 集成与 Web API 暴露。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request

PLUGIN_NAME = "astrbot_plugin_cbm"


# --------------------------------------------------------------------------- #
# 载入核心模块（兼容相对导入与按文件路径导入两种方式）
# --------------------------------------------------------------------------- #
def _load_core():
    try:  # 正常情况：作为包的一部分被导入
        from . import cbm_manager as core  # type: ignore

        return core
    except Exception:  # noqa: BLE001 — 退回到按文件路径加载
        import sys

        here = Path(__file__).resolve().parent
        mod_name = "astrbot_plugin_cbm_core"
        if mod_name in sys.modules:
            return sys.modules[mod_name]
        spec = importlib.util.spec_from_file_location(mod_name, here / "cbm_manager.py")
        if spec is None or spec.loader is None:  # pragma: no cover
            raise ImportError("无法加载 cbm_manager.py")
        module = importlib.util.module_from_spec(spec)
        # 必须先注册到 sys.modules，否则 @dataclass 解析 cls.__module__ 时会得到 None。
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(mod_name, None)
            raise
        return module


core = _load_core()
CbmManager = core.CbmManager
CbmError = core.CbmError
DEFAULT_PORT = core.DEFAULT_PORT
DEFAULT_MIRROR = core.DEFAULT_MIRROR
DEFAULT_PROXY_HOST = core.DEFAULT_PROXY_HOST
DEFAULT_PROXY_PORT = core.DEFAULT_PROXY_PORT
BIN_NAME = core.BIN_NAME
VARIANTS = core.VARIANTS


def _data_root() -> Path:
    """返回本插件的数据目录（用于存放 CBM 二进制、状态、日志）。"""
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

        base = Path(get_astrbot_plugin_data_path())
    except Exception:  # noqa: BLE001
        base = Path(__file__).resolve().parent / "data"
    return base / PLUGIN_NAME


class CbmPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self.data_root = _data_root()
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.manager = CbmManager(self.data_root)

        # 安装进度（供前端轮询）
        self._install_job: dict = {"running": False}
        self._install_task: asyncio.Task | None = None

        self._register_web_apis()

        # 若 CBM 已在运行，补挂可视化反向代理（插件热重载场景）
        self._schedule(self._sync_ui_proxy())

        if self._cfg_bool("auto_start", False):
            self._schedule(self._auto_start())

    @staticmethod
    def _schedule(coro) -> None:
        """在事件循环中调度协程；无运行中的循环时（如单元测试同步构造）安全跳过。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return
        loop.create_task(coro)

    # ------------------------------------------------------------------ #
    # 配置便捷读取
    # ------------------------------------------------------------------ #
    def _cfg(self, key: str, default=None):
        try:
            value = self.config.get(key, None)
        except Exception:  # noqa: BLE001
            value = None
        return default if value is None else value

    def _cfg_bool(self, key: str, default: bool = False) -> bool:
        value = self._cfg(key, None)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def _cfg_int(self, key: str, default: int) -> int:
        try:
            return int(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------ #
    # Web API 注册
    # ------------------------------------------------------------------ #
    def _register_web_apis(self) -> None:
        P = f"/{PLUGIN_NAME}"
        routes = [
            (f"{P}/status", self.api_status, ["GET"], "CBM 运行状态"),
            (f"{P}/versions", self.api_versions, ["GET"], "可用 / 已安装版本"),
            (f"{P}/install", self.api_install, ["POST"], "安装 CBM"),
            (f"{P}/install/progress", self.api_install_progress, ["GET"], "安装进度"),
            (f"{P}/uninstall", self.api_uninstall, ["POST"], "卸载 CBM"),
            (f"{P}/start", self.api_start, ["POST"], "启动 CBM"),
            (f"{P}/stop", self.api_stop, ["POST"], "停止 CBM"),
            (f"{P}/restart", self.api_restart, ["POST"], "重启 CBM"),
            (f"{P}/logs", self.api_logs, ["GET"], "读取运行日志"),
            (f"{P}/logs/clear", self.api_logs_clear, ["POST"], "清除运行日志"),
            (f"{P}/mcp", self.api_mcp_config, ["GET"], "获取 MCP 接入配置"),
            (f"{P}/mcp/add", self.api_mcp_add, ["POST"], "写入 AstrBot MCP 配置"),
        ]
        for route, handler, methods, desc in routes:
            self.context.register_web_api(route, handler, methods, desc)

    # ------------------------------------------------------------------ #
    # 辅助
    # ------------------------------------------------------------------ #
    async def _call(self, func, *args, **kwargs):
        """在线程中执行阻塞式管理器方法。"""
        return await asyncio.to_thread(func, *args, **kwargs)

    def _err(self, exc: Exception):
        if isinstance(exc, CbmError):
            return error_response(exc.message, status_code=exc.status_code)
        logger.exception("CBM 操作失败")
        return error_response(f"操作失败: {exc}", status_code=500)

    # ------------------------------------------------------------------ #
    # UI 反向代理
    # ------------------------------------------------------------------ #
    async def _start_proxy_if_ui(self, variant: str, port: int) -> dict:
        """UI 版启动后，把仅监听 127.0.0.1 的可视化面板通过代理对外暴露。"""
        if variant != "ui":
            return {"running": False}
        host = str(self._cfg("ui_proxy_host", DEFAULT_PROXY_HOST) or DEFAULT_PROXY_HOST)
        proxy_port = self._cfg_int("ui_proxy_port", DEFAULT_PROXY_PORT)
        info = await self._call(self.manager.start_proxy, host, proxy_port, int(port))
        if info.get("running"):
            logger.info(
                f"CBM 可视化反代已就绪：{info.get('host')}:{info.get('port')} -> 127.0.0.1:{port}"
            )
        elif info.get("error"):
            logger.warning(f"CBM 可视化反代启动失败：{info.get('error')}")
        return info

    async def _sync_ui_proxy(self) -> None:
        """插件加载时，如果 CBM 已在运行，补挂反向代理。"""
        try:
            status = await self._call(self.manager.status)
            if status.get("running") and status.get("variant") == "ui" and status.get("port"):
                await self._start_proxy_if_ui("ui", int(status["port"]))
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"同步 CBM 反向代理失败: {exc}")

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    async def api_status(self):
        try:
            data = await self._call(self.manager.status)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)
        data["ui_url"] = data.get("ui_url")
        return json_response(data)

    async def api_versions(self):
        result = {
            "installed": self.manager.installed_info(),
            "latest": None,
            "releases": [],
            "variants": list(VARIANTS),
            "default_variant": self._cfg("variant", "ui"),
            "platform": {
                "os": self.manager.os_name,
                "arch": self.manager.arch,
            },
        }
        try:
            result["latest"] = await self._call(self.manager.get_latest_version)
        except Exception as exc:  # noqa: BLE001
            result["latest_error"] = str(getattr(exc, "message", exc))
        try:
            result["releases"] = await self._call(self.manager.list_releases, 30)
        except Exception as exc:  # noqa: BLE001
            result["releases_error"] = str(getattr(exc, "message", exc))
        return json_response(result)

    # ------------------------------------------------------------------ #
    # 安装 / 卸载
    # ------------------------------------------------------------------ #
    async def api_install(self):
        payload = await request.json(default={})
        variant = str(payload.get("variant") or self._cfg("variant", "ui"))
        version = str(payload.get("version") or self._cfg("version", "latest"))
        mirror = str(payload.get("mirror") if payload.get("mirror") is not None else self._cfg("mirror", DEFAULT_MIRROR))

        if variant not in VARIANTS:
            return error_response(f"未知的版本类型: {variant}")
        if self._install_job.get("running"):
            return error_response("已有安装任务正在进行", status_code=409)

        logger.info(
            f"CBM 开始安装：variant={variant} version={version} mirror={mirror or '(直连 GitHub)'}"
        )

        self._install_job = {
            "running": True,
            "phase": "准备中",
            "downloaded": 0,
            "total": 0,
            "message": "",
            "error": None,
            "result": None,
            "started_at": time.time(),
        }

        async def _run():
            loop = asyncio.get_running_loop()

            def progress(done: int, total: int) -> None:
                self._install_job["downloaded"] = done
                self._install_job["total"] = total
                self._install_job["phase"] = "下载中"

            try:
                self._install_job["phase"] = "下载并校验"
                info = await self._call(
                    self.manager.install, variant, version, mirror, progress
                )
                self._install_job["phase"] = "完成"
                self._install_job["result"] = info
                self._install_job["message"] = "安装成功"
            except Exception as exc:  # noqa: BLE001
                self._install_job["error"] = str(getattr(exc, "message", exc))
                self._install_job["phase"] = "失败"
                logger.error(f"CBM 安装失败：{self._install_job['error']}")
            else:
                logger.info("CBM 安装完成")
            finally:
                self._install_job["running"] = False

        self._install_task = asyncio.create_task(_run())
        return json_response({"started": True, "variant": variant, "version": version})

    async def api_install_progress(self):
        job = dict(self._install_job)
        if job.get("total"):
            job["percent"] = round(job["downloaded"] / job["total"] * 100, 1)
        else:
            job["percent"] = 0
        return json_response(job)

    async def api_uninstall(self):
        payload = await request.json(default={})
        purge = bool(payload.get("purge_data", self._cfg_bool("purge_data_on_uninstall", False)))
        try:
            result = await self._call(self.manager.uninstall, purge)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)
        return json_response({"ok": True, "removed": result})

    # ------------------------------------------------------------------ #
    # 运行控制
    # ------------------------------------------------------------------ #
    async def _resolve_run_options(self, payload: dict) -> tuple[str, int, str | None]:
        variant = str(payload.get("variant") or self._cfg("variant", "ui"))
        port = payload.get("port")
        if port is None:
            port = self._cfg_int("port", DEFAULT_PORT)
        cache_dir = payload.get("cache_dir")
        if cache_dir is None:
            cache_dir = self._cfg("cache_dir", "") or None
        return variant, int(port), cache_dir

    async def api_start(self):
        payload = await request.json(default={})
        variant, port, cache_dir = await self._resolve_run_options(payload)
        try:
            data = await self._call(self.manager.start, variant, port, cache_dir)
            proxy = await self._start_proxy_if_ui(variant, port)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)
        logger.info(f"CBM 已启动：variant={variant} port={port} proxy={proxy.get('running')}")
        return json_response({"ok": True, **data, "ui_proxy": proxy})

    async def api_stop(self):
        try:
            await self._call(self.manager.stop_proxy)
            data = await self._call(self.manager.stop)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)
        logger.info("CBM 已停止")
        return json_response({"ok": True, **data})

    async def api_restart(self):
        payload = await request.json(default={})
        variant, port, cache_dir = await self._resolve_run_options(payload)
        try:
            await self._call(self.manager.stop_proxy)
            self.manager.stop()
            data = await self._call(self.manager.start, variant, port, cache_dir)
            proxy = await self._start_proxy_if_ui(variant, port)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)
        logger.info(f"CBM 已重启：variant={variant} port={port}")
        return json_response({"ok": True, **data, "ui_proxy": proxy})

    # ------------------------------------------------------------------ #
    # 日志清理
    # ------------------------------------------------------------------ #
    async def api_logs_clear(self):
        try:
            await self._call(self.manager.clear_logs)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)
        logger.info("CBM 运行日志已清除")
        return json_response({"ok": True})

    # ------------------------------------------------------------------ #
    # MCP 接入 AstrBot
    # ------------------------------------------------------------------ #
    def _astrbot_mcp_config_path(self) -> Path | None:
        """定位 AstrBot 的 MCP 服务配置文件（data/mcp_server.json）。"""
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            return Path(get_astrbot_data_path()) / "mcp_server.json"
        except Exception:  # noqa: BLE001
            return None

    async def api_mcp_config(self):
        try:
            servers = await self._call(self.manager.mcp_server_config)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)
        path = self._astrbot_mcp_config_path()
        snippet = json.dumps({"mcpServers": servers}, ensure_ascii=False, indent=2)
        return json_response(
            {
                "servers": servers,
                "snippet": snippet,
                "config_path": str(path) if path else None,
            }
        )

    async def api_mcp_add(self):
        payload = await request.json(default={})
        name = str(payload.get("name") or BIN_NAME)
        try:
            servers = await self._call(self.manager.mcp_server_config, name)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

        path = self._astrbot_mcp_config_path()
        if path is None:
            return error_response("无法定位 AstrBot 数据目录，请复制配置手动添加。")
        try:
            data: dict = {}
            if path.exists():
                data = json.loads(path.read_text("utf-8") or "{}") or {}
            if not isinstance(data, dict):
                data = {}
            data.setdefault("mcpServers", {})
            data["mcpServers"].update(servers)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=4), "utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"写入 AstrBot MCP 配置失败: {exc}")
            return error_response(f"写入失败：{exc}。请复制配置手动添加。", status_code=500)
        logger.info(f"已将 CBM 写入 AstrBot MCP 配置：{path}")
        return json_response({"ok": True, "path": str(path), "servers": servers})

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #
    async def api_logs(self):
        lines = int(request.query.get("lines", 200, type=int) or 200)
        status = self.manager.status()
        # UI 版运行中：CBM 的 stdout 基本为空，真正的日志在 UI 的 /api/logs。
        if status.get("running") and status.get("variant") == "ui" and status.get("port"):
            try:
                text = await self._call(self.manager.read_ui_logs, int(status["port"]), lines)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"读取 CBM UI 日志失败，回退文件日志：{exc}")
                text = await self._call(self.manager.read_logs, lines)
        else:
            text = await self._call(self.manager.read_logs, lines)
        return json_response({"lines": text})

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    async def _auto_start(self) -> None:
        try:
            if not self.manager.is_installed():
                return
            variant = self._cfg("variant", "ui")
            port = self._cfg_int("port", DEFAULT_PORT)
            cache_dir = self._cfg("cache_dir", "") or None
            if not self.manager.is_running():
                await self._call(self.manager.start, variant, port, cache_dir)
                logger.info("CBM 已随插件自动启动")
            await self._start_proxy_if_ui(variant, port)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"CBM 自动启动失败: {exc}")

    async def terminate(self):
        """插件被卸载 / 停用时调用。"""
        if self._cfg_bool("stop_on_unload", True):
            try:
                await asyncio.to_thread(self.manager.stop_proxy)
                await asyncio.to_thread(self.manager.stop)
                logger.info("CBM 已随插件卸载而停止")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"停止 CBM 失败: {exc}")
