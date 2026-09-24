"""Small CLI for exercising the MCP gateway from a terminal.

    # 1) 列出某个 agent 可见的工具
    .venv\\Scripts\\python.exe scripts/mcp_gateway_call.py tools --token dev-token-codex

    # 2) 调用工具
    .venv\\Scripts\\python.exe scripts/mcp_gateway_call.py call gateway_ping \
        --token dev-token-codex --arg message=hello

    # 3) 读取资源 / 展开提示词
    .venv\\Scripts\\python.exe scripts/mcp_gateway_call.py read \
        spaces://collab/knowledge/README --token dev-token-codex
    .venv\\Scripts\\python.exe scripts/mcp_gateway_call.py prompt code-review \
        --token dev-token-codex --arg diff="--- a"

    # 4) 给注册过的 agent 铸 token（需要签名密钥）
    .venv\\Scripts\\python.exe scripts/mcp_gateway_call.py mint --agent codex-main
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

DEFAULT_URL = os.getenv("MCP_GATEWAY_URL", "http://127.0.0.1:8088/mcp")


def rpc(url: str, token: str, method: str, params: dict | None = None) -> dict:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    response = httpx.post(url, json=payload, headers=headers, timeout=60)
    response.raise_for_status()
    return response.json()


def _parse_args(pairs: list[str]) -> dict:
    parsed: dict = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"参数格式应为 key=value，收到：{pair}")
        key, value = pair.split("=", 1)
        parsed[key] = value
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MCP gateway CLI")
    parser.add_argument("command", choices=["tools", "call", "resources", "read", "prompts", "prompt", "mint"])
    parser.add_argument("target", nargs="?", help="工具名 / 资源 URI / 提示词名 / agent id")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--token", default=os.getenv("MCP_GATEWAY_TOKEN", ""))
    parser.add_argument("--arg", action="append", default=[], help="key=value，可重复")
    parser.add_argument("--secret", default=os.getenv("MCP_GATEWAY_JWT_SECRET", ""))
    parser.add_argument("--ttl", type=int, default=43200)
    args = parser.parse_args(argv)

    if args.command == "mint":
        if not args.secret:
            raise SystemExit("铸 token 需要 --secret 或 MCP_GATEWAY_JWT_SECRET")
        from app.mcp_gateway.auth import mint_agent_token

        token = mint_agent_token(args.secret, args.target or "", [], args.ttl)
        print(token)
        return 0

    if args.command == "tools":
        body = rpc(args.url, args.token, "tools/list")
    elif args.command == "call":
        body = rpc(
            args.url,
            args.token,
            "tools/call",
            {"name": args.target, "arguments": _parse_args(args.arg)},
        )
    elif args.command == "resources":
        body = rpc(args.url, args.token, "resources/list")
    elif args.command == "read":
        body = rpc(args.url, args.token, "resources/read", {"uri": args.target})
    elif args.command == "prompts":
        body = rpc(args.url, args.token, "prompts/list")
    else:  # prompt
        body = rpc(
            args.url,
            args.token,
            "prompts/get",
            {"name": args.target, "arguments": _parse_args(args.arg)},
        )

    print(json.dumps(body, ensure_ascii=False, indent=2))
    return 1 if "error" in body else 0


if __name__ == "__main__":
    sys.exit(main())
