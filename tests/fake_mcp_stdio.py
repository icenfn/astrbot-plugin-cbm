#!/usr/bin/env python3
"""一个用于测试的假 CBM MCP stdio 服务端。

按 MCP stdio 规范（newline-delimited JSON-RPC）响应：
- initialize         -> 返回协议版本、能力、serverInfo
- notifications/*    -> 不回复
- tools/list         -> 返回一个示例工具 echo
- tools/call         -> 回显 arguments.text
- ping               -> 返回 {}
仅用于单元测试，避免依赖真实的 CBM 二进制。
"""
import json
import sys

PROTOCOL_VERSION = "2025-03-26"


def _respond(msg_id, result=None, error=None):
    payload = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main():
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue
        method = message.get("method")
        msg_id = message.get("id")
        if method == "initialize":
            _respond(
                msg_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-cbm", "version": "9.9.9"},
                },
            )
        elif method and method.startswith("notifications/"):
            continue
        elif method == "ping":
            _respond(msg_id, {})
        elif method == "tools/list":
            _respond(
                msg_id,
                {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo back the provided text.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"],
                            },
                        }
                    ]
                },
            )
        elif method == "tools/call":
            params = message.get("params") or {}
            arguments = params.get("arguments") or {}
            text = str(arguments.get("text", ""))
            _respond(
                msg_id,
                {
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                },
            )
        elif msg_id is not None:
            _respond(msg_id, error={"code": -32601, "message": f"Method not found: {method}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
