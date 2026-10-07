"""Generic JSON-over-HTTP tool adapter (image generation, internal services…).

Supports:
  * synchronous JSON responses,
  * async task style (``task_id`` -> poll status URL until terminal),
  * image extraction (base64 field, or downloadable URL below a size cap).

All upstream credentials come from environment variables held by the adapter.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any, Mapping

import httpx

from app.mcp_gateway.adapters.base import (
    BaseAdapter,
    ToolResult,
    image_content,
    text_content,
)
from app.mcp_gateway.config import SkillSpec
from app.mcp_gateway.errors import McpError, UPSTREAM_UNAVAILABLE

_MOCK_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


def get_path(data: Any, path: str, default: Any = None) -> Any:
    """Dotted lookup supporting dict keys and list indices (``a.b.0.c``)."""

    if not path:
        return data
    current = data
    for part in str(path).split("."):
        if isinstance(current, Mapping):
            if part not in current:
                return default
            current = current[part]
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return default
        else:
            return default
    return current


class HttpJsonAdapter(BaseAdapter):
    name = "http-json"

    async def call_tool(
        self, skill: SkillSpec, args: Mapping[str, Any], *, agent: Any = None
    ) -> ToolResult:
        if self.ctx.mock:
            return self._mock_result(skill, args)

        options = skill.options or {}
        url = self.require_upstream(skill)
        method = str(options.get("method") or "POST").upper()
        headers = self.auth_headers(skill)
        body = self._build_body(skill, args)

        try:
            async with self.ctx.client(timeout=skill.timeout_sec) as client:
                response = await client.request(method, url, json=body, headers=headers)
                response.raise_for_status()
                payload = self._decode(response)
        except McpError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalized as upstream failure
            raise McpError(
                UPSTREAM_UNAVAILABLE,
                f"上游 '{skill.id}' 调用失败：{type(exc).__name__}: {exc}",
                data={"skill": skill.id, "stage": "request"},
            ) from exc

        task_id_field = options.get("task_id_field")
        if options.get("async") and task_id_field:
            payload = await self._poll_task(skill, url, payload)

        return await self._to_result(skill, payload)

    # --- internals ------------------------------------------------------------
    def _build_body(self, skill: SkillSpec, args: Mapping[str, Any]) -> dict[str, Any]:
        template = (skill.options or {}).get("body_template")
        if isinstance(template, Mapping):
            body: dict[str, Any] = {}
            for key, value in template.items():
                if isinstance(value, str) and value.startswith("$"):
                    body[key] = args.get(value[1:])
                else:
                    body[key] = value
            return body
        return dict(args)

    def _decode(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return {"text": response.text}

    async def _poll_task(
        self, skill: SkillSpec, upstream: str, payload: Any
    ) -> Any:
        options = skill.options or {}
        task_id = get_path(payload, str(options.get("task_id_field")))
        if task_id is None:
            return payload
        status_url = str(
            options.get("status_url_template") or "{upstream}/tasks/{task_id}"
        ).format(upstream=upstream.rstrip("/"), task_id=task_id)
        status_field = str(options.get("status_field") or "status")
        done = {str(v).lower() for v in options.get("done_values") or ("succeeded",)}
        failed = {str(v).lower() for v in options.get("failed_values") or ("failed",)}
        interval = float(options.get("poll_interval_sec") or 2)
        deadline = time.monotonic() + max(skill.timeout_sec, 1)
        headers = self.auth_headers(skill)

        try:
            async with self.ctx.client(timeout=skill.timeout_sec) as client:
                while time.monotonic() < deadline:
                    response = await client.get(status_url, headers=headers)
                    response.raise_for_status()
                    data = self._decode(response)
                    state = str(get_path(data, status_field, "")).lower()
                    if state in done:
                        return data
                    if state in failed:
                        raise McpError(
                            UPSTREAM_UNAVAILABLE,
                            f"上游任务失败：{state}",
                            data={"skill": skill.id, "status": state},
                        )
                    await asyncio.sleep(interval)
        except McpError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise McpError(
                UPSTREAM_UNAVAILABLE,
                f"轮询上游任务失败：{type(exc).__name__}: {exc}",
                data={"skill": skill.id, "stage": "poll"},
            ) from exc
        raise McpError(
            UPSTREAM_UNAVAILABLE,
            f"轮询上游任务超时（{skill.timeout_sec}s）。",
            data={"skill": skill.id, "stage": "poll"},
        )

    async def _to_result(self, skill: SkillSpec, payload: Any) -> ToolResult:
        options = skill.options or {}
        result_field = options.get("result_field")
        result = get_path(payload, str(result_field)) if result_field else payload
        if result is None:
            result = payload
        image_result = next((item for item in result if isinstance(item, Mapping)), result) if isinstance(result, list) else result

        image_field = options.get("image_field")
        if image_field:
            data = get_path(image_result, str(image_field))
            if isinstance(data, str) and data:
                return ToolResult(
                    content=[image_content(data, self._mime(skill, options))]
                )

        url_field = options.get("image_url_field")
        if url_field:
            url = get_path(image_result, str(url_field))
            if isinstance(url, str) and url.startswith("http"):
                return await self._url_to_image(skill, url)

        if isinstance(result, str):
            return ToolResult(content=[text_content(result)])
        return ToolResult(
            content=[text_content(json.dumps(result, ensure_ascii=False, default=str))]
        )

    async def _url_to_image(self, skill: SkillSpec, url: str) -> ToolResult:
        """Download small images as base64; fall back to a URL text block."""

        options = skill.options or {}
        max_bytes = int(options.get("max_image_bytes") or 5_242_880)
        try:
            async with self.ctx.client(timeout=skill.timeout_sec) as client:
                response = await client.get(url)
                response.raise_for_status()
                raw = response.content
        except Exception as exc:  # noqa: BLE001
            raise McpError(
                UPSTREAM_UNAVAILABLE,
                f"下载生成结果失败：{type(exc).__name__}: {exc}",
                data={"skill": skill.id},
            ) from exc
        if len(raw) > max_bytes:
            return ToolResult(
                content=[
                    text_content(
                        f"图片超过 {max_bytes} 字节，已改为返回 URL：{url}"
                    )
                ]
            )
        mime = response.headers.get("content-type", "image/png").split(";")[0].strip()
        return ToolResult(content=[image_content(base64.b64encode(raw).decode("ascii"), mime)])

    def _mime(self, skill: SkillSpec, options: Mapping[str, Any]) -> str:
        return str(options.get("mime_type") or skill.mime_type or "image/png")

    def _mock_result(self, skill: SkillSpec, args: Mapping[str, Any]) -> ToolResult:
        note = (
            f"[mock] {skill.id} 未真正调用上游。"
            f"参数：{json.dumps(dict(args), ensure_ascii=False)[:200]}"
        )
        if skill.id == "generate_image" or (skill.options or {}).get("image_field"):
            return ToolResult(content=[image_content(_MOCK_PNG), text_content(note)])
        return ToolResult(content=[text_content(note)])
