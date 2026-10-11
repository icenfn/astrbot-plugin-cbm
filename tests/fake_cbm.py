#!/usr/bin/env python3
"""一个用于测试的假 CBM 可执行文件（mimic codebase-memory-mcp）。

支持：
- --version         -> 打印版本
- config list/set/reset
- 无参数运行         -> 常驻进程（读取 stdin，保持存活）

仅用于单元测试，避免在测试中依赖 286MB 的真实二进制。
"""
import json
import os
import sys

CACHE = os.environ.get("CBM_CACHE_DIR") or os.path.expanduser("~/.cache/codebase-memory-mcp")
CFG = os.path.join(CACHE, "config.json")
DEFAULTS = {
    "auto_index": False,
    "auto_index_limit": 50000,
    "auto_watch": True,
    "watcher_enabled": True,
    "ui-lang": "auto",
    "ui_enabled": True,
    "ui_port": 9749,
}


def _load():
    try:
        with open(CFG, "r", encoding="utf-8") as fh:
            return {**DEFAULTS, **json.load(fh)}
    except Exception:
        return dict(DEFAULTS)


def _save(data):
    os.makedirs(CACHE, exist_ok=True)
    with open(CFG, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def _fmt(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def main():
    args = sys.argv[1:]
    if "--version" in args:
        print("codebase-memory-mcp 9.9.9")
        return 0
    if args and args[0] == "config":
        sub = args[1] if len(args) > 1 else "list"
        data = _load()
        if sub == "list":
            print("Configuration:")
            for key, value in data.items():
                print(f"  {key:<22}= {_fmt(value)}")
            return 0
        if sub == "set":
            key, value = args[2], args[3]
            default = DEFAULTS.get(key)
            if isinstance(default, bool):
                value = value.lower() in ("true", "1", "yes", "on")
            elif isinstance(default, int):
                try:
                    value = int(value)
                except ValueError:
                    pass
            data[key] = value
            _save(data)
            print(f"set {key} = {_fmt(value)}")
            return 0
        if sub == "reset":
            key = args[2]
            data[key] = DEFAULTS.get(key)
            _save(data)
            print(f"reset {key}")
            return 0
        return 1
    # 常驻服务：阻塞在 stdin 上，直到父进程关闭或进程被终止
    print("codebase-memory-mcp fake server started", flush=True)
    try:
        while True:
            line = sys.stdin.readline()
            if line == "":
                # stdin 关闭后保持短暂存活，避免立即退出
                import time

                time.sleep(600)
                break
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
