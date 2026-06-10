"""
CodeCritic — 配置加载器

从 YAML 文件加载配置，合并环境变量。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


def _load_yaml(path: Path) -> dict[str, Any]:
    """加载 YAML 文件"""
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolve_env_vars(value: Any) -> Any:
    """递归解析字符串中的 ${VAR} 环境变量"""
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            env_var = value[2:-1]
            env_value = os.getenv(env_var)
            if env_value is None:
                # 环境变量未设置时保留原模板字符串
                # 实际使用时会通过 build_llm_kwargs 报错
                return value
            return env_value
        return value
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


def load_env():
    """加载 .env 文件"""
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        # 尝试 .env 作为 fallback
        example_path = PROJECT_ROOT / ".env"
        if example_path.exists():
            print(
                "[提示] 未找到 .env 文件，已加载 .env"
                "（如需使用请复制为 .env 并填入 API Key）"
            )
            load_dotenv(example_path)


def load_settings() -> dict[str, Any]:
    """加载 settings.yaml"""
    path = CONFIG_DIR / "settings.yaml"
    return _load_yaml(path)


def load_models_config() -> dict[str, Any]:
    """加载 models.yaml，并解析环境变量引用"""
    path = CONFIG_DIR / "models.yaml"
    raw = _load_yaml(path)
    models = raw.get("models", {})
    resolved = {}
    for name, cfg in models.items():
        resolved[name] = _resolve_env_vars(cfg)
    return resolved


def load_agents_config() -> dict[str, Any]:
    """加载 agents.yaml"""
    path = CONFIG_DIR / "agents.yaml"
    raw = _load_yaml(path)
    return raw.get("agents", {})


def get_model_config(
    model_name: str,
    models_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """获取单个模型的完整配置"""
    if models_config is None:
        models_config = load_models_config()
    cfg = models_config.get(model_name)
    if cfg is None:
        raise ValueError(
            f"Model '{model_name}' not found in models.yaml. "
            f"Available models: {list(models_config.keys())}"
        )
    return cfg


def build_llm_kwargs(model_name: str, models_config: dict[str, Any]) -> dict[str, Any]:
    """
    根据模型名构建 LLM 初始化参数。

    所有参数来自 models.yaml 配置：
    - api_key: 支持 ${ENV_VAR} 模板（推荐）或直接写值
    - base_url: 支持 ${ENV_VAR} 模板或直接写 URL
    - 支持任意 provider-specific 参数

    Returns:
        dict 包含 provider, model_name, api_key, base_url 等参数
    """
    cfg = get_model_config(model_name, models_config)
    provider = cfg["provider"]

    kwargs = {
        "model_name": cfg["model_name"],
        "temperature": cfg.get("temperature", 0.2),
        "max_tokens": cfg.get("max_tokens", 4096),
        "max_retries": cfg.get("max_retries", 3),
    }

    # 统一解析 api_key（从 cfg 读取，支持 ${VAR} 模板）
    api_key = cfg.get("api_key", "")
    if isinstance(api_key, str) and api_key.startswith("${") and api_key.endswith("}"):
        env_var = api_key[2:-1]
        api_key = os.getenv(env_var, "")
    if api_key:
        kwargs["api_key"] = api_key

    # 统一解析 base_url（从 cfg 读取，支持 ${VAR} 模板）
    base_url = cfg.get("base_url", "")
    if isinstance(base_url, str) and base_url.startswith("${") and base_url.endswith("}"):
        env_var = base_url[2:-1]
        base_url = os.getenv(env_var, "")
    if base_url:
        kwargs["base_url"] = base_url

    # 映射 provider → LangChain provider 标记
    provider_map = {
        "openai": "openai",
        "anthropic": "anthropic",
        "openai_compatible": "openai",
        "ollama": "openai",
    }
    mapped = provider_map.get(provider)
    if mapped is None:
        raise ValueError(f"Unsupported provider: {provider}")
    kwargs["_provider"] = mapped

    return kwargs
