# -*- coding: utf-8 -*-
"""
MiMo TTS 语音合成模块（OpenAI 兼容 chat.completions + audio 参数 + 磁盘缓存）

基于小米 MiMo-V2.5-TTS 系列模型，通过 tokendance.space 网关调用。
支持两种模式：
  1. 音色克隆（mimo-v2.5-tts-voiceclone）：传入参考音频样本，复刻任意音色。
  2. 预置音色（mimo-v2.5-tts）：使用内置精品音色（冰糖/茉莉/苏打/白桦等）。
  当角色有参考音频时自动用克隆模式，否则回退到预置音色。

设计目标（与 image_gen.py 保持一致）：
- 磁盘缓存：同一 (speaker + text) 只合成一次，重玩/重开同一剧本不重复花钱。
- 未配置 TTS_API_KEY 时自动禁用，调用方回退到纯文字模式，游戏照常可玩。
- 角色音色映射：优先使用角色卡中的 voiceSample/voiceId 字段，其次用 TTS_VOICE_MAP
  环境变量，再按 gender 推断预置音色，最后回退到 TTS_DEFAULT_VOICE。
"""
from __future__ import annotations

import os
import re
import time
import base64
import hashlib
from pathlib import Path
from typing import Optional

# 加载项目 .env（与 image_gen.py 保持一致）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent  # quillforge
try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT.parent / ".env")
    load_dotenv(_PROJECT_ROOT / ".env")
except Exception:
    pass

# 生成音频的磁盘缓存目录
AUDIO_DIR = _PROJECT_ROOT / "generated_audio"

# 参考音频样本目录（用于音色克隆）
VOICE_SAMPLES_DIR = _PROJECT_ROOT / "voice_samples"

# MiMo 预置音色集合（非文件路径的 voiceId 视为预置音色名）
_PRESET_VOICES = {
    "mimo_default", "冰糖", "茉莉", "苏打", "白桦",
    "Mia", "Chloe", "Milo", "Dean",
}


class TTSGenerator:
    """MiMo TTS 客户端（带磁盘缓存与无 key 回退）"""

    def __init__(self):
        self.api_key = os.environ.get("TTS_API_KEY", "").strip()
        base_url = os.environ.get("TTS_BASE_URL", "").strip()
        self.base_url = base_url or None
        # 主模型（音色克隆）
        self.model = os.environ.get("TTS_MODEL", "mimo-v2.5-tts-voiceclone").strip()
        # 回退模型（预置音色，不需要参考音频）
        self.fallback_model = os.environ.get("TTS_FALLBACK_MODEL", "mimo-v2.5-tts").strip()
        # 默认预置音色
        self.default_voice = os.environ.get("TTS_DEFAULT_VOICE", "mimo_default").strip()
        # 默认男声/女声预置音色（角色卡有 gender 字段时自动选择）
        self.default_voice_male = os.environ.get("TTS_DEFAULT_VOICE_MALE", "苏打").strip()
        self.default_voice_female = os.environ.get("TTS_DEFAULT_VOICE_FEMALE", "冰糖").strip()
        # 按年龄分段的预置音色映射（环境变量可覆盖）
        # 格式: voice1,voice2,...（按年龄段顺序：少年/青年/中年/老年）
        self.voice_male_by_age = self._load_age_voices(
            "TTS_VOICE_MALE_BY_AGE", ["Milo", "苏打", "Dean", "白桦"]
        )
        self.voice_female_by_age = self._load_age_voices(
            "TTS_VOICE_FEMALE_BY_AGE", ["茉莉", "冰糖", "Mia", "Chloe"]
        )
        # 年龄分段阈值
        self.age_thresholds = self._load_age_thresholds()
        # 角色名 -> 音色ID/音频文件路径 映射（环境变量配置）
        self.voice_map = self._load_voice_map()
        # 初始化 OpenAI 客户端
        self.client = None
        if self.api_key:
            try:
                from openai import OpenAI
                self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            except Exception as e:
                print(f"[tts_gen] 初始化 TTS 客户端失败: {e}")
                self.client = None
        # 是否启用
        self.enabled = self.client is not None

    @staticmethod
    def _load_voice_map() -> dict:
        """加载角色->音色映射，格式: TTS_VOICE_MAP=角色名:voice_id,角色名:voice_id"""
        raw = os.environ.get("TTS_VOICE_MAP", "")
        result = {}
        for pair in raw.split(","):
            pair = pair.strip()
            if ":" in pair:
                name, vid = pair.split(":", 1)
                result[name.strip()] = vid.strip()
        return result

    @staticmethod
    def _load_age_voices(env_key: str, default_list: list) -> list:
        """加载按年龄分段的音色列表，格式: voice1,voice2,voice3,voice4
        顺序: 少年(<=18) / 青年(19-35) / 中年(36-55) / 老年(56+)
        """
        raw = os.environ.get(env_key, "")
        if not raw.strip():
            return default_list
        parts = [v.strip() for v in raw.split(",") if v.strip()]
        # 不足4个时用默认值补齐
        while len(parts) < 4:
            parts.append(default_list[len(parts)])
        return parts

    @staticmethod
    def _load_age_thresholds() -> list:
        """加载年龄分段阈值，格式: 18,35,55
        对应分段: <=18 / 19-35 / 36-55 / 56+
        """
        raw = os.environ.get("TTS_AGE_THRESHOLDS", "18,35,55")
        try:
            thresholds = [int(x.strip()) for x in raw.split(",") if x.strip()]
            if len(thresholds) >= 3:
                return thresholds[:3]
        except ValueError:
            pass
        return [18, 35, 55]

    def _voice_by_age(self, gender: str, age_str: str) -> Optional[str]:
        """根据性别和年龄返回对应的预置音色名。"""
        if not age_str:
            return None
        # 尝试提取数字年龄
        age_match = re.search(r"\d+", age_str)
        if not age_match:
            return None
        age = int(age_match.group())
        thresholds = self.age_thresholds  # [18, 35, 55]
        if age <= thresholds[0]:
            idx = 0  # 少年
        elif age <= thresholds[1]:
            idx = 1  # 青年
        elif age <= thresholds[2]:
            idx = 2  # 中年
        else:
            idx = 3  # 老年
        if gender == "male":
            return self.voice_male_by_age[idx]
        elif gender == "female":
            return self.voice_female_by_age[idx]
        return None

    def get_voice_id(self, speaker: str, characters: list) -> str:
        """根据角色名获取音色ID或音频文件路径。
        优先级: 角色卡 voiceSample > voiceId > TTS_VOICE_MAP 环境变量 > gender 推断 > 默认
        """
        # 1. 角色卡显式 voiceSample 字段（指向音频文件路径，用于音色克隆）
        char = next((c for c in characters if isinstance(c, dict) and c.get("name") == speaker), None)
        if char:
            voice_sample = str(char.get("voiceSample", "") or char.get("voice_sample", "")).strip()
            if voice_sample:
                return voice_sample

        # 2. 角色卡显式 voiceId 字段（预置音色名 或 音频文件路径）
        if char:
            voice_id = str(char.get("voiceId", "") or char.get("voice_id", "")).strip()
            if voice_id:
                return voice_id

        # 3. TTS_VOICE_MAP 环境变量
        if speaker in self.voice_map:
            return self.voice_map[speaker]

        # 4. 按 gender + age 推断预置音色
        if char:
            gender = str(char.get("gender", "")).strip().lower()
            age = str(char.get("age", "")).strip()
            if gender in ("male", "female"):
                voice = self._voice_by_age(gender, age)
                if voice:
                    return voice
                # age 无效时回退到默认男女声
                if gender == "male":
                    return self.default_voice_male
                elif gender == "female":
                    return self.default_voice_female

        # 5. 默认音色
        return self.default_voice

    def _resolve_voice_sample(self, voice_id: str) -> Optional[Path]:
        """尝试将 voice_id 解析为参考音频文件路径。
        如果 voice_id 不是预置音色名，尝试在 voice_samples/ 目录下查找音频文件。
        """
        if not voice_id or voice_id in _PRESET_VOICES:
            return None

        # 尝试多种路径组合
        candidates = [
            VOICE_SAMPLES_DIR / voice_id,
            _PROJECT_ROOT.parent / "voice_samples" / voice_id,
            VOICE_SAMPLES_DIR / f"{voice_id}.mp3",
            VOICE_SAMPLES_DIR / f"{voice_id}.wav",
            Path(voice_id),  # 绝对路径
        ]
        for c in candidates:
            try:
                if c.exists() and c.is_file() and c.suffix.lower() in (".mp3", ".wav"):
                    return c.resolve()
            except Exception:
                continue
        return None

    def _cache_path(self, script_slug: str, speaker: str, text: str, voice_id: str = "") -> Path:
        """缓存文件名含 (speaker|voice_id|text) 的哈希，扩展名 .wav（MiMo 返回 WAV 格式）
        voice_id 纳入哈希，避免更换音色后命中旧缓存（如从默认女声切换到男声）。
        """
        h = hashlib.md5(f"{speaker}|{voice_id}|{text}".encode("utf-8")).hexdigest()[:8]
        safe_speaker = re.sub(r"[^\w\u4e00-\u9fff]+", "_", str(speaker or "unknown")).strip("_") or "unknown"
        d = AUDIO_DIR / script_slug
        d.mkdir(parents=True, exist_ok=True)
        return d / f"tts_{safe_speaker}_{h}.wav"

    def generate(self, script_slug: str, speaker: str, text: str, voice_id: str = "") -> Optional[str]:
        """
        合成语音并返回 data URI（data:audio/wav;base64,...）。
        不写入磁盘缓存（LLM 每次生成内容不同，缓存无复用价值）。
        未启用或失败返回 None。
        """
        if not text.strip():
            return None

        # 如果未显式传入 voice_id，尝试从角色列表推断
        vid = voice_id or self.get_voice_id(speaker, [])

        if not self.enabled:
            return None

        # 网络瞬时故障自动重试
        max_attempts = 3
        last_err = None
        for attempt in range(1, max_attempts + 1):
            try:
                audio_data = self._call_tts_api(text, vid)
                if audio_data:
                    b64 = base64.b64encode(audio_data).decode("utf-8")
                    return f"data:audio/wav;base64,{b64}"
                else:
                    print(f"[tts_gen] {speaker}: API 返回空音频数据", flush=True)
                    return None
            except Exception as e:
                last_err = e
                if attempt < max_attempts:
                    wait = attempt * 2
                    print(f"[tts_gen] {speaker} TTS 合成异常，{wait}s 后重试({attempt}/{max_attempts}): {e}", flush=True)
                    time.sleep(wait)
                    continue

        print(f"[tts_gen] 合成失败 {speaker}: {type(last_err).__name__}: {last_err}", flush=True)
        return None

    def _call_tts_api(self, text: str, voice_id: str) -> Optional[bytes]:
        """
        调用 MiMo TTS API 合成语音，返回 WAV 音频二进制数据。

        API 使用 OpenAI 兼容的 chat.completions.create() 端点：
        - 合成文本放在 assistant 消息的 content 中
        - 风格指令可放在 user 消息的 content 中（可选）
        - audio.voice 参数指定音色：
          - 预置音色：直接传音色名（如 "冰糖"）
          - 音色克隆：传 "data:{mime};base64,{base64_audio}" 格式的参考音频

        当角色有参考音频文件时使用 mimo-v2.5-tts-voiceclone（音色克隆），
        否则回退到 mimo-v2.5-tts（预置音色）。
        """
        # 尝试解析 voice_id 为参考音频文件路径
        voice_sample_path = self._resolve_voice_sample(voice_id)

        if voice_sample_path:
            # ─── 音色克隆模式 ───
            sample_bytes = voice_sample_path.read_bytes()
            # 检查 base64 编码后是否超过 10MB 限制
            sample_b64_size = len(sample_bytes) * 4 // 3
            if sample_b64_size > 10 * 1024 * 1024:
                print(f"[tts_gen] 参考音频过大（{sample_b64_size // 1024 // 1024}MB > 10MB 限制），回退到预置音色", flush=True)
                voice_sample_path = None

        if voice_sample_path:
            # 音色克隆：使用 mimo-v2.5-tts-voiceclone
            mime = "audio/mpeg" if voice_sample_path.suffix.lower() == ".mp3" else "audio/wav"
            voice_b64 = base64.b64encode(sample_bytes).decode("utf-8")
            voice_param = f"data:{mime};base64,{voice_b64}"
            model = self.model
        else:
            # 预置音色：使用 mimo-v2.5-tts
            voice_param = voice_id if voice_id in _PRESET_VOICES else self.default_voice
            model = self.fallback_model

        # 构建 messages：合成文本放在 assistant 消息中
        messages = [
            {"role": "user", "content": ""},      # 风格指令（空=默认风格）
            {"role": "assistant", "content": text},  # 要合成的文本
        ]

        # 调用 API（非流式，返回完整 WAV）
        completion = self.client.chat.completions.create(
            model=model,
            messages=messages,
            audio={"format": "wav", "voice": voice_param},
        )

        # 从响应中提取音频数据
        message = completion.choices[0].message
        audio_data = getattr(getattr(message, "audio", None), "data", None)
        if not audio_data:
            # 兼容 dict 格式
            audio_obj = getattr(message, "audio", None)
            if isinstance(audio_obj, dict):
                audio_data = audio_obj.get("data", "")
        if not audio_data:
            print(f"[tts_gen] 响应中无 audio.data 字段", flush=True)
            return None

        return base64.b64decode(audio_data)
