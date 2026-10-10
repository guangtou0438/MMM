"""Model configuration owned by the calendar application."""

from __future__ import annotations

import os

from dataclasses import dataclass

@dataclass(frozen=True)
class ProviderSpec:
    """一个服务商的默认接入参数。

    key_envs / base_url_envs / model_envs 按顺序取第一个非空的环境变量，
    这样同一台机器上多个服务商的 key 可以并存，靠 LLM_PROVIDER 一行切换，
    不会出现 A 服务商捡走 B 服务商 key 的串线问题。
    """

    base_url: str
    key_envs: tuple[str, ...]
    default_model: str
    base_url_envs: tuple[str, ...] = ()
    model_envs: tuple[str, ...] = ()
    label: str = ""
    # 推理模型的思考强度。空字符串表示不发这个参数——
    # 不支持的服务商收到未知参数可能直接 400。
    default_reasoning_effort: str = ""

PROVIDERS: dict[str, ProviderSpec] = {
    "deepseek": ProviderSpec(
        base_url="https://api.deepseek.com/v1",
        key_envs=("DEEPSEEK_API_KEY",),
        default_model="deepseek-v4-flash",
        label="DeepSeek 官方",
    ),
    "dashscope": ProviderSpec(
        # 阿里云百炼。同一个 key 既能调 qwen 也能调平台上架的 deepseek。
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        base_url_envs=("DASHSCOPE_BASE_URL",),
        key_envs=("DASHSCOPE_API_KEY",),
        model_envs=("DASHSCOPE_MODEL",),
        default_model="qwen-plus",
        label="阿里云百炼 / DashScope",
    ),
    "zhipu": ProviderSpec(
        base_url="https://open.bigmodel.cn/api/paas/v4",
        base_url_envs=("ZHIPU_BASE_URL",),
        key_envs=("ZHIPU_API_KEY", "GLM_API_KEY"),
        model_envs=("ZHIPU_MODEL",),
        default_model="glm-5.3-flash",
        label="智谱 GLM / BigModel",
        # GLM-5.3-Flash 是推理模型且无法关闭思考，只能调档位（low/high/max）。
        # MMM 全是短的结构化调用，low 足够，实测比默认省约 6 倍思考 token。
        default_reasoning_effort="low",
    ),
}

PROVIDER_ALIASES = {
    "qwen": "dashscope",
    "bailian": "dashscope",
    "aliyun": "dashscope",
    "glm": "zhipu",
    "bigmodel": "zhipu",
    "zai": "zhipu",
}

def resolve_provider(name: str) -> str:
    key = (name or "").strip().lower()
    return PROVIDER_ALIASES.get(key, key)

def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()

def _first_env(names: tuple[str, ...]) -> str:
    for name in names:
        value = _env(name)
        if value:
            return value
    return ""

@dataclass(frozen=True)
class LLMConfig:
    provider: str
    api_key: str
    model: str
    base_url: str
    key_source: str = ""  # key 实际来自哪个环境变量，排查串线问题时有用
    reasoning_effort: str = ""  # 空则不发该参数

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

def offline_mode() -> bool:
    """通过兼容的 MMM_OFFLINE 开关停用真实模型调用。

    单靠 `unset XXX_API_KEY` 是不够的——.env 存在时 load_dotenv 会把它填回来，
    于是「以为在离线跑」实际上在花钱。跑测试和冒烟验证一律用这个开关。
    """
    return _env("MMM_OFFLINE", "0") not in ("0", "", "false", "False")

def load_llm_config() -> LLMConfig:
    provider = resolve_provider(_env("LLM_PROVIDER", "deepseek"))
    spec = PROVIDERS.get(provider)

    # 显式的 LLM_* 永远优先；没填才按 provider 查表回落到该服务商自己的环境变量
    if offline_mode():
        return LLMConfig(provider=provider, api_key="", model="", base_url="", key_source="")

    api_key = _env("LLM_API_KEY")
    key_source = "LLM_API_KEY" if api_key else ""
    if not api_key and spec is not None:
        for name in spec.key_envs:
            if _env(name):
                api_key, key_source = _env(name), name
                break

    base_url = _env("LLM_BASE_URL")
    if not base_url and spec is not None:
        base_url = _first_env(spec.base_url_envs) or spec.base_url

    model = _env("LLM_MODEL")
    if not model and spec is not None:
        model = _first_env(spec.model_envs) or spec.default_model

    # 显式配 none 表示「就是不要发这个参数」，区别于留空走 provider 默认
    effort = _env("LLM_REASONING_EFFORT")
    if not effort and spec is not None:
        effort = spec.default_reasoning_effort
    if effort.lower() in ("none", "off"):
        effort = ""

    return LLMConfig(
        provider=provider,
        api_key=api_key,
        model=model,
        base_url=base_url,
        key_source=key_source,
        reasoning_effort=effort,
    )
