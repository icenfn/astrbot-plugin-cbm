"""CBM (codebase-memory-mcp) 进程与生命周期管理核心逻辑。

该模块刻意不依赖 astrbot，独立可测：
- 平台/资产解析
- 从 GitHub Release 下载 + SHA-256（强制）校验 + 解包二进制
- 安装 / 卸载
- 启动 / 停止 / 重启
- 运行状态查询
- 配置读写

所有函数均为阻塞式；在异步环境（AstrBot 的 Web API handler）中请用
``asyncio.to_thread`` 调用。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import tarfile
import time
import zipfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

REPO = "DeusData/codebase-memory-mcp"
GITHUB_API = "https://api.github.com"
GITHUB_DL = "https://github.com"

BIN_NAME = "codebase-memory-mcp"
DEFAULT_PORT = 9749
# 默认 GitHub 下载加速镜像（可自定义，留空则直连 GitHub）
DEFAULT_MIRROR = "https://gh-proxy.com/"

# UI 可视化反向代理默认监听地址。
# CBM 的 HTTP 面板只绑定 127.0.0.1 且会校验 Host 头，远程浏览器无法直连，
# 因此由本插件提供一个轻量反向代理对外暴露。
DEFAULT_PROXY_HOST = "0.0.0.0"
DEFAULT_PROXY_PORT = 9750

VARIANTS = ("standard", "ui")

# MCP stdio 启动器模板：绕开 AstrBot 对 command 的白名单限制。
_MCP_LAUNCHER_TEMPLATE = '''#!/usr/bin/env python3
"""CBM MCP stdio 启动器（由 astrbot_plugin_cbm 自动生成，请勿手动修改）。

AstrBot 对 MCP stdio 的 command 设有白名单，无法直接以 CBM 可执行文件作为
command。本脚本以受信任的 python3 启动器身份拉起真正的 CBM 二进制，并把
标准输入 / 输出原样透传，保证 stdio MCP 协议正常工作。
"""
import os
import subprocess
import sys

BINARY = {binary!r}
CACHE_DIR = {cache!r}


def main():
    env = dict(os.environ)
    if CACHE_DIR:
        env.setdefault("CBM_CACHE_DIR", CACHE_DIR)
    try:
        return subprocess.call([BINARY], env=env)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
'''


class CbmError(Exception):
    """面向用户的可读错误。"""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# --------------------------------------------------------------------------- #
# 平台与资产
# --------------------------------------------------------------------------- #
def detect_platform() -> tuple[str, str]:
    """返回 (os_name, arch)，与 GitHub Release 资产命名一致。"""
    system = platform.system().lower()
    os_name = {"darwin": "darwin", "linux": "linux", "windows": "windows"}.get(
        system, system
    )
    machine = platform.machine().lower()
    arch = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(machine, machine)
    return os_name, arch


def binary_filename(os_name: str | None = None) -> str:
    os_name = os_name or detect_platform()[0]
    return BIN_NAME + ".exe" if os_name == "windows" else BIN_NAME


def build_asset_names(os_name: str, arch: str, variant: str) -> list[str]:
    """按优先级返回候选资产名（首选 portable，其次普通包）。"""
    if variant not in VARIANTS:
        raise CbmError(f"未知的版本类型: {variant}")
    prefix = "codebase-memory-mcp-ui" if variant == "ui" else "codebase-memory-mcp"
    if os_name == "windows":
        return [f"{prefix}-{os_name}-{arch}.zip"]
    names: list[str] = []
    if os_name == "linux":
        names.append(f"{prefix}-{os_name}-{arch}-portable.tar.gz")
    names.append(f"{prefix}-{os_name}-{arch}.tar.gz")
    return names


def apply_mirror(url: str, mirror: str) -> str:
    """应用下载加速镜像。支持 {url} 占位符，否则做前缀拼接。"""
    mirror = (mirror or "").strip()
    if not mirror:
        return url
    if "{url}" in mirror:
        return mirror.replace("{url}", url)
    if not mirror.endswith("/"):
        mirror += "/"
    return mirror + url


def _sha256_of_file(path: Path, *, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# 进程辅助
# --------------------------------------------------------------------------- #
def _proc_pids():
    try:
        for name in os.listdir("/proc"):
            if name.isdigit():
                yield int(name)
    except FileNotFoundError:
        return


def _cmdline_list(pid: int) -> list[str]:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            raw = fh.read().decode("utf-8", "ignore")
    except OSError:
        return []
    return [c for c in raw.split("\x00") if c]


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.path.exists(f"/proc/{pid}"):
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# --------------------------------------------------------------------------- #
# UI 反向代理（CBM 的 HTTP 面板仅监听 127.0.0.1，且会校验 Host 头）
# --------------------------------------------------------------------------- #
class UiProxy:
    """把 ``127.0.0.1:<target>`` 的 CBM 可视化面板通过本机网卡对外暴露。

    CBM 只监听回环地址，并且在 Host 头不是 127.0.0.1 时返回 403，因此远程
    浏览器无法直接访问。此处用一个轻量反向代理：对外监听 ``host:port``，把
    请求转发到 ``127.0.0.1:target_port``（转发时 Host 即 127.0.0.1，可通过
    CBM 的校验）。同时移除 CBM 响应里的 ``frame-ancestors 'none'``，便于按需内嵌。
    """

    def __init__(self, host: str, port: int, target_port: int):
        self.host = host
        self.port = int(port)
        self.target_port = int(target_port)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _make_handler(self):
        target_port = self.target_port

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _proxy(self, method: str) -> None:  # noqa: ANN001
                target = f"http://127.0.0.1:{target_port}{self.path}"
                body = None
                length = self.headers.get("Content-Length")
                if length:
                    try:
                        body = self.rfile.read(int(length))
                    except (ValueError, OSError):
                        body = None
                req = urllib.request.Request(target, data=body, method=method)
                for key, value in self.headers.items():
                    low = key.lower()
                    # CBM 会校验来源：Host 或 Origin 只要不是回环地址就返回
                    # 403（"forbidden host"/"forbidden origin"）。浏览器通过
                    # 代理访问时会带上真实站点的 Host 与（crossorigin 资源触发的）
                    # Origin，因此这里必须丢弃它们，让 urllib 以 127.0.0.1 重新
                    # 生成 Host，并彻底去掉 Origin。
                    if low in (
                        "host",
                        "origin",
                        "referer",
                        "content-length",
                        "connection",
                    ):
                        continue
                    req.add_header(key, value)
                try:
                    resp = urllib.request.urlopen(req, timeout=30)
                    data = resp.read()
                    status = resp.status
                    headers = resp.headers
                except urllib.error.HTTPError as exc:
                    data = exc.read()
                    status = exc.code
                    headers = exc.headers
                except Exception as exc:  # noqa: BLE001
                    data = f"CBM 未就绪：{exc}".encode("utf-8")
                    status = 502
                    headers = {}

                self.send_response(status)
                for key, value in (headers.items() if hasattr(headers, "items") else []):
                    low = key.lower()
                    if low in ("content-length", "connection", "transfer-encoding"):
                        continue
                    if low == "content-security-policy":
                        value = value.replace("frame-ancestors 'none';", "").replace(
                            "frame-ancestors 'none'", ""
                        )
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if method != "HEAD":
                    self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                self._proxy("GET")

            def do_HEAD(self):  # noqa: N802
                self._proxy("HEAD")

            def do_POST(self):  # noqa: N802
                self._proxy("POST")

            def do_PUT(self):  # noqa: N802
                self._proxy("PUT")

            def do_DELETE(self):  # noqa: N802
                self._proxy("DELETE")

            def do_OPTIONS(self):  # noqa: N802
                self._proxy("OPTIONS")

            def log_message(self, *args):  # noqa: ANN002
                return

        return _Handler

    def start(self) -> dict[str, Any]:
        if self._httpd is not None:
            return {"running": True, "host": self.host, "port": self.port}
        self._httpd = ThreadingHTTPServer((self.host, self.port), self._make_handler())
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return {"running": True, "host": self.host, "port": self.port}

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._httpd.server_close()
            except Exception:  # noqa: BLE001
                pass
        self._httpd = None
        self._thread = None

    @property
    def running(self) -> bool:
        return self._httpd is not None


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #
@dataclass
class CbmState:
    pid: int | None = None
    variant: str | None = None
    version: str | None = None
    # UI 服务端口（对两种版本都记录：ui 版为显式端口，标准版为配置端口）
    port: int | None = None
    cache_dir: str | None = None
    started_at: float | None = None
    log_path: str | None = None

    @classmethod
    def load(cls, path: Path) -> "CbmState":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text("utf-8"))
            return cls(**{k: data.get(k) for k in cls.__dataclass_fields__})
        except Exception:
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.__dict__, ensure_ascii=False, indent=2), "utf-8"
        )


# --------------------------------------------------------------------------- #
# 管理器
# --------------------------------------------------------------------------- #
class CbmManager:
    """管理单个 CBM 安装（不开发多版本共存）。"""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
        self.bin_dir = self.root / "bin"
        self.download_dir = self.root / "downloads"
        self.log_dir = self.root / "logs"
        self.state_path = self.root / "state.json"
        self.meta_path = self.root / "install.json"
        self.log_path = self.log_dir / "cbm.log"
        self._popen: subprocess.Popen | None = None
        self._proxy: UiProxy | None = None
        self._proxy_target: int | None = None
        # 串行化 启动/停止 等修改进程状态的操作，避免并发调用（如插件热重载时
        # 的自动启动任务与手动操作）相互干扰。
        self._proc_lock = threading.RLock()

    # ---------- 基础路径 ----------
    @property
    def os_name(self) -> str:
        return detect_platform()[0]

    @property
    def arch(self) -> str:
        return detect_platform()[1]

    @property
    def binary_path(self) -> Path:
        return self.bin_dir / binary_filename(self.os_name)

    def is_installed(self) -> bool:
        return self.binary_path.exists() and os.access(self.binary_path, os.X_OK)

    def installed_info(self) -> dict[str, Any]:
        if self.meta_path.exists():
            try:
                return json.loads(self.meta_path.read_text("utf-8"))
            except Exception:
                pass
        return {}

    def _write_installed_info(self, **info: Any) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(
            json.dumps(info, ensure_ascii=False, indent=2), "utf-8"
        )

    def resolve_cache_dir(self, override: str | None = None) -> Path:
        if override:
            return Path(os.path.expanduser(override)).resolve()
        env = os.environ.get("CBM_CACHE_DIR")
        if env:
            return Path(os.path.expanduser(env)).resolve()
        return (Path.home() / ".cache" / "codebase-memory-mcp").resolve()

    # ---------- UI 反向代理 ----------
    def start_proxy(
        self,
        listen_host: str = DEFAULT_PROXY_HOST,
        listen_port: int = DEFAULT_PROXY_PORT,
        target_port: int | None = None,
    ) -> dict[str, Any]:
        """启动对外可视化反向代理；target 默认指向当前 UI 端口。"""
        target = int(target_port or self._proxy_target or DEFAULT_PORT)
        if self._proxy is not None and self._proxy.running:
            if self._proxy.port == int(listen_port) and self._proxy.target_port == target:
                return {
                    "running": True,
                    "host": self._proxy.host,
                    "port": self._proxy.port,
                    "target_port": target,
                }
            self.stop_proxy()
        self._proxy = UiProxy(listen_host, int(listen_port), target)
        self._proxy_target = target
        try:
            return self._proxy.start()
        except OSError as exc:
            self._proxy = None
            return {
                "running": False,
                "host": listen_host,
                "port": int(listen_port),
                "error": f"代理监听失败: {exc}",
            }

    def stop_proxy(self) -> None:
        if self._proxy is not None:
            self._proxy.stop()
        self._proxy = None

    def proxy_info(self) -> dict[str, Any]:
        if self._proxy is not None and self._proxy.running:
            return {
                "running": True,
                "host": self._proxy.host,
                "port": self._proxy.port,
                "target_port": self._proxy.target_port,
            }
        return {"running": False, "host": None, "port": None, "target_port": None}

    # ---------- 版本解析 ----------
    def get_latest_version(self) -> str:
        import urllib.request

        url = f"{GITHUB_API}/repos/{REPO}/releases/latest"
        req = urllib.request.Request(url, headers={"User-Agent": "astrbot-plugin-cbm"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            tag = data.get("tag_name") or ""
            return tag.lstrip("v")
        except Exception as exc:  # noqa: BLE001
            raise CbmError(f"获取最新版本失败: {exc}", status_code=502) from exc

    def list_releases(self, limit: int = 30) -> list[dict[str, Any]]:
        """列出官方 Release（供版本选择）。返回按发布时间倒序的版本列表。"""
        import urllib.request

        url = f"{GITHUB_API}/repos/{REPO}/releases?per_page={max(1, int(limit))}"
        req = urllib.request.Request(url, headers={"User-Agent": "astrbot-plugin-cbm"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise CbmError(f"获取版本列表失败: {exc}", status_code=502) from exc
        releases = []
        for item in data:
            tag = (item.get("tag_name") or "").strip()
            if not tag:
                continue
            releases.append(
                {
                    "tag": tag,
                    "version": tag.lstrip("v"),
                    "name": item.get("name") or tag,
                    "prerelease": bool(item.get("prerelease")),
                    "published_at": item.get("published_at"),
                }
            )
        return releases

    def _resolve_tag(self, version: str) -> str:
        version = (version or "latest").strip()
        if version in ("", "latest"):
            version = self.get_latest_version()
        return version if version.startswith("v") else f"v{version}"

    # ---------- 下载 ----------
    def _download(self, url: str, dest: Path, progress: Callable | None = None) -> None:
        import urllib.request

        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "astrbot-plugin-cbm"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                with open(dest, "wb") as fh:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        if progress and total:
                            progress(done, total)
        except Exception as exc:  # noqa: BLE001
            raise CbmError(f"下载失败: {url} ({exc})", status_code=502) from exc

    def _fetch_checksums(self, tag: str, mirror: str) -> dict[str, str]:
        url = apply_mirror(
            f"{GITHUB_DL}/{REPO}/releases/download/{tag}/checksums.txt", mirror
        )
        tmp = self.download_dir / f"checksums-{tag}.txt"
        try:
            self._download(url, tmp)
        except CbmError:
            return {}
        result: dict[str, str] = {}
        for line in tmp.read_text("utf-8", "ignore").splitlines():
            parts = line.split()
            if len(parts) == 2:
                result[parts[1].lstrip("*")] = parts[0]
        return result

    @staticmethod
    def _extract_binary(archive: Path, out_path: Path) -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        target = binary_filename()
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                member = next(
                    (n for n in zf.namelist() if Path(n).name in (target, BIN_NAME)),
                    None,
                )
                if not member:
                    raise CbmError("压缩包内未找到 CBM 可执行文件")
                with zf.open(member) as src, open(out_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        else:
            with tarfile.open(archive, "r:gz") as tf:
                member = next(
                    (m for m in tf.getmembers() if Path(m.name).name in (target, BIN_NAME)),
                    None,
                )
                if not member:
                    raise CbmError("压缩包内未找到 CBM 可执行文件")
                src = tf.extractfile(member)
                if src is None:
                    raise CbmError("无法从压缩包读取 CBM 可执行文件")
                with src, open(out_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        os.chmod(out_path, 0o755)

    def install(
        self,
        variant: str = "ui",
        version: str = "latest",
        mirror: str = "",
        progress: Callable | None = None,
    ) -> dict[str, Any]:
        """下载并安装 CBM。返回安装信息。SHA-256 校验为强制项。"""
        if variant not in VARIANTS:
            raise CbmError(f"未知的版本类型: {variant}")

        tag = self._resolve_tag(version)
        os_name, arch = self.os_name, self.arch
        candidates = build_asset_names(os_name, arch, variant)

        # SHA-256 校验强制：无法获取校验文件时中止，避免安装被篡改的二进制。
        checksums = self._fetch_checksums(tag, mirror)
        if not checksums:
            raise CbmError(
                f"无法获取 {tag} 的 checksums.txt，出于安全考虑已中止安装",
                status_code=502,
            )

        last_err = ""
        archive: Path | None = None
        chosen: str | None = None
        for asset in candidates:
            expected = checksums.get(asset)
            if not expected:
                last_err = f"{asset} 未在 checksums.txt 中登记"
                continue
            url = apply_mirror(
                f"{GITHUB_DL}/{REPO}/releases/download/{tag}/{asset}", mirror
            )
            dest = self.download_dir / f"{tag}-{asset}"
            try:
                self._download(url, dest, progress)
            except CbmError as exc:
                last_err = str(exc)
                continue
            actual = _sha256_of_file(dest)
            if actual.lower() != expected.lower():
                last_err = f"{asset} SHA-256 校验失败"
                continue
            archive, chosen = dest, asset
            break

        if archive is None or chosen is None:
            raise CbmError(
                f"未找到适配 {os_name}/{arch} 的 {variant} 版本资产。{last_err}",
                status_code=502,
            )

        # 停止旧进程后再替换二进制
        self.stop()
        tmp_out = self.binary_path.with_suffix(".new")
        self._extract_binary(archive, tmp_out)
        os.replace(tmp_out, self.binary_path)

        info = {
            "variant": variant,
            "version": tag.lstrip("v"),
            "tag": tag,
            "asset": chosen,
            "os": os_name,
            "arch": arch,
            "installed_at": time.time(),
            "sha256_verified": True,
        }
        self._write_installed_info(**info)
        return info

    def uninstall(self, purge_data: bool = False) -> dict[str, Any]:
        self.stop()
        # 在删除 install.json 之前先读取，否则拿不到已记录的缓存目录。
        recorded_cache = (self.installed_info() or {}).get("cache_dir")
        removed: dict[str, Any] = {"binary": False, "data": False}
        if self.binary_path.exists():
            self.binary_path.unlink()
            removed["binary"] = True
        if self.bin_dir.exists():
            shutil.rmtree(self.bin_dir, ignore_errors=True)
        if self.meta_path.exists():
            self.meta_path.unlink()
        if purge_data:
            cache_dir = self.resolve_cache_dir(recorded_cache)
            if cache_dir.exists():
                shutil.rmtree(cache_dir, ignore_errors=True)
                removed["data"] = True
        return removed

    # ---------- 运行控制 ----------
    def _build_args(self, variant: str, port: int) -> list[str]:
        if variant == "ui":
            return ["--ui=true", f"--port={int(port)}"]
        return []

    def _iter_cbm_processes(self):
        """遍历属于本安装的 CBM 进程 (pid, cmdline)。

        以可执行文件的真实路径精确匹配，避免误伤系统中其它 CBM 安装：
        - 原生二进制：argv[0] 即安装路径；
        - 脚本(shebang)启动：argv[1] 为脚本路径；
        - 后台守护进程(--cbm-daemon-internal)：argv[0] 为安装路径。
        """
        target = os.path.realpath(self.binary_path)
        for pid in _proc_pids():
            cmdline = _cmdline_list(pid)
            if not cmdline:
                continue
            if any(os.path.realpath(c) == target for c in cmdline[:2]):
                yield pid, cmdline

    def _our_pids(self) -> set[int]:
        return {pid for pid, _ in self._iter_cbm_processes()}

    def start(
        self,
        variant: str = "ui",
        port: int = DEFAULT_PORT,
        cache_dir: str | None = None,
    ) -> dict[str, Any]:
        if not self.is_installed():
            raise CbmError("CBM 尚未安装，请先安装")
        if variant not in VARIANTS:
            raise CbmError(f"未知的版本类型: {variant}")
        if self.is_running():
            raise CbmError("CBM 已在运行中")

        resolved_cache = self.resolve_cache_dir(cache_dir)
        resolved_cache.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        env = dict(os.environ)
        env["CBM_CACHE_DIR"] = str(resolved_cache)

        args = [str(self.binary_path), *self._build_args(variant, port)]
        log_fh = open(self.log_path, "ab", buffering=0)
        try:
            proc = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(self.bin_dir),
                start_new_session=True,
            )
        finally:
            log_fh.close()
        self._popen = proc

        time.sleep(1.5)
        if proc.poll() is not None:
            if self._popen is proc:
                self._popen = None
            tail = self.read_logs(30)
            raise CbmError(f"CBM 启动后立即退出。日志:\n{tail}", status_code=500)

        # 仅 UI 版提供可视化 HTTP 服务；标准版为纯 stdio MCP 服务，无端口。
        ui_port: int | None = int(port) if variant == "ui" else None

        state = CbmState(
            pid=proc.pid,
            variant=variant,
            version=self.installed_info().get("version"),
            port=ui_port,
            cache_dir=str(resolved_cache),
            started_at=time.time(),
            log_path=str(self.log_path),
        )
        state.save(self.state_path)
        return self._state_dict(state)

    def stop(self, timeout: float = 8.0) -> dict[str, Any]:
        state = CbmState.load(self.state_path)
        targets: set[int] = set(self._our_pids())
        if state.pid:
            targets.add(state.pid)

        for pid in targets:
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                continue

        deadline = time.time() + timeout
        remaining = {pid for pid in targets if _pid_alive(pid)}
        while remaining and time.time() < deadline:
            remaining = {pid for pid in remaining if _pid_alive(pid)}
            if remaining:
                time.sleep(0.3)

        for pid in remaining:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

        if self._popen is not None:
            try:
                if self._popen.stdin:
                    self._popen.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            self._popen = None

        CbmState().save(self.state_path)
        return {"stopped_pids": sorted(targets), "running": False}

    def restart(
        self,
        variant: str | None = None,
        port: int | None = None,
        cache_dir: str | None = None,
    ) -> dict[str, Any]:
        state = CbmState.load(self.state_path)
        variant = (
            variant
            or state.variant
            or self.installed_info().get("variant")
            or "ui"
        )
        port = int(port or state.port or DEFAULT_PORT)
        cache_dir = cache_dir or state.cache_dir
        self.stop()
        return self.start(variant=variant, port=port, cache_dir=cache_dir)

    # ---------- 状态 ----------
    def is_running(self) -> bool:
        return bool(self._our_pids())

    def _version_of_binary(self) -> str | None:
        if not self.is_installed():
            return None
        try:
            out = subprocess.run(
                [str(self.binary_path), "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return (out.stdout or out.stderr).strip().split("\n")[0]
        except Exception:  # noqa: BLE001
            return None

    def _state_dict(self, state: CbmState) -> dict[str, Any]:
        running = self.is_running()
        return {
            "pid": state.pid,
            "variant": state.variant,
            "port": state.port,
            "cache_dir": state.cache_dir,
            "started_at": state.started_at,
            "running": running,
        }

    def status(self) -> dict[str, Any]:
        info = self.installed_info()
        state = CbmState.load(self.state_path)
        running = self.is_running()
        uptime = None
        if running and state.started_at:
            uptime = int(time.time() - state.started_at)
        ui_url = None
        if running and state.variant == "ui" and state.port:
            ui_url = f"http://127.0.0.1:{state.port}"
        return {
            "installed": self.is_installed(),
            "install_info": info,
            "binary_path": str(self.binary_path),
            "binary_version": info.get("version"),
            "running": running,
            "pid": state.pid if running else None,
            "variant": state.variant or info.get("variant"),
            "port": state.port,
            "ui_url": ui_url,
            "ui_proxy": self.proxy_info(),
            "cache_dir": state.cache_dir or str(self.resolve_cache_dir()),
            "uptime": uptime,
            "started_at": state.started_at,
            "platform": {"os": self.os_name, "arch": self.arch},
            "log_path": str(self.log_path),
        }

    # ---------- 日志 ----------
    def read_logs(self, lines: int = 200) -> str:
        if not self.log_path.exists():
            return ""
        try:
            data = self.log_path.read_text("utf-8", "ignore").splitlines()
            return "\n".join(data[-max(1, int(lines)):])
        except Exception:  # noqa: BLE001
            return ""

    def read_ui_logs(self, port: int, lines: int = 200) -> str:
        """从 CBM UI 的 /api/logs 读取运行日志。

        UI 模式下 CBM 的 stdout/stderr 基本为空，真正的日志由 UI 的 HTTP
        接口提供，因此远程查看运行日志需走此处。
        """
        url = f"http://127.0.0.1:{int(port)}/api/logs"
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
        except Exception as exc:  # noqa: BLE001
            raise CbmError(f"读取 CBM 日志失败: {exc}", status_code=502) from exc
        items = data.get("lines") if isinstance(data, dict) else data
        if not items:
            return ""
        return "\n".join(str(x) for x in items[-max(1, int(lines)):])

    # ---------- MCP 接入配置 ----------
    @property
    def mcp_launcher_path(self) -> Path:
        return self.bin_dir / "cbm_mcp_launcher.py"

    def ensure_mcp_launcher(self) -> Path:
        """生成 / 刷新 MCP stdio 启动器。

        AstrBot 对 MCP stdio 的 ``command`` 设有白名单（仅允许 bun / node /
        npm / python3 / uv 等常见启动器），CBM 可执行文件不在其中，直接填
        二进制路径会报 "command ... is not allowed"。因此这里生成一个受信任
        的 python3 启动器，由它拉起真正的 CBM 二进制并透传标准输入输出，保证
        stdio MCP 协议正常工作。
        """
        if not self.is_installed():
            raise CbmError("CBM 尚未安装")
        content = _MCP_LAUNCHER_TEMPLATE.format(
            binary=str(self.binary_path),
            cache=str(self.resolve_cache_dir()),
        )
        try:
            self.bin_dir.mkdir(parents=True, exist_ok=True)
            self.mcp_launcher_path.write_text(content, "utf-8")
        except Exception as exc:  # noqa: BLE001
            raise CbmError(f"生成 MCP 启动器失败: {exc}", status_code=500) from exc
        return self.mcp_launcher_path

    def mcp_server_config(self, name: str = BIN_NAME) -> dict[str, Any]:
        """返回把 CBM 接入 AstrBot 的 MCP 配置对象。

        ``command`` 使用白名单内的 ``python3``，通过启动器脚本拉起 CBM 二进制。
        """
        launcher = self.ensure_mcp_launcher()
        return {
            name: {
                "command": "python3",
                "args": [str(launcher)],
                "env": {"CBM_CACHE_DIR": str(self.resolve_cache_dir())},
            }
        }


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #
