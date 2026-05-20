import os
import time
from typing import Any, Iterable, Optional

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from .config import DrawConfig
    from .draw_client import DrawApiClient
    from .image_utils import ImageProcessor
    from .message_actions import MessageActions
    from .message_images import MessageImageExtractor
    from .prompt_store import PromptRecord, PromptStore
    from .prompt_web import PromptWebService
except ImportError:
    from config import DrawConfig
    from draw_client import DrawApiClient
    from image_utils import ImageProcessor
    from message_actions import MessageActions
    from message_images import MessageImageExtractor
    from prompt_store import PromptRecord, PromptStore
    from prompt_web import PromptWebService


@register(
    "astrbot_plugin_simple_draw",
    "codex",
    "通过聊天命令调用绘图 AI API 生成图片，并提供提示词收录 Web 服务。",
    "1.1.0",
)
class SimpleDrawPlugin(Star):
    def __init__(self, context: Context, config: Optional[AstrBotConfig] = None):
        super().__init__(context)
        self.config = DrawConfig(config or {})
        self.output_dir = os.path.join(os.path.dirname(__file__), "generated_images")
        self.image_processor = ImageProcessor(
            self.output_dir,
            max_side=self.config.max_reference_image_side,
            quality=self.config.reference_image_quality,
        )
        self.image_extractor = MessageImageExtractor(self.config, self.image_processor)
        self.draw_client = DrawApiClient(self.config, self.image_processor)
        self.message_actions = MessageActions()
        self.data_dir = os.path.join(os.path.dirname(__file__), "data")
        self.prompt_store = PromptStore(os.path.join(self.data_dir, "prompts.db"))
        self.prompt_web = PromptWebService(self.config, self.prompt_store, self.output_dir)

    async def initialize(self):
        await self.prompt_store.initialize()
        await self.prompt_web.start()

    @filter.command("draw", alias={"画图", "绘图"})
    async def draw(self, event: AstrMessageEvent):
        """使用绘图 AI 生成图片。用法：/draw 一只赛博朋克风格的猫"""
        prompt = self._extract_prompt(event.message_str)
        if not prompt:
            yield event.plain_result("请在命令后输入绘图提示词，例如：/draw 雨夜街头的机器人少女")
            return

        if not self.config.base_url or not self.config.api_key:
            yield event.plain_result("绘图 API 还没有配置，请先在插件配置里填写 base_url 和 api_key。")
            return

        await self.message_actions.recall_user_message(event)
        try:
            reference_images = await self.image_extractor.extract(event)
            image_path = await self.draw_client.generate_image(prompt, reference_images)
        except Exception as exc:
            logger.exception("SimpleDraw image generation failed")
            await self.message_actions.send_draw_forward(event, error=str(exc))
            if False:
                yield event.plain_result("")
            return

        await self._record_draw(event, prompt, status="success", output_path=image_path, reference_images=reference_images)
        await self.message_actions.send_draw_forward(event, image_path=image_path, archive_url=self._prompt_archive_url())
        if False:
            yield event.plain_result("")

    @filter.command("draw_help", alias={"画图帮助", "绘图帮助"})
    async def draw_help(self, event: AstrMessageEvent):
        """查看绘图插件帮助。"""
        yield event.plain_result(
            "绘图命令：\n"
            "/draw 提示词\n"
            "别名：/画图、/绘图\n"
            "示例：/draw 水彩风格，一座漂浮在云海上的图书馆，暖色光照"
        )

    def _extract_prompt(self, message: str) -> str:
        message = (message or "").strip()
        if not message:
            return ""

        parts = message.split(maxsplit=1)
        if len(parts) == 1:
            return ""
        return parts[1].strip()

    def _prompt_archive_url(self) -> str:
        return self.config.prompt_archive_url or self.prompt_web.url

    async def _record_draw(
        self,
        event: AstrMessageEvent,
        prompt: str,
        status: str,
        output_path: str = "",
        error: str = "",
        reference_images: Iterable[tuple[bytes, str]] = (),
    ) -> None:
        try:
            reference_paths = self._save_reference_previews(reference_images)
            await self.prompt_store.add_record(
                PromptRecord(
                    created_at=int(time.time()),
                    platform=event.get_platform_name(),
                    session_id=event.get_session_id(),
                    sender_id=str(event.get_sender_id()),
                    sender_name=event.get_sender_name(),
                    sender_avatar=self._extract_sender_avatar(event),
                    prompt=prompt,
                    input_outline=event.get_message_outline(),
                    output_path=output_path,
                    reference_paths="\n".join(reference_paths),
                    status=status,
                    error=error,
                ),
            )
        except Exception as exc:
            logger.warning(f"SimpleDraw failed to record prompt: {exc}")

    def _extract_sender_avatar(self, event: AstrMessageEvent) -> str:
        for value in self._iter_avatar_candidates(event):
            if value:
                return value

        platform = str(event.get_platform_name() or "").lower()
        sender_id = str(event.get_sender_id() or "").strip()
        if sender_id and any(name in platform for name in ("qq", "aiocqhttp", "napcat", "onebot")):
            return f"https://q1.qlogo.cn/g?b=qq&nk={sender_id}&s=100"
        return ""

    def _iter_avatar_candidates(self, event: AstrMessageEvent):
        for attr in ("get_sender_avatar", "get_avatar_url", "get_sender_avatar_url"):
            method = getattr(event, attr, None)
            if callable(method):
                try:
                    yield str(method() or "").strip()
                except Exception:
                    pass

        message_obj = getattr(event, "message_obj", None)
        for attr in ("sender_avatar", "avatar", "avatar_url", "face_url"):
            yield str(getattr(message_obj, attr, "") or "").strip()

        raw_message = getattr(message_obj, "raw_message", None)
        yield from self._iter_avatar_values(raw_message)

    def _iter_avatar_values(self, value: Any):
        if isinstance(value, dict):
            for key in ("avatar", "avatar_url", "face_url", "head_img", "headimgurl"):
                yield str(value.get(key, "") or "").strip()
            sender = value.get("sender")
            if isinstance(sender, dict):
                for key in ("avatar", "avatar_url", "face_url", "head_img", "headimgurl"):
                    yield str(sender.get(key, "") or "").strip()
            return

        for attr in ("avatar", "avatar_url", "face_url", "head_img", "headimgurl"):
            yield str(getattr(value, attr, "") or "").strip()

    def _save_reference_previews(self, reference_images: Iterable[tuple[bytes, str]]) -> list[str]:
        paths: list[str] = []
        for image_bytes, filename in reference_images:
            try:
                paths.append(self.image_processor.save_reference_preview(image_bytes, filename))
            except Exception as exc:
                logger.warning(f"SimpleDraw failed to save reference preview: {exc}")
        return paths

    async def terminate(self):
        await self.prompt_web.stop()
        logger.info("SimpleDrawPlugin terminated")
