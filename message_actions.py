from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
import astrbot.api.message_components as Comp


class MessageActions:
    async def recall_user_message(self, event: AstrMessageEvent) -> None:
        message_id = self._get_message_id(event)
        if not message_id:
            return

        bot = getattr(event, "bot", None)
        if bot and hasattr(bot, "call_action"):
            try:
                await bot.call_action("delete_msg", message_id=message_id)
                return
            except Exception as exc:
                logger.warning(f"SimpleDraw failed to recall message by OneBot delete_msg: {exc}")

        client = getattr(event, "client", None)
        for method_name in ("recall_message", "delete_message", "revoke_message"):
            method = getattr(client, method_name, None) if client else None
            if not method:
                continue
            try:
                await method(message_id)
                return
            except Exception as exc:
                logger.warning(f"SimpleDraw failed to recall message by {method_name}: {exc}")

    async def send_draw_forward(
        self,
        event: AstrMessageEvent,
        image_path: str | None = None,
        error: str | None = None,
        archive_url: str = "",
    ) -> None:
        chain = MessageChain([self._build_forward_nodes(event, image_path=image_path, error=error, archive_url=archive_url)])
        try:
            await event.send(chain)
        except Exception as exc:
            logger.warning(f"SimpleDraw failed to send forward message, fallback to normal message: {exc}")
            await event.send(self._build_fallback_chain(image_path=image_path, error=error, archive_url=archive_url))

    def _build_forward_nodes(
        self,
        event: AstrMessageEvent,
        image_path: str | None,
        error: str | None,
        archive_url: str,
    ) -> Comp.Nodes:
        nodes = [
            Comp.Node(
                self._original_message_content(event),
                name=event.get_sender_name() or "用户",
                uin=str(event.get_sender_id() or 0),
            ),
            Comp.Node(
                self._draw_result_content(image_path=image_path, error=error),
                name="SimpleDraw",
                uin=str(event.get_self_id() or 0),
            ),
        ]
        if image_path and archive_url:
            nodes.append(
                Comp.Node(
                    [Comp.Plain(f"提示词已收录至{archive_url}")],
                    name="SimpleDraw",
                    uin=str(event.get_self_id() or 0),
                ),
            )
        return Comp.Nodes(nodes)

    def _original_message_content(self, event: AstrMessageEvent) -> list[Any]:
        content = list(event.get_messages() or [])
        if content:
            return content

        outline = event.get_message_outline() or event.get_message_str() or "原绘图请求"
        return [Comp.Plain(outline)]

    def _draw_result_content(self, image_path: str | None, error: str | None) -> list[Any]:
        if error:
            return [Comp.Plain(f"画图失败：{error}")]
        if image_path:
            return [Comp.Plain("绘图结果"), Comp.Image.fromFileSystem(image_path)]
        return [Comp.Plain("画图失败：没有生成图片")]

    def _build_fallback_chain(self, image_path: str | None, error: str | None, archive_url: str) -> MessageChain:
        if error:
            return MessageChain([Comp.Plain(f"画图失败：{error}")])
        if image_path:
            messages: list[Any] = [Comp.Image.fromFileSystem(image_path)]
            if archive_url:
                messages.append(Comp.Plain(f"\n提示词已收录至{archive_url}"))
            return MessageChain(messages)
        return MessageChain([Comp.Plain("画图失败：没有生成图片")])

    def _get_message_id(self, event: AstrMessageEvent) -> str:
        message_id = str(getattr(event.message_obj, "message_id", "") or "")
        if message_id:
            return message_id

        raw_message = getattr(event.message_obj, "raw_message", None)
        if isinstance(raw_message, dict):
            return str(raw_message.get("message_id", "") or raw_message.get("id", "") or "")

        return str(getattr(raw_message, "message_id", "") or getattr(raw_message, "id", "") or "")
