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
KNOWN_CONFIG = core.KNOWN_CONFIG
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

        if self._cfg_bool("auto_start", False):
            asyncio.create_task(self._auto_start())

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
            (f"{P}/config", self.api_config_get, ["GET"], "读取 CBM 配置"),
            (f"{P}/config", self.api_config_set, ["POST"], "修改 CBM 配置"),
            (f"{P}/config/reset", self.api_config_reset, ["POST"], "重置配置项"),
            (f"{P}/logs", self.api_logs, ["GET"], "读取运行日志"),
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
            "variants": list(VARIANTS),
            "platform": {
                "os": self.manager.os_name,
                "arch": self.manager.arch,
            },
        }
        try:
            result["latest"] = await self._call(self.manager.get_latest_version)
        except Exception as exc:  # noqa: BLE001
            result["latest_error"] = str(getattr(exc, "message", exc))
        return json_response(result)

    # ------------------------------------------------------------------ #
    # 安装 / 卸载
    # ------------------------------------------------------------------ #
    async def api_install(self):
        payload = await request.json(default={})
        variant = str(payload.get("variant") or self._cfg("variant", "standard"))
        version = str(payload.get("version") or self._cfg("version", "latest"))
        mirror = str(payload.get("mirror") if payload.get("mirror") is not None else self._cfg("mirror", ""))

        if variant not in VARIANTS:
            return error_response(f"未知的版本类型: {variant}")
        if self._install_job.get("running"):
            return error_response("已有安装任务正在进行", status_code=409)

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
        variant = str(payload.get("variant") or self._cfg("variant", "standard"))
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
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)
        return json_response({"ok": True, **data})

    async def api_stop(self):
        try:
            data = await self._call(self.manager.stop)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)
        return json_response({"ok": True, **data})

    async def api_restart(self):
        payload = await request.json(default={})
        variant, port, cache_dir = await self._resolve_run_options(payload)
        try:
            self.manager.stop()
            data = await self._call(self.manager.start, variant, port, cache_dir)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)
        return json_response({"ok": True, **data})

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #
    async def api_config_get(self):
        cache_dir = self._cfg("cache_dir", "") or None
        try:
            data = await self._call(self.manager.config_list, cache_dir)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)
        return json_response(data)

    async def api_config_set(self):
        payload = await request.json(default={})
        key = payload.get("key")
        if not key:
            return error_response("缺少配置项名称")
        value = payload.get("value")
        cache_dir = self._cfg("cache_dir", "") or None
        try:
            await self._call(self.manager.config_set, str(key), value, cache_dir)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)
        return json_response({"ok": True, "key": key, "value": value})

    async def api_config_reset(self):
        payload = await request.json(default={})
        key = payload.get("key")
        if not key:
            return error_response("缺少配置项名称")
        cache_dir = self._cfg("cache_dir", "") or None
        try:
            await self._call(self.manager.config_reset, str(key), cache_dir)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)
        return json_response({"ok": True, "key": key})

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #
    async def api_logs(self):
        lines = request.query.get("lines", 200, type=int)
        text = await self._call(self.manager.read_logs, int(lines or 200))
        return json_response({"lines": text})

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    async def _auto_start(self) -> None:
        try:
            if self.manager.is_installed() and not self.manager.is_running():
                variant = self._cfg("variant", "standard")
                port = self._cfg_int("port", DEFAULT_PORT)
                cache_dir = self._cfg("cache_dir", "") or None
                await self._call(self.manager.start, variant, port, cache_dir)
                logger.info("CBM 已随插件自动启动")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"CBM 自动启动失败: {exc}")

    async def terminate(self):
        """插件被卸载 / 停用时调用。"""
        if self._cfg_bool("stop_on_unload", True):
            try:
                await asyncio.to_thread(self.manager.stop)
                logger.info("CBM 已随插件卸载而停止")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"停止 CBM 失败: {exc}")
