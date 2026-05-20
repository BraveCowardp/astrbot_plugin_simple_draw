import json
import os
import re
from typing import Any, Optional, Tuple

import aiohttp

try:
    from .config import DrawConfig
    from .image_utils import ImageProcessor, guess_image_ext, guess_mime_type
except ImportError:
    from config import DrawConfig
    from image_utils import ImageProcessor, guess_image_ext, guess_mime_type


class DrawApiClient:
    def __init__(self, config: DrawConfig, image_processor: ImageProcessor):
        self.config = config
        self.image_processor = image_processor

    async def generate_image(self, prompt: str, reference_images: Optional[list[Tuple[bytes, str]]] = None) -> str:
        client_timeout = aiohttp.ClientTimeout(total=self.config.timeout)
        reference_images = reference_images or []

        if reference_images:
            return await self._edit_image(prompt, reference_images, client_timeout)

        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.post(
                    self.config.build_api_url("images/generations"),
                    json=self.config.build_payload(prompt),
                    headers=self.config.build_headers(),
                    proxy=self.config.proxy or None,
                ) as response:
                    body = await response.text()
                    if response.status >= 400:
                        raise RuntimeError(self._format_http_error("API 返回", response.status, body))
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"无法连接绘图 API: {exc}") from exc

        return await self._save_image_from_body(body)

    async def _edit_image(
        self,
        prompt: str,
        reference_images: list[Tuple[bytes, str]],
        client_timeout: aiohttp.ClientTimeout,
    ) -> str:
        form = aiohttp.FormData()
        for key, value in self.config.build_payload(prompt).items():
            form.add_field(key, str(value))

        for index, (image_bytes, filename) in enumerate(reference_images):
            image_bytes, filename = self.image_processor.normalize_reference_image(image_bytes, filename)
            form.add_field(
                "image",
                image_bytes,
                filename=filename or f"reference_{index}.png",
                content_type=guess_mime_type(filename),
            )

        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.post(
                    self.config.build_api_url("images/edits"),
                    data=form,
                    headers=self.config.build_headers(include_content_type=False),
                    proxy=self.config.proxy or None,
                ) as response:
                    body = await response.text()
                    if response.status >= 400:
                        raise RuntimeError(self._format_http_error("API 返回", response.status, body))
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"无法连接绘图编辑 API: {exc}") from exc

        return await self._save_image_from_body(body)

    async def _save_image_from_body(self, body: str) -> str:
        try:
            result = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("API 返回的不是合法 JSON") from exc

        return await self._save_image_from_response(result)

    async def _save_image_from_response(self, result: dict[str, Any]) -> str:
        data = result.get("data")
        if not isinstance(data, list) or not data:
            raise RuntimeError("API 响应中没有 data[0] 图片数据")

        first = data[0]
        if not isinstance(first, dict):
            raise RuntimeError("API 响应格式不正确：data[0] 不是对象")

        image_url = first.get("url")
        if image_url:
            return await self._download_image(str(image_url))

        b64_json = first.get("b64_json")
        if b64_json:
            return self.image_processor.save_base64_image(str(b64_json))

        raise RuntimeError("API 响应中没有 url 或 b64_json")

    async def _download_image(self, url: str) -> str:
        client_timeout = aiohttp.ClientTimeout(total=self.config.timeout)

        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.get(url, proxy=self.config.proxy or None) as response:
                    data = await response.read()
                    content_type = response.headers.get("Content-Type", "")
                    if response.status >= 400:
                        error_body = data.decode("utf-8", errors="ignore")
                        raise RuntimeError(self._format_http_error("下载图片失败", response.status, error_body))
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"无法下载生成的图片: {exc}") from exc

        path = self.image_processor.new_image_path(guess_image_ext(content_type))
        with open(path, "wb") as file:
            file.write(data)
        return path

    def _format_http_error(self, prefix: str, status: int, body: str) -> str:
        parts = [f"{prefix} HTTP {status}"]
        hint = self._http_status_hint(status)
        summary = self._summarize_error_body(body)
        if hint:
            parts.append(hint)
        if summary:
            parts.append(summary)
        return "：".join(parts)

    def _http_status_hint(self, status: int) -> str:
        hints = {
            400: "请求参数有误",
            401: "鉴权失败，请检查 API Key",
            403: "请求被拒绝，请检查权限或代理",
            404: "接口地址不存在，请检查 base_url",
            408: "请求超时",
            413: "请求体过大，参考图可能太大",
            429: "请求过于频繁或额度不足",
            500: "上游服务内部错误",
            502: "上游网关错误，可能为生成图片违规",
            503: "上游服务暂不可用",
            504: "上游网关超时",
            524: "上游服务处理超时，通常是绘图耗时超过网关限制",
        }
        return hints.get(status, "")

    def _summarize_error_body(self, body: str, limit: int = 500) -> str:
        body = (body or "").strip()
        if not body:
            return ""

        json_summary = self._summarize_json_error(body)
        if json_summary:
            return json_summary[:limit]

        lower_head = body[:500].lower()
        text = self._strip_html(body) if "<html" in lower_head or "<!doctype" in lower_head else body
        return self._normalize_space(text)[:limit]

    def _summarize_json_error(self, body: str) -> str:
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return ""

        if not isinstance(data, dict):
            return ""

        error = data.get("error")
        if isinstance(error, dict):
            for key in ("message", "code", "type"):
                if error.get(key):
                    return str(error[key])
        if isinstance(error, str):
            return error

        for key in ("message", "detail", "error_description"):
            if data.get(key):
                return str(data[key])
        return ""

    def _strip_html(self, html_text: str) -> str:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
        title = self._normalize_space(title_match.group(1)) if title_match else ""
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html_text)
        text = re.sub(r"(?s)<!--.*?-->", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = self._normalize_space(text)
        if title and title not in text:
            return f"{title} {text}"
        return text or title

    def _normalize_space(self, value: str) -> str:
        return " ".join(str(value or "").split())
