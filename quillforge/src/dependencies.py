# -*- coding: utf-8 -*-
"""
依赖注入 / 单例管理 — 从 server.py 提取

管理 Harness、ImageGenerator、TTSGenerator 的懒加载单例，
以及会话存储（sessions、realtime_sessions、uploaded_scripts）。
会话使用 TTLCache 自动过期（默认 30 分钟），过期时自动释放线程池资源。
"""

from __future__ import annotations

import threading
from typing import Optional

from cachetools import TTLCache

from quillforge import Harness, create_default_harness
from image_gen import ImageGenerator
from tts_gen import TTSGenerator
from realtime_engine import RealtimeStorySession
from session_manager import GameSession
from logger import get_logger
from config_manager import get_settings

logger = get_logger("dependencies")


def _get_session_ttl() -> int:
    """获取会话 TTL（秒），优先从 get_settings() 获取，失败时回退到环境变量"""
    try:
        return get_settings().session_ttl_seconds
    except Exception as e:
        logger.warning("[dependencies] 无法从配置获取 session TTL，使用环境变量回退值: %s", e)
        return int(__import__("os").getenv("SESSION_TTL_SECONDS", "1800"))


SESSION_TTL = _get_session_ttl()


class _LockedTTLCache(TTLCache):
    """线程安全的 TTLCache。

    cachetools 非线程安全：FastAPI 同步路由在线程池中并发调用 get/__setitem__
    会同时触发 expire() 内部突变。用 RLock 串行化所有公开访问。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = threading.RLock()

    def __getitem__(self, key):
        with self._lock:
            return super().__getitem__(key)

    def __setitem__(self, key, value):
        with self._lock:
            super().__setitem__(key, value)

    def __delitem__(self, key):
        with self._lock:
            super().__delitem__(key)

    def __contains__(self, key):
        with self._lock:
            return super().__contains__(key)

    def get(self, key, default=None):
        with self._lock:
            return super().get(key, default)

    def pop(self, key, *args):
        with self._lock:
            return super().pop(key, *args)

    def expire(self, time=None):
        with self._lock:
            return super().expire(time)

    def keys(self):
        with self._lock:
            return list(super().keys())

    def items(self):
        with self._lock:
            return list(super().items())

    def __len__(self):
        with self._lock:
            return super().__len__()


class _RealtimeSessionCache(_LockedTTLCache):
    """TTLCache 子类：过期淘汰时自动关闭 RealtimeStorySession 的线程池。

    注意：cachetools 的 TTLCache.expire() 内部直接调用基类 Cache.__delitem__，
    会绕过子类覆写的 __delitem__，因此必须同时覆写 expire() 才能
    在惰性过期路径（get/__getitem__/__contains__）上正确释放资源。
    """

    def _close_session(self, key, value) -> None:
        if value is not None and hasattr(value, "close"):
            try:
                value.close()
                logger.info(f"实时会话 {key} 过期，已释放线程池资源")
            except Exception as e:
                logger.warning("[dependencies] 会话 %s 过期时关闭线程池异常: %s", key, e)

    def __delitem__(self, key):
        value = self.get(key)
        super().__delitem__(key)
        self._close_session(key, value)

    def expire(self, time=None):
        """覆写惰性过期逻辑，对每个被淘汰的会话执行 close()

        cachetools >= 6 的 TTLCache.expire 签名变为 expire(self, time=None)，
        且 __setitem__ 内部会以 self.expire(time) 调用，必须透传该参数。
        """
        expired_items = list(super().expire(time))
        for key, value in expired_items:
            self._close_session(key, value)
        return expired_items


# ── 会话存储（TTL 自动过期，线程安全）──
sessions: _LockedTTLCache = _LockedTTLCache(maxsize=100, ttl=SESSION_TTL)
realtime_sessions: _RealtimeSessionCache = _RealtimeSessionCache(maxsize=50, ttl=SESSION_TTL)
uploaded_scripts: dict[str, dict] = {}  # script_id -> {path, adapter, summary}
_loaded_stories: dict[str, dict] = {}  # path -> story data (轻量 API 缓存)

# ── 单例 ──
_harness: Optional[Harness] = None
_harness_lock = threading.Lock()
_image_generator: Optional[ImageGenerator] = None
_tts_generator: Optional[TTSGenerator] = None


def get_harness() -> Harness:
    global _harness
    if _harness is None:
        with _harness_lock:
            if _harness is None:
                _harness = create_default_harness()
    return _harness


def get_image_generator() -> ImageGenerator:
    """生图器单例（未配置 IMAGE_API_KEY 时 enabled=False，调用方回退占位图）"""
    global _image_generator
    if _image_generator is None:
        _image_generator = ImageGenerator()
    return _image_generator


def get_tts_generator() -> TTSGenerator:
    """TTS 语音合成器单例（未配置 TTS_API_KEY 时 enabled=False，回退纯文字模式）"""
    global _tts_generator
    if _tts_generator is None:
        _tts_generator = TTSGenerator()
    return _tts_generator
