# -*- coding: utf-8 -*-
"""
通用生图模块（OpenAI 兼容协议）

设计目标：
- 兼容任意 OpenAI 图像接口的服务：硅基流动（FLUX.1-schnell / Kolors）、智谱 CogView、
  OpenAI gpt-image-1 / DALL-E 3 等，只需在 .env 配置 IMAGE_API_KEY / IMAGE_BASE_URL / IMAGE_MODEL。
- 磁盘缓存：同一 (model + size + prompt) 只生成一次，重玩/重开同一剧本不重复花钱。
- 未配置 IMAGE_API_KEY 时自动禁用，调用方回退到占位图，游戏照常可玩。
"""
from __future__ import annotations

import os
import re
import time
import base64
import shutil
import hashlib
import urllib.request
from pathlib import Path

# 加载项目 .env（与 harness.py 保持一致：工作区根目录 + narrative-harness 目录）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent  # narrative-harness
try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT.parent / ".env")
    load_dotenv(_PROJECT_ROOT / ".env")
except Exception:
    pass

# 生成图片的磁盘缓存目录
IMAGES_DIR = _PROJECT_ROOT / "generated_images"

# 默认尺寸（可被 .env 覆盖）：背景横屏 16:9、立绘竖屏
# 1344x768 / 768x1344 同时被硅基流动 FLUX/Kolors 与智谱 CogView 支持
DEFAULT_BG_SIZE = os.environ.get("IMAGE_SIZE_BG", "1344x768").strip() or "1344x768"
DEFAULT_CHAR_SIZE = os.environ.get("IMAGE_SIZE_CHAR", "768x1344").strip() or "768x1344"

# 风格后缀：中文描述 + 英文标签，兼顾国产模型（Kolors/CogView 中文强）与 FLUX（英文强）
STYLE_SUFFIX_CHAR = (
    "，日系动漫风格，视觉小说角色立绘，精致细节，电影级光影，"
    "全身像，居中构图，简洁纯色背景，无文字，无水印, "
    "anime style, visual novel character sprite, full body, centered, plain background, high quality, detailed"
)
STYLE_SUFFIX_BG = (
    "，日系动漫风格，视觉小说场景背景，精致细节，电影级光影，"
    "宽银幕构图，无人物，无文字，无水印, "
    "anime style, visual novel background art, scenic, wide shot, no people, high quality, detailed"
)


def slugify(title: str) -> str:
    """把剧本标题转成安全的目录名（保留中文/字母数字，其余替换为下划线）"""
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "_", str(title or "")).strip("_")
    return s or "script"


def build_character_prompt(char: dict) -> str:
    """从角色数据构建生图提示词（name + age + occupation + appearance 外貌描述）"""
    name = str(char.get("name", "")).strip()
    appearance = str(char.get("appearance", "")).strip() or "（无外貌描述，按角色气质自由发挥）"
    age = str(char.get("age", "")).strip()
    age_str = (f"{age}岁" if age.isdigit() else age) if age else ""
    identity = str(char.get("occupation", "")).strip() or str(char.get("role", "")).strip()
    parts = [f"角色：{name}"] + ([age_str] if age_str else []) + ([identity] if identity else [])
    head = "，".join(parts)
    return f"{head}。外貌：{appearance}{STYLE_SUFFIX_CHAR}"


def build_background_prompt(scene: dict) -> str:
    """从场景数据构建生图提示词（场景名/地点/氛围/画面）。
    兼容两种数据源：素材清单场景素材（mood/details）与游戏场景（atmosphere/description）。"""
    name = str(scene.get("name", "")).strip()
    location = str(scene.get("location", "")).strip()
    mood = str(scene.get("mood", "") or scene.get("atmosphere", "")).strip()
    details = str(scene.get("details", "") or scene.get("description", "")).strip()
    if len(details) > 200:
        details = details[:200] + "…"
    parts = [
        f"场景：{name}" if name else "",
        f"地点：{location}" if location else "",
        f"氛围：{mood}" if mood else "",
        f"画面：{details}" if details else "",
    ]
    head = "，".join(p for p in parts if p)
    return f"{head}{STYLE_SUFFIX_BG}"


class ImageGenerator:
    """OpenAI 兼容的生图客户端（带磁盘缓存与无 key 回退）"""

    def __init__(self):
        self.api_key = os.environ.get("IMAGE_API_KEY", "").strip()
        base_url = os.environ.get("IMAGE_BASE_URL", "").strip()
        self.base_url = base_url or None
        self.model = os.environ.get("IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell").strip()
        # 可选：画质（hd/standard，空则用模型默认）
        self.quality = os.environ.get("IMAGE_QUALITY", "").strip()
        # 可选：水印开关（仅部分服务商支持，如智谱需先签免责声明才能关）；空则不传、用服务商默认
        wm = os.environ.get("IMAGE_WATERMARK", "").strip().lower()
        self.watermark_enabled = None
        if wm in ("false", "0", "off", "no"):
            self.watermark_enabled = False
        elif wm in ("true", "1", "on", "yes"):
            self.watermark_enabled = True
        self.client = None
        if self.api_key:
            try:
                from openai import OpenAI
                self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            except Exception as e:  # pragma: no cover
                print(f"[image_gen] 初始化生图客户端失败: {e}")
                self.client = None

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def _cache_path(self, script_slug: str, kind: str, asset_id: str, prompt: str, size: str) -> Path:
        """缓存文件名含 (model|size|prompt) 的哈希：提示词/模型变化时自动重新生成"""
        h = hashlib.md5(f"{self.model}|{size}|{prompt}".encode("utf-8")).hexdigest()[:8]
        safe_id = re.sub(r"[^\w\u4e00-\u9fff]+", "_", str(asset_id or "x")).strip("_") or "x"
        d = IMAGES_DIR / script_slug
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{kind}_{safe_id}_{h}.png"

    def generate(self, script_slug: str, kind: str, asset_id: str, prompt: str, size: str) -> str | None:
        """
        生成图片并缓存到磁盘。
        返回可访问的 URL 路径（/generated_images/<slug>/<file>.png）；未启用或失败返回 None。
        """
        path = self._cache_path(script_slug, kind, asset_id, prompt, size)
        rel_url = f"/generated_images/{script_slug}/{path.name}"

        # 命中缓存：直接复用，不重复调用 API
        if path.exists() and path.stat().st_size > 0:
            return rel_url

        if not self.enabled:
            return None

        kwargs = dict(model=self.model, prompt=prompt, size=size, n=1)
        if self.quality:
            kwargs["quality"] = self.quality
        extra_body = {}
        if self.watermark_enabled is not None:
            extra_body["watermark_enabled"] = self.watermark_enabled
        if extra_body:
            kwargs["extra_body"] = extra_body

        # 网络瞬时故障（连接失败/超时）自动重试，提升弱网环境成功率
        try:
            from openai import APIConnectionError, APITimeoutError
            retryable = (APIConnectionError, APITimeoutError)
        except Exception:  # pragma: no cover
            retryable = ()
        max_attempts = 3
        last_err = None
        for attempt in range(1, max_attempts + 1):
            try:
                resp = self.client.images.generate(**kwargs)
                item = resp.data[0]
                b64 = getattr(item, "b64_json", None)
                url = getattr(item, "url", None)
                if b64:
                    path.write_bytes(base64.b64decode(b64))
                elif url:
                    tmp_path, _ = urllib.request.urlretrieve(url)
                    shutil.copyfile(tmp_path, path)
                else:
                    print(f"[image_gen] {kind}/{asset_id}: 响应中既无 b64_json 也无 url", flush=True)
                    return None
                return rel_url
            except retryable as e:
                last_err = e
                if attempt < max_attempts:
                    wait = attempt * 2
                    print(f"[image_gen] {kind}/{asset_id} 连接异常，{wait}s 后重试({attempt}/{max_attempts}): {e}", flush=True)
                    time.sleep(wait)
                    continue
            except Exception as e:
                last_err = e
                break

        msg = f"[image_gen] 生成失败 {kind}/{asset_id}: {type(last_err).__name__}: {last_err}"
        print(msg, flush=True)
        try:
            with open(IMAGES_DIR / "error.log", "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass
        return None
