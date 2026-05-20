import inspect
import os
from typing import Any, Iterable, Set, Tuple

import aiohttp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
import astrbot.api.message_components as Comp

try:
    from .config import DrawConfig
    from .image_utils import ImageProcessor
except ImportError:
    from config import DrawConfig
    from image_utils import ImageProcessor


class MessageImageExtractor:
    def __init__(self, config: DrawConfig, image_processor: ImageProcessor):
        self.config = config
        self.image_processor = image_processor

    async def extract(self, event: AstrMessageEvent) -> list[Tuple[bytes, str]]:
        max_images = self.config.max_reference_images
        if max_images == 0:
            return []

        components = event.get_messages()
        reference_images: list[Tuple[bytes, str]] = []

        for image in self._iter_image_components(components):
            try:
                image_bytes, filename = await self._read_image_component(image)
            except Exception as exc:
                logger.warning(f"SimpleDraw ignored an unreadable reference image: {exc}")
                continue

            reference_images.append((image_bytes, filename))
            if len(reference_images) >= max_images:
                break

        if not reference_images:
            async for image_bytes, filename in self._extract_from_raw_message(getattr(event.message_obj, "raw_message", None)):
                reference_images.append((image_bytes, filename))
                if len(reference_images) >= max_images:
                    break

        if not reference_images and self._has_reply_component(components):
            raise RuntimeError("没有从引用消息中读取到图片。请确认引用的原消息里包含图片，或改用“图片 + /draw 提示词”的方式发送。")

        return reference_images

    def _iter_image_components(self, components: Iterable[Any]) -> Iterable[Comp.Image]:
        visited: Set[int] = set()
        yield from self._iter_image_components_inner(components, visited)

    def _iter_image_components_inner(self, components: Iterable[Any], visited: Set[int]) -> Iterable[Comp.Image]:
        for component in components:
            component_id = id(component)
            if component_id in visited:
                continue
            visited.add(component_id)

            if isinstance(component, Comp.Image):
                yield component
                continue

            for attr_name in ("chain", "message", "messages"):
                nested_components = getattr(component, attr_name, None)
                if isinstance(nested_components, list):
                    yield from self._iter_image_components_inner(nested_components, visited)

    def _has_reply_component(self, components: Iterable[Any]) -> bool:
        return any(isinstance(component, Comp.Reply) for component in components)

    async def _read_image_component(self, image: Comp.Image) -> Tuple[bytes, str]:
        file_path = await self._image_file_path(image)
        if file_path and os.path.exists(file_path):
            with open(file_path, "rb") as file:
                return self.image_processor.validate_image_bytes(file.read()), os.path.basename(file_path)

        base64_data = await self._image_base64(image)
        if base64_data:
            return self.image_processor.decode_base64_image(base64_data), "reference.png"

        image_url = str(getattr(image, "url", "") or getattr(image, "file", "") or "")
        if image_url.startswith(("http://", "https://")):
            return await self._download_bytes(image_url), os.path.basename(image_url.split("?", 1)[0]) or "reference.png"

        raise RuntimeError("无法读取图片组件")

    async def _image_file_path(self, image: Comp.Image) -> str:
        try:
            file_path = await self._maybe_await(image.convert_to_file_path())
        except Exception:
            file_path = ""

        if not file_path:
            file_path = str(getattr(image, "file", "") or getattr(image, "path", ""))

        if file_path.startswith("file:///"):
            file_path = file_path[8:]

        return str(file_path)

    async def _image_base64(self, image: Comp.Image) -> str:
        try:
            base64_data = await self._maybe_await(image.convert_to_base64())
        except Exception:
            base64_data = ""

        if not base64_data:
            base64_data = str(getattr(image, "base64", "") or getattr(image, "b64", ""))

        return str(base64_data)

    async def _extract_from_raw_message(self, raw_message: Any):
        for image_url in self._iter_raw_image_urls(raw_message):
            try:
                image_bytes = await self._download_bytes(image_url)
            except Exception as exc:
                logger.warning(f"SimpleDraw ignored an unreadable raw reference image: {exc}")
                continue

            yield image_bytes, os.path.basename(image_url.split("?", 1)[0]) or "reference.png"

    async def _download_bytes(self, url: str) -> bytes:
        client_timeout = aiohttp.ClientTimeout(total=self.config.timeout)

        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.get(url, proxy=self.config.proxy or None) as response:
                    data = await response.read()
                    if response.status >= 400:
                        error_body = data.decode("utf-8", errors="ignore")
                        raise RuntimeError(f"下载参考图失败 HTTP {response.status}: {error_body[:300]}")
                    content_type = response.headers.get("Content-Type", "")
                    return self.image_processor.validate_image_bytes(data, content_type)
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"无法下载参考图: {exc}") from exc

    def _iter_raw_image_urls(self, value: Any) -> Iterable[str]:
        visited: Set[int] = set()
        yield from self._iter_raw_image_urls_inner(value, visited)

    def _iter_raw_image_urls_inner(self, value: Any, visited: Set[int]) -> Iterable[str]:
        value_id = id(value)
        if value_id in visited:
            return
        visited.add(value_id)

        if isinstance(value, dict):
            type_text = str(value.get("type", "")).lower()
            for key in ("url", "file", "path"):
                raw_url = str(value.get(key, "") or "")
                if raw_url.startswith(("http://", "https://")) and ("image" in type_text or self._looks_like_image_url(raw_url)):
                    yield raw_url
            for item in value.values():
                yield from self._iter_raw_image_urls_inner(item, visited)
            return

        if isinstance(value, list):
            for item in value:
                yield from self._iter_raw_image_urls_inner(item, visited)

    def _looks_like_image_url(self, url: str) -> bool:
        path = url.split("?", 1)[0].lower()
        return path.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))

    async def _maybe_await(self, value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value
