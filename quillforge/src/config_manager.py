# -*- coding: utf-8 -*-
"""
统一配置管理 — 消除散落在各模块中的 load_dotenv + os.environ.get

使用 pydantic-settings 从 .env 文件和环境变量加载配置，
提供单一入口供所有模块使用。
"""

from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings
from pydantic import Field

# 项目路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # quillforge/
WORKSPACE_ROOT = PROJECT_ROOT.parent  # QuillForge workspace root


class QuillForgeSettings(BaseSettings):
    """QuillForge 全局配置

    优先级：环境变量 > .env 文件 > 默认值
    """

    # ── LLM 配置 ──
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    deepseek_model: str = Field(default="deepseek-chat", alias="DEEPSEEK_MODEL")
    script_gen_model: str = Field(default="deepseek-v4-flash", alias="SCRIPT_GEN_MODEL")

    # ── LLM 调用参数 ──
    llm_temperature: float = 0.8
    llm_max_tokens: int = 3000
    llm_timeout: float = 90.0
    llm_connect_timeout: float = 10.0

    # ── 剧本生成专用参数 ──
    script_gen_temperature: float = 0.85
    script_gen_max_tokens: int = 8000
    script_gen_timeout: float = 120.0

    # ── 实时引擎专用参数 ──
    realtime_temperature: float = 0.85
    realtime_max_tokens: int = 4000
    realtime_timeout: float = 180.0

    # ── 服务器配置 ──
    server_host: str = Field(default="127.0.0.1", alias="SERVER_HOST")
    server_port: int = Field(default=8050, alias="SERVER_PORT")
    session_ttl_seconds: int = Field(default=1800, alias="SESSION_TTL_SECONDS")

    # ── 图片生成配置 ──
    image_concurrency: int = Field(default=8, alias="IMAGE_CONCURRENCY")
    image_bg_preload_count: int = Field(default=2, alias="IMAGE_BG_PRELOAD_COUNT")
    image_remove_bg: str = Field(default="true", alias="IMAGE_REMOVE_BG")
    image_size_bg: str = Field(default="1344x768", alias="IMAGE_SIZE_BG")
    image_size_char: str = Field(default="768x1344", alias="IMAGE_SIZE_CHAR")

    # ── 会话配置 ──
    session_ttl_minutes: int = 30
    session_max_size: int = 100
    realtime_session_max_size: int = 50

    # ── 安全配置 ──
    quillforge_api_key: str = Field(default="", alias="QUILLFORGE_API_KEY")
    rate_limit_per_minute: int = 10

    # ── TTS 配置 ──
    tts_api_key: str = Field(default="", alias="TTS_API_KEY")
    tts_base_url: str = Field(default="", alias="TTS_BASE_URL")
    tts_model: str = Field(default="mimo-v2.5-tts-voiceclone", alias="TTS_MODEL")
    tts_fallback_model: str = Field(default="mimo-v2.5-tts", alias="TTS_FALLBACK_MODEL")
    tts_default_voice: str = Field(default="mimo_default", alias="TTS_DEFAULT_VOICE")
    tts_default_voice_male: str = Field(default="苏打", alias="TTS_DEFAULT_VOICE_MALE")
    tts_default_voice_female: str = Field(default="冰糖", alias="TTS_DEFAULT_VOICE_FEMALE")
    tts_voice_map: str = Field(default="", alias="TTS_VOICE_MAP")
    tts_voice_male_by_age: str = Field(default="", alias="TTS_VOICE_MALE_BY_AGE")
    tts_voice_female_by_age: str = Field(default="", alias="TTS_VOICE_FEMALE_BY_AGE")
    tts_age_thresholds: str = Field(default="18,35,55", alias="TTS_AGE_THRESHOLDS")

    # ── 图片 API 配置 ──
    image_api_key: str = Field(default="", alias="IMAGE_API_KEY")
    image_base_url: str = Field(default="", alias="IMAGE_BASE_URL")
    image_model: str = Field(default="black-forest-labs/FLUX.1-schnell", alias="IMAGE_MODEL")
    image_quality: str = Field(default="", alias="IMAGE_QUALITY")
    image_watermark: str = Field(default="", alias="IMAGE_WATERMARK")

    model_config = {
        "env_file": [
            str(WORKSPACE_ROOT / ".env"),
            str(PROJECT_ROOT / ".env"),
        ],
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "populate_by_name": True,
    }

    @property
    def effective_api_key(self) -> str:
        """获取有效的 API Key（优先 DEEPSEEK，兼容 OPENAI）"""
        return self.deepseek_api_key or self.openai_api_key or "sk-placeholder"


@lru_cache()
def get_settings() -> QuillForgeSettings:
    """获取全局配置单例"""
    return QuillForgeSettings()
