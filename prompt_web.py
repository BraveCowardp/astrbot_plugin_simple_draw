import html
import math
import os
import re
from collections import Counter
from datetime import datetime
from functools import lru_cache
from string import Template
from urllib.parse import quote

from aiohttp import web
from astrbot.api import logger
from PIL import Image as PILImage

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
        html_text = self._template("index.html").safe_substitute(total=stats.get("total", 0))
        return web.Response(text=html_text, content_type="text/html")

    async def _handle_gallery(self, request: web.Request) -> web.Response:
        records = await self.store.list_records()
        stats = await self.store.get_stats()
        groups = self._group_records(records)
        cards = "\n".join(self._render_card(group) for group in groups) or (
            '<div class="empty"><b>暂无记录</b><span>生成图片后，提示词会自动收录到这里。</span></div>'
        )
        html_text = self._template("gallery.html").safe_substitute(cards=cards, total=stats.get("total", 0))
        return web.Response(text=html_text, content_type="text/html")

    async def _handle_detail(self, request: web.Request) -> web.Response:
        record_id = int(request.match_info["record_id"])
        records = await self.store.list_records()
        group = self._find_group(records, record_id)
        if not group:
            raise web.HTTPNotFound()

        selected = next((row for row in group["records"] if row["id"] == record_id), group["primary"])
        group["primary"] = selected
        template_name = "detail.html" if request.headers.get("HX-Request") == "true" else "detail_page.html"
        return web.Response(text=self._render_detail(group, template_name), content_type="text/html")

    async def _handle_records(self, request: web.Request) -> web.Response:
        return web.json_response(await self.store.list_records())

    async def _handle_image(self, request: web.Request) -> web.FileResponse:
        filename = os.path.basename(request.match_info["filename"])
        image_path = os.path.abspath(os.path.join(self.output_dir, filename))
        if not image_path.startswith(self.output_dir) or not os.path.exists(image_path):
            raise web.HTTPNotFound()
        return web.FileResponse(image_path)

    def _render_card(self, group: dict) -> str:
        row = group["primary"]
        view = self._record_view(row)
        thumb = f'<img src="{view["output_url"]}" alt="绘图结果" loading="lazy">' if view["output_url"] else ""
        return self._template("card.html").safe_substitute(
            id=row["id"],
            thumb=thumb,
            prompt=html.escape(row["prompt"]),
            created_at_text=view["created_at_text"],
            aspect_ratio=self._image_aspect_ratio(row.get("output_path", "")),
            group_badge=self._render_group_badge(group),
        )

    def _render_detail(self, group: dict, template_name: str = "detail.html") -> str:
        row = group["primary"]
        view = self._record_view(row)
        preview = (
            f'<a href="{view["output_url"]}" target="_blank"><img src="{view["output_url"]}" alt="绘图结果"></a>'
            if view["output_url"]
            else '<div class="placeholder">没有生成图片</div>'
        )
        references = (
            "\n".join(f'<a href="{url}" target="_blank"><img src="{url}" alt="参考图"></a>' for url in view["reference_urls"])
            if view["reference_urls"]
            else '<div class="placeholder">无参考图</div>'
        )
        sender_value = row["sender_name"] or row["sender_id"]
        prompt = str(row["prompt"] or "")
        return self._template(template_name).safe_substitute(
            title="绘图结果",
            created_at_text=view["created_at_text"],
            prompt=html.escape(prompt),
            preview=preview,
            references=references,
            sender=html.escape(sender_value),
            avatar=self._render_avatar(row, sender_value),
            group_summary=self._render_group_summary(group),
            group_records=self._render_group_records(group),
        )

    def _render_group_badge(self, group: dict) -> str:
        count = len(group["records"])
        if count <= 1:
            return ""
        return f'<span class="group-badge">合并 {count}</span>'

    def _render_group_summary(self, group: dict) -> str:
        count = len(group["records"])
        if count <= 1:
            return ""
        threshold = int(round(self.config.prompt_similarity_threshold * 100))
        return (
            '<div class="section group-summary">'
            '<div><div class="label">相似提示词</div>'
            f'<div class="text">已合并 {count} 条相似提示词，当前阈值 {threshold}%</div></div>'
            f'<div class="group-count">{count}</div>'
            "</div>"
        )

    def _render_group_records(self, group: dict) -> str:
        records = group["records"]
        if len(records) <= 1:
            return ""

        items = "\n".join(self._render_nested_card(row, group["primary"]["id"]) for row in records)
        return (
            '<div class="section merged-records">'
            '<div class="section-head"><div class="label">相似提示词</div></div>'
            f'<div class="nested-grid">{items}</div>'
            "</div>"
        )

    def _render_nested_card(self, row: dict, active_id: int) -> str:
        view = self._record_view(row)
        active_class = " active" if row["id"] == active_id else ""
        thumb = f'<img src="{view["output_url"]}" alt="绘图结果" loading="lazy">' if view["output_url"] else ""
        sender_value = row["sender_name"] or row["sender_id"]
        return (
            f'<a class="nested-card{active_class}" style="--card-ratio: {self._image_aspect_ratio(row.get("output_path", ""))};" '
            f'href="/records/{row["id"]}" hx-get="/records/{row["id"]}" hx-target="#modal-root" hx-swap="innerHTML" '
            'aria-label="查看组内记录详情">'
            f'<div class="nested-thumb">{thumb}</div>'
            '<div class="nested-body">'
            '<div class="card-kicker">'
            f'<span># {row["id"]}</span>'
            '</div>'
            f'<div class="prompt">{html.escape(str(row["prompt"] or ""))}</div>'
            '<div class="meta">'
            f'<span>{html.escape(view["created_at_text"])}</span><span>{html.escape(sender_value)}</span>'
            '</div>'
            '</div>'
            '</a>'
        )

    def _group_records(self, records: list[dict]) -> list[dict]:
        if not records:
            return []

        groups: list[dict] = []
        for row in records:
            vector = self._prompt_vector(row.get("prompt", ""))
            target = self._best_group(groups, vector)
            if target:
                target["records"].append(row)
                target["vectors"].append(vector)
            else:
                groups.append({"primary": row, "records": [row], "vectors": [vector]})

        for group in groups:
            group["records"].sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
            group["primary"] = group["records"][0]

        return sorted(groups, key=lambda group: (group["primary"]["created_at"], group["primary"]["id"]), reverse=True)

    def _find_group(self, records: list[dict], record_id: int) -> dict | None:
        for group in self._group_records(records):
            if any(row["id"] == record_id for row in group["records"]):
                return group
        return None

    def _best_group(self, groups: list[dict], vector: Counter) -> dict | None:
        threshold = self.config.prompt_similarity_threshold
        best_group = None
        best_score = 0.0
        for group in groups:
            score = max(self._cosine_similarity(vector, other) for other in group["vectors"])
            if score > best_score:
                best_group = group
                best_score = score
        return best_group if best_score >= threshold else None

    @staticmethod
    def _prompt_vector(prompt: str) -> Counter:
        text = re.sub(r"\s+", " ", str(prompt or "").strip().lower())
        if not text:
            return Counter()

        padded = f" {text} "
        grams = []
        for size in (2, 3):
            grams.extend(padded[index : index + size] for index in range(max(1, len(padded) - size + 1)))
        tokens = re.findall(r"[\w.-]+", text)
        return Counter(grams + tokens)

    @staticmethod
    def _cosine_similarity(left: Counter, right: Counter) -> float:
        if not left or not right:
            return 0.0

        common = left.keys() & right.keys()
        dot = sum(left[key] * right[key] for key in common)
        left_norm = math.sqrt(sum(value * value for value in left.values()))
        right_norm = math.sqrt(sum(value * value for value in right.values()))
        if not left_norm or not right_norm:
            return 0.0
        return dot / (left_norm * right_norm)

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

    @staticmethod
    @lru_cache(maxsize=512)
    def _read_image_size(path: str) -> tuple[int, int] | None:
        if not path or not os.path.exists(path):
            return None
        try:
            with PILImage.open(path) as image:
                return image.size
        except Exception:
            return None

    def _image_aspect_ratio(self, path: str) -> str:
        size = self._read_image_size(os.path.abspath(path or ""))
        if not size:
            return "1 / 1"
        width, height = size
        if width <= 0 or height <= 0:
            return "1 / 1"
        return f"{width} / {height}"

    def _render_avatar(self, row: dict, fallback_value: str) -> str:
        avatar_url = str(row.get("sender_avatar", "") or "").strip()
        fallback = html.escape(self._avatar_text(fallback_value))
        if avatar_url:
            return (
                f'<div class="user-avatar-fallback">{fallback}</div>'
                f'<img class="user-avatar-img" src="{html.escape(avatar_url, quote=True)}" alt="用户头像" '
                f'onerror="this.style.display=\'none\'">'
            )
        return f'<div class="user-avatar-fallback">{fallback}</div>'

    def _avatar_text(self, value: str) -> str:
        value = str(value or "").strip()
        return value[:1].upper() if value else "U"

    def _template(self, name: str) -> Template:
        path = os.path.join(self.webui_dir, "templates", name)
        with open(path, "r", encoding="utf-8") as file:
            return Template(file.read())
