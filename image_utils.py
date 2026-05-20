import base64
import io
import os
import time
from typing import Tuple

from PIL import Image as PILImage


class ImageProcessor:
    def __init__(self, output_dir: str, max_side: int = 2048, quality: int = 88):
        self.output_dir = output_dir
        self.max_side = max_side
        self.quality = quality
        os.makedirs(self.output_dir, exist_ok=True)

    def validate_image_bytes(self, data: bytes, content_type: str = "") -> bytes:
        if content_type and "image" not in content_type.lower():
            raise RuntimeError(f"参考图下载结果不是图片: {content_type}")

        try:
            with PILImage.open(io.BytesIO(data)) as image:
                image.verify()
        except Exception as exc:
            raise RuntimeError("参考图数据不是有效图片") from exc

        return data

    def normalize_reference_image(self, data: bytes, filename: str) -> Tuple[bytes, str]:
        with PILImage.open(io.BytesIO(data)) as image:
            image = image.convert("RGB")
            image.thumbnail((self.max_side, self.max_side))

            output = io.BytesIO()
            image.save(output, format="JPEG", quality=self.quality, optimize=True)

        base_name = os.path.splitext(filename or "reference.jpg")[0] or "reference"
        return output.getvalue(), f"{base_name}.jpg"

    def save_reference_preview(self, data: bytes, filename: str) -> str:
        image_bytes, normalized_name = self.normalize_reference_image(data, filename)
        path = os.path.join(self.output_dir, f"draw_{int(time.time() * 1000)}_ref_{normalized_name}")
        with open(path, "wb") as file:
            file.write(image_bytes)
        return path

    def save_base64_image(self, b64_json: str) -> str:
        try:
            image_bytes = self.decode_base64_image(b64_json)
        except RuntimeError as exc:
            raise RuntimeError("API 返回的 b64_json 不是合法 Base64 图片数据") from exc

        path = self.new_image_path(".png")
        with open(path, "wb") as file:
            file.write(image_bytes)
        return path

    def decode_base64_image(self, value: str) -> bytes:
        if "," in value and value.lstrip().startswith("data:"):
            value = value.split(",", 1)[1]

        if value.startswith("base64://"):
            value = value.removeprefix("base64://")

        try:
            return base64.b64decode(value)
        except ValueError as exc:
            raise RuntimeError("Base64 图片数据不合法") from exc

    def new_image_path(self, ext: str) -> str:
        filename = f"draw_{int(time.time() * 1000)}{ext}"
        return os.path.join(self.output_dir, filename)


def guess_image_ext(content_type: str) -> str:
    content_type = content_type.lower()
    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    if "webp" in content_type:
        return ".webp"
    return ".png"


def guess_mime_type(filename: str) -> str:
    filename = (filename or "").lower()
    if filename.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if filename.endswith(".webp"):
        return "image/webp"
    return "image/png"
