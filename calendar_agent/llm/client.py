"""Bounded OpenAI-compatible transport. Planner owns the validation retry loop."""
import httpx
from ..config import llm_settings


class ModelError(RuntimeError):
    pass


class Client:
    def __init__(self, settings=None):
        self.settings = settings or llm_settings()

    def complete(self, messages):
        from ..providers.llm import MODEL_SLOTS
        with MODEL_SLOTS:
            return self._complete(messages)

    def _complete(self, messages):
        cfg = self.settings
        if not cfg.enabled:
            raise ModelError("未配置可用模型，请检查 .env 或关闭 MMM_OFFLINE。")
        body = {"model": cfg.model, "messages": messages, "temperature": 0.2}
        if cfg.reasoning_effort: body["reasoning_effort"] = cfg.reasoning_effort
        try:
            with httpx.Client(timeout=90) as client:
                response = client.post(cfg.base_url.rstrip("/") + "/chat/completions", json=body,
                                       headers={"Authorization": "Bearer " + cfg.api_key})
                response.raise_for_status()
                data = response.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ModelError("模型返回空内容或被截断。")
            return content
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
            # Do not leak tokens, provider bodies, or private request content.
            raise ModelError(f"模型请求失败（{type(error).__name__}），请检查模型接入配置。") from error
