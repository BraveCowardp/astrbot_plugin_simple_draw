import html
import os
from datetime import datetime
from string import Template
from urllib.parse import quote

from aiohttp import web
from astrbot.api import logger

try:
    from .config import DrawConfig
    from .prompt_store import PromptStore
except ImportError:
    from config import DrawConfig
    from prompt_store import PromptStore


class PromptWebService:
    def __init__(self, config: DrawConfig, store: PromptStore, output_dir: str):
        self.config = config
        self.store = store
        self.output_dir = os.path.abspath(output_dir)
        self.webui_dir = os.path.join(os.path.dirname(__file__), "webui")
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    @property
    def url(self) -> str:
        return f"http://{self.config.web_host}:{self.config.web_port}"

    async def start(self) -> None:
        if not self.config.web_enabled:
            return
        if self._runner:
            return

        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/records", self._handle_gallery)
        app.router.add_get("/records/{record_id}", self._handle_detail)
        app.router.add_get("/api/records", self._handle_records)
        app.router.add_get("/images/{filename}", self._handle_image)
        app.router.add_static("/static", os.path.join(self.webui_dir, "static"))

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.config.web_host, self.config.web_port)
        await self._site.start()
        logger.info(f"SimpleDraw prompt web service started at {self.url}")

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
            logger.info("SimpleDraw prompt web service stopped")

    async def _handle_index(self, request: web.Request) -> web.Response:
        stats = await self.store.get_stats()
        html_text = self._template("index.html").safe_substitute(
            total=stats.get("total", 0),
            success=stats.get("success", 0),
            failed=stats.get("failed", 0),
        )
        return web.Response(text=html_text, content_type="text/html")

    async def _handle_gallery(self, request: web.Request) -> web.Response:
        records = await self.store.list_records(self.config.web_page_size)
        cards = "\n".join(self._render_card(row) for row in records) or (
            '<div class="empty"><b>暂无记录</b><span>生成图片后，提示词会自动收录到这里。</span></div>'
        )
        html_text = self._template("gallery.html").safe_substitute(cards=cards)
        return web.Response(text=html_text, content_type="text/html")

    async def _handle_detail(self, request: web.Request) -> web.Response:
        record_id = int(request.match_info["record_id"])
        record = await self.store.get_record(record_id)
        if not record:
            raise web.HTTPNotFound()

        template_name = "detail.html" if request.headers.get("HX-Request") == "true" else "detail_page.html"
        return web.Response(text=self._render_detail(record, template_name), content_type="text/html")

    async def _handle_records(self, request: web.Request) -> web.Response:
        limit = int(request.query.get("limit", self.config.web_page_size))
        return web.json_response(await self.store.list_records(limit))

    async def _handle_image(self, request: web.Request) -> web.FileResponse:
        filename = os.path.basename(request.match_info["filename"])
        image_path = os.path.abspath(os.path.join(self.output_dir, filename))
        if not image_path.startswith(self.output_dir) or not os.path.exists(image_path):
            raise web.HTTPNotFound()
        return web.FileResponse(image_path)

    def _render_card(self, row: dict) -> str:
        view = self._record_view(row)
        status_class = self._status_class(row["status"])
        thumb = (
            f'<img src="{view["output_url"]}" alt="绘图结果" loading="lazy">'
            if view["output_url"]
            else f'<div class="failed-thumb">{html.escape(row["error"] or "生成失败")}</div>'
        )
        return self._template("card.html").safe_substitute(
            id=row["id"],
            thumb=thumb,
            prompt=html.escape(row["prompt"]),
            created_at_text=view["created_at_text"],
            status_class=status_class,
            status_text=self._status_text(row["status"]),
        )

    def _render_detail(self, row: dict, template_name: str = "detail.html") -> str:
        view = self._record_view(row)
        status_class = self._status_class(row["status"])
        preview = (
            f'<a href="{view["output_url"]}" target="_blank"><img src="{view["output_url"]}" alt="绘图结果"></a>'
            if view["output_url"]
            else f'<div class="placeholder">{html.escape(row["error"] or "没有生成图片")}</div>'
        )
        references = (
            "\n".join(f'<a href="{url}" target="_blank"><img src="{url}" alt="参考图"></a>' for url in view["reference_urls"])
            if view["reference_urls"]
            else '<div class="placeholder">无参考图</div>'
        )
        title = "绘图结果" if row["status"] == "success" else "失败记录"
        sender = html.escape(row["sender_name"] or row["sender_id"])
        return self._template(template_name).safe_substitute(
            title=title,
            created_at_text=view["created_at_text"],
            prompt=html.escape(row["prompt"]),
            input_outline=html.escape(row["input_outline"]),
            preview=preview,
            references=references,
            sender=sender,
            platform=html.escape(row["platform"]),
            session_id=html.escape(row["session_id"]),
            status_class=status_class,
            status_text=self._status_text(row["status"]),
        )

    def _record_view(self, row: dict) -> dict:
        return {
            **row,
            "created_at_text": datetime.fromtimestamp(row["created_at"]).strftime("%Y-%m-%d %H:%M:%S"),
            "output_url": self._image_url(row.get("output_path", "")),
            "reference_urls": [self._image_url(path) for path in str(row.get("reference_paths", "")).splitlines() if path],
        }

    def _image_url(self, path: str) -> str:
        filename = os.path.basename(path or "")
        if not filename:
            return ""
        return f"/images/{quote(filename)}"

    def _status_class(self, status: str) -> str:
        return "success" if status == "success" else "failed"

    def _status_text(self, status: str) -> str:
        return {"success": "成功", "failed": "失败"}.get(status, status)

    def _template(self, name: str) -> Template:
        path = os.path.join(self.webui_dir, "templates", name)
        with open(path, "r", encoding="utf-8") as file:
            return Template(file.read())
