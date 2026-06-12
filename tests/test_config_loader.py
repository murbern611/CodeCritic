"""
测试配置加载器
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.utils.config_loader import (
    CONFIG_DIR,
    PROJECT_ROOT,
    _resolve_env_vars,
    build_llm_kwargs,
    load_settings,
)


class TestResolveEnvVars:
    """测试环境变量解析"""

    def test_simple_env_var(self):
        """核心：${VAR} 替换"""
        os.environ["_TEST_KEY"] = "test_value_123"
        result = _resolve_env_vars("${_TEST_KEY}")
        assert result == "test_value_123"

    def test_non_env_string(self):
        """边界：普通字符串不变"""
        result = _resolve_env_vars("hello world")
        assert result == "hello world"

    def test_unset_var_keeps_template(self):
        """边界：未设置的环境变量保留模板"""
        # 确保变量不存在
        os.environ.pop("_NONEXISTENT_VAR_XYZ", None)
        result = _resolve_env_vars("${_NONEXISTENT_VAR_XYZ}")
        assert result == "${_NONEXISTENT_VAR_XYZ}"

    def test_dict_recursive(self):
        """基础：递归解析 dict"""
        os.environ["_TEST_DB"] = "mydb"
        data = {"database": "${_TEST_DB}", "host": "localhost"}
        result = _resolve_env_vars(data)
        assert result == {"database": "mydb", "host": "localhost"}

    def test_list_recursive(self):
        """基础：递归解析 list"""
        os.environ["_TEST_VAL"] = "42"
        data = ["${_TEST_VAL}", "static"]
        result = _resolve_env_vars(data)
        assert result == ["42", "static"]


class TestBuildLlmKwargs:
    """测试 LLM 参数构建"""

    def test_build_openai_kwargs(self):
        """核心：OpenAI 参数"""
        models_config = {
            "gpt-4o-mini": {
                "provider": "openai",
                "model_name": "gpt-4o-mini",
                "api_key": "sk-test-key",
                "max_tokens": 4096,
                "temperature": 0.2,
            }
        }
        kwargs = build_llm_kwargs("gpt-4o-mini", models_config)
        assert kwargs["_provider"] == "openai"
        assert kwargs["api_key"] == "sk-test-key"
        assert kwargs["model_name"] == "gpt-4o-mini"

    def test_build_anthropic_kwargs(self):
        """核心：Anthropic 参数"""
        models_config = {
            "claude-sonnet": {
                "provider": "anthropic",
                "model_name": "claude-sonnet-4-20250514",
                "api_key": "sk-ant-test",
                "max_tokens": 8192,
                "temperature": 0.2,
            }
        }
        kwargs = build_llm_kwargs("claude-sonnet", models_config)
        assert kwargs["_provider"] == "anthropic"

    def test_build_openai_compatible_kwargs(self):
        """核心：兼容 OpenAI API 的提供商"""
        models_config = {
            "deepseek": {
                "provider": "openai_compatible",
                "model_name": "deepseek-v4-flash",
                "api_key": "sk-ds-test",
                "base_url": "https://api.deepseek.com",
                "max_tokens": 4096,
            }
        }
        kwargs = build_llm_kwargs("deepseek", models_config)
        # openai_compatible 映射到 openai provider
        assert kwargs["_provider"] == "openai"
        assert kwargs["base_url"] == "https://api.deepseek.com"

    def test_unknown_model_raises(self):
        """边界：未知模型报错"""
        with pytest.raises(ValueError, match="not found"):
            build_llm_kwargs("nonexistent-model", {})

    def test_unsupported_provider_raises(self):
        """边界：不支持的提供商"""
        models_config = {
            "test": {
                "provider": "unsupported_provider",
                "model_name": "test",
            }
        }
        with pytest.raises(ValueError, match="Unsupported provider"):
            build_llm_kwargs("test", models_config)


class TestConfigFiles:
    """测试配置文件存在性（非内容测试）"""

    def test_config_dir_exists(self):
        """基础：配置目录存在"""
        assert CONFIG_DIR.exists()

    def test_settings_yaml_exists(self):
        """基础：settings.yaml 存在"""
        assert (CONFIG_DIR / "settings.yaml").exists()

    def test_agents_yaml_exists(self):
        """基础：agents.yaml 存在"""
        assert (CONFIG_DIR / "agents.yaml").exists()

    def test_load_settings_returns_dict(self):
        """基础：load_settings 返回 dict"""
        settings = load_settings()
        assert isinstance(settings, dict)
        assert "project" in settings
