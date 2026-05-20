from typing import Any, Mapping


class DrawConfig:
    def __init__(self, config: Mapping[str, Any]):
        self._config = config

    @property
    def base_url(self) -> str:
        return self.get_str("base_url", self.get_str("api_url", "https://api.openai.com/v1")).rstrip("/")

    @property
    def api_key(self) -> str:
        return self.get_str("api_key")

    @property
    def timeout(self) -> int:
        return self.get_int("timeout", 360)

    @property
    def proxy(self) -> str:
        return self.get_str("proxy")

    @property
    def max_reference_images(self) -> int:
        return max(0, self.get_int("max_reference_images", 4))

    @property
    def max_reference_image_side(self) -> int:
        return max(512, self.get_int("max_reference_image_side", 2048))

    @property
    def reference_image_quality(self) -> int:
        return min(95, max(50, self.get_int("reference_image_quality", 88)))

    @property
    def web_enabled(self) -> bool:
        return self.get_bool("web_enabled", True)

    @property
    def web_host(self) -> str:
        return self.get_str("web_host", "127.0.0.1")

    @property
    def web_port(self) -> int:
        return self.get_int("web_port", 7788)

    @property
    def web_page_size(self) -> int:
        return min(1000, max(1, self.get_int("web_page_size", 200)))

    def build_api_url(self, endpoint: str) -> str:
        return f"{self.base_url}/{endpoint.lstrip('/')}"

    def build_payload(self, prompt: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.get_str("model", "gpt-image-2"),
            "prompt": prompt,
            "n": 1,
        }

        for key, default in (
            ("size", "auto"),
            ("response_format", "b64_json"),
            ("quality", "auto"),
        ):
            value = self.get_str(key, default)
            if value:
                payload[key] = value

        return payload

    def build_headers(self, include_content_type: bool = True) -> dict[str, str]:
        authorization = self.api_key if self.api_key.lower().startswith("bearer ") else f"Bearer {self.api_key}"
        headers = {"Authorization": authorization}
        if include_content_type:
            headers["Content-Type"] = "application/json"
        return headers

    def get_str(self, key: str, default: str = "") -> str:
        value = self._config.get(key, default)
        return str(value).strip() if value is not None else default

    def get_int(self, key: str, default: int) -> int:
        value = self._config.get(key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool) -> bool:
        value = self._config.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "启用", "是"}
        return bool(value)
