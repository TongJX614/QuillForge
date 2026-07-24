# -*- coding: utf-8 -*-
"""
System Under Test (SUT) — 被测系统的隔离封装

SUT = LLM 客户端 + Prompt 模板加载/渲染
Harness 编排器通过 SUT 接口调用 LLM，不直接接触 LLM 客户端或 Prompt 模板。
这样可以替换 SUT（如换 LLM 服务商、换 Prompt 策略）而不影响 Harness 编排逻辑。

从原 harness.py 迁移（现已重构为 QuillForge）：
  - LLMClient 类（generate / generate_stream / generate_json / generate_json_stream）
  - load_config / load_prompt / render_template 函数
  - 系统提示词渲染逻辑
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Generator

import yaml
from dotenv import load_dotenv
from openai import OpenAI

# 项目路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # quillforge/
PROMPTS_DIR = PROJECT_ROOT / "prompts"
CONFIG_DIR = PROJECT_ROOT / "config"


# ═══════════════════════════════════════════════════════
# 配置与 Prompt 加载（模块级函数）
# ═══════════════════════════════════════════════════════

def load_config() -> dict:
    """加载 quillforge_config.yaml"""
    config_path = CONFIG_DIR / "quillforge_config.yaml"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}


def load_prompt(filename: str) -> str:
    """加载 Prompt 模板文件"""
    path = PROMPTS_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def render_template(template: str, variables: dict) -> str:
    """
    简单的模板变量替换
    支持 {{variable}} 和 {{#if var}}...{{/if}} 和 {{#each arr}}...{{/each}}
    """
    result = template

    # 处理 {{#if var}}...{{#else}}...{{/if}}
    def replace_if(match):
        var_name = match.group(1).strip()
        then_block = match.group(2)
        else_block = match.group(3) if match.group(3) is not None else ""
        if variables.get(var_name):
            return then_block
        return else_block

    result = re.sub(
        r'\{\{#if\s+(\w+)\}\}(.*?)(?:\{\{#else\}\}(.*?))?\{\{/if\}\}',
        replace_if,
        result,
        flags=re.DOTALL,
    )

    # 处理 {{#each arr}}...{{/each}}
    def replace_each(match):
        var_name = match.group(1).strip()
        block = match.group(2)
        items = variables.get(var_name, [])
        parts = []
        for item in items:
            rendered = block
            # 统一转为 dict 处理，支持 dict / dataclass / 普通对象
            if isinstance(item, dict):
                item_dict = item
            elif hasattr(item, 'to_dict'):
                item_dict = item.to_dict()
            elif hasattr(item, '__dataclass_fields__'):
                item_dict = {k: getattr(item, k, '') for k in item.__dataclass_fields__}
            else:
                rendered = rendered.replace("{{this}}", str(item))
                parts.append(rendered)
                continue
            # 用 dict 的键值替换模板变量
            for k, v in item_dict.items():
                rendered = rendered.replace(f"{{{{this.{k}}}}}", str(v))
            # 清理空值行：删除只包含标签但值为空的行
            rendered = re.sub(r'(?m)^[\s]*[^{]*\{\{this\.\w+\}\}.*$', '', rendered)
            # 清理标签已替换但值为空的行（如 "  动机：\n" 或 "- 秘密：\n"）
            rendered = re.sub(r'(?m)^[\s]*[-•]?\s*[^：:\n]+[：:]\s*$', '', rendered)
            # 清理连续空行
            rendered = re.sub(r'\n{3,}', '\n\n', rendered)
            parts.append(rendered)
        return "\n".join(parts)

    result = re.sub(
        r'\{\{#each\s+(\w+)\}\}(.*?)\{\{/each\}\}',
        replace_each,
        result,
        flags=re.DOTALL,
    )

    # 处理简单变量 {{variable}}
    for key, value in variables.items():
        if isinstance(value, (str, int, float, bool)):
            result = result.replace(f"{{{{{key}}}}}", str(value))

    return result


# ═══════════════════════════════════════════════════════
# LLM 客户端
# ═══════════════════════════════════════════════════════

class LLMClient:
    """统一的 LLM 调用接口"""

    def __init__(self, config: dict):
        # 加载项目根目录下的 .env（支持 DEEPSEEK_API_KEY / OPENAI_API_KEY）
        env_path = PROJECT_ROOT.parent / ".env"
        load_dotenv(env_path)
        load_dotenv(PROJECT_ROOT / ".env")  # 也尝试加载 quillforge/.env

        llm_cfg = config.get("quillforge", {}).get("llm", {})
        self.model = llm_cfg.get("model", "deepseek-chat")
        self.temperature = llm_cfg.get("temperature", 0.8)
        self.max_tokens = llm_cfg.get("maxTokens", 3000)

        # 优先使用 DEEPSEEK_API_KEY，兼容 OPENAI_API_KEY
        api_key = (
            os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or llm_cfg.get("apiKey", "sk-placeholder")
        )
        api_base = llm_cfg.get("apiBase", "https://api.deepseek.com")

        self.client = OpenAI(api_key=api_key, base_url=api_base)

    def generate(self, system_prompt: str, user_prompt: str, model: str = "") -> str:
        """调用 LLM 生成文本"""
        response = self.client.chat.completions.create(
            model=model or self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return response.choices[0].message.content or ""

    def generate_stream(self, system_prompt: str, user_prompt: str, model: str = ""):
        """调用 LLM 并以流式逐块 yield 输出文本（用于旁白实时推送）"""
        stream = self.client.chat.completions.create(
            model=model or self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is not None and delta.content:
                yield delta.content

    def generate_json(self, system_prompt: str, user_prompt: str, model: str = "", _retries: int = 2) -> dict | list:
        """调用 LLM 并解析 JSON 输出，失败自动重试"""
        last_err = None
        for attempt in range(_retries + 1):
            raw = self.generate(system_prompt, user_prompt, model)
            # 尝试提取 JSON 块
            json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw)
            json_str = json_match.group(1).strip() if json_match else raw.strip()

            # 如果整体是数组或对象，直接解析
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                # 尝试找第一个 [ 或 { 到最后一个 ] 或 }
                for start_char, end_char in [('[', ']'), ('{', '}')]:
                    start = json_str.find(start_char)
                    end = json_str.rfind(end_char)
                    if start != -1 and end != -1 and end > start:
                        try:
                            return json.loads(json_str[start:end + 1])
                        except json.JSONDecodeError:
                            continue
                last_err = raw[:500]
                if attempt < _retries:
                    print(f"  [JSON RETRY] 解析失败，重试第 {attempt + 1} 次...")
                    continue
        raise ValueError(f"无法解析 LLM 输出为 JSON (重试 {_retries} 次):\n{last_err}")

    def generate_json_stream(self, system_prompt: str, user_prompt: str, model: str = ""):
        """流式调用 LLM，增量解析 JSON 数组并逐个 yield 其中的顶层对象。
        用于对话流式推送：每解析出一条完整对象立即 yield，无需等待整个数组生成完，
        从而消除"旁白播完后干等全部对话生成"的空白等待。
        采用括号深度 + 字符串状态机扫描（游标只前进，整体 O(n)），
        兼容 ```json 代码块包裹、数组括号以及对象内嵌套/转义引号。"""
        buf = ""
        pos = 0          # 已扫描游标
        depth = 0        # 当前对象嵌套深度
        in_string = False
        escape = False
        obj_start = -1   # 当前顶层对象起始位置
        for chunk in self.generate_stream(system_prompt, user_prompt, model):
            buf += chunk
            while pos < len(buf):
                ch = buf[pos]
                if in_string:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_string = False
                else:
                    if ch == '"':
                        in_string = True
                    elif ch == "{":
                        if depth == 0:
                            obj_start = pos
                        depth += 1
                    elif ch == "}":
                        if depth > 0:
                            depth -= 1
                            if depth == 0 and obj_start != -1:
                                obj_str = buf[obj_start:pos + 1]
                                try:
                                    yield json.loads(obj_str)
                                except json.JSONDecodeError:
                                    pass
                                obj_start = -1
                pos += 1


# ═══════════════════════════════════════════════════════
# SUT — System Under Test
# ═══════════════════════════════════════════════════════

class NarrativeSUT:
    """
    System Under Test：封装 LLM 客户端 + Prompt 模板加载/渲染。

    Harness 编排器通过 SUT 接口调用 LLM，实现：
    - SUT 可替换（换 LLM 服务商、换 Prompt 策略）
    - Prompt 模板统一管理
    - 系统提示词渲染逻辑集中
    """

    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.llm = LLMClient(self.config)
        # 加载所有 Prompt 模板
        self.prompts = {
            "system": load_prompt("system_prompt_v2.md"),
            "system_dynamic": load_prompt("system_prompt_v2.md"),
            "narration": load_prompt("narration_v2.md"),
            "dialogue": load_prompt("dialogue_v2.md"),
            "choices": load_prompt("choices_v2.md"),
        }

    def render_template(self, template: str, variables: dict) -> str:
        """渲染 Prompt 模板"""
        return render_template(template, variables)

    def render_system_prompt(self, extra: dict, harness_input) -> str:
        """构建系统提示词（融合世界书约束和叙事风格）"""
        sys_prompt_vars = {
            "narrativeStyle": extra.get("_narrativeStyle", "第三人称叙事"),
            "worldbookRules": extra.get("_worldbook", ""),
            "plotSummary": extra.get("_plotSummary", ""),
            "coreConflict": extra.get("_coreConflict", ""),
            "themes": extra.get("_themes", ""),
            "relationshipNetwork": extra.get("_relationshipNetwork", ""),
            "sceneAtmosphere": (
                extra.get("_sceneAtmosphere", "")
                or extra.get("_sceneDetails", "")[:200]
            ),
            "narrativeNotes": extra.get("_narrativeNotes", ""),
            "emotionalArc": extra.get("_emotionalArc", ""),
            "minDialogues": harness_input.options.dialogue_count_min,
            "maxDialogues": harness_input.options.dialogue_count_max,
        }
        return render_template(self.prompts["system_dynamic"], sys_prompt_vars)

    # ── LLM 调用委托 ──

    def call(self, system: str, user: str, model: str = "") -> str:
        """调用 LLM 生成文本"""
        return self.llm.generate(system, user, model)

    def call_stream(self, system: str, user: str, model: str = "") -> Generator[str, None, None]:
        """调用 LLM 流式生成"""
        yield from self.llm.generate_stream(system, user, model)

    def call_json(self, system: str, user: str, model: str = "", _retries: int = 2) -> dict | list:
        """调用 LLM 并解析 JSON 输出"""
        return self.llm.generate_json(system, user, model, _retries)

    def call_json_stream(self, system: str, user: str, model: str = ""):
        """调用 LLM 流式生成并增量解析 JSON 数组"""
        yield from self.llm.generate_json_stream(system, user, model)
