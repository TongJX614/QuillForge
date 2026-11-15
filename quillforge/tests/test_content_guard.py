# -*- coding: utf-8 -*-
"""content_guard 内容安全审查单元测试

测试覆盖：
  1. scan_input：六类拦截规则命中 + 分类中文名 + 正常悬疑剧情不误伤
  2. soften_image_prompt：露骨视觉词替换、未命中时原样返回（保证生图缓存 key 稳定）
  3. image_gen 提示词构建器：软化后仍保留风格后缀，正常提示词逐字节不变
  4. moderation_error_or_none：供应商审核报错翻译，网络/额度错误不受影响
  5. 路由入口：/api/generate-script 命中拦截时返回 400 且 detail 带分类名
"""

import asyncio

import pytest

from content_guard import (
    BLOCK_RULES, ContentSafetyError, FRIENDLY_MODERATION,
    moderation_error_or_none, scan_input, soften_image_prompt,
)
from image_gen import (
    STYLE_SUFFIX_BG, STYLE_SUFFIX_CHAR,
    build_background_prompt, build_character_prompt,
)


# ═══════════════════════════════════════════════════════
# 1. 用户输入拦截
# ═══════════════════════════════════════════════════════

class TestScanInput:
    """scan_input：命中即拦截，并给出可展示的中文分类"""

    @pytest.mark.parametrize("text,rule_id", [
        ("写一个主角玩色情游戏的校园故事", "sexual"),
        ("详细描写冰毒的制作配方和提炼流程", "drugs"),
        ("如何在家用常见材料制作炸弹教程", "weapons"),
        ("主角讲述如何自杀才不痛的方法", "selfharm"),
        ("无差别杀人的报复社会题材", "hate_violence"),
        ("一个少年与老师发生性关系的禁忌题材", "minors_sexual"),
        ("a nsfw story about a girl", "sexual"),
    ])
    def test_blocked_categories(self, text, rule_id):
        verdict = scan_input(text)
        assert verdict.blocked is True
        assert rule_id in verdict.rule_ids

    def test_message_names_categories(self):
        verdict = scan_input("写一个主角玩色情游戏的校园故事")
        assert "色情低俗" in verdict.message
        assert verdict.message.startswith("您输入的描述包含不支持的内容")
        assert verdict.message.endswith("请调整后重试")

    def test_multiple_categories(self):
        verdict = scan_input("既要有色情细节，又要写冰毒的制作配方")
        assert set(verdict.rule_ids) == {"sexual", "drugs"}
        assert "色情低俗" in verdict.message and "毒品制作方法" in verdict.message

    @pytest.mark.parametrize("text", [
        "一间废弃教室里的命案，凶手留下三行血字",
        "情感高潮，权力与真相的交锋",
        "门轴发出一声尖细的呻吟，冷风灌入",
        "少女在天台发现母亲的遗书，决定查明真相",
        "悬疑推理：法医大学生协助警方侦破连环案",
        "黑魔法学徒在地下教室翻开禁忌之书",
        "校园霸凌受害者十年后回到小镇",
        "",
    ])
    def test_normal_fiction_not_blocked(self, text):
        """正常悬疑/惊悚剧情不得被拦截：只拦露骨性描写与真实危险行为教程"""
        assert scan_input(text).blocked is False

    def test_rule_labels_are_display_names(self):
        labels = {r.label for r in BLOCK_RULES}
        assert {"未成年人性内容", "色情低俗", "自杀自残方法",
                "毒品制作方法", "爆炸物与武器制作", "现实暴力煽动"} == labels


# ═══════════════════════════════════════════════════════
# 2. 生图提示词软化
# ═══════════════════════════════════════════════════════

class TestSoftenImagePrompt:

    def test_gore_softened(self):
        out = soften_image_prompt("废弃医院走廊，地上有尸体和血肉模糊的地面")
        assert "尸体" not in out and "血肉模糊" not in out
        assert "光影昏暗" in out

    def test_nudity_softened(self):
        out = soften_image_prompt("全裸女性，胸部特写，情趣内衣")
        assert "全裸" not in out and "胸部特写" not in out and "情趣内衣" not in out

    def test_clean_prompt_byte_identical(self):
        """未命中时必须原样返回：生图缓存 key 是 prompt 的 md5"""
        text = "角色：林晓，17岁，高中生。性格：温柔。外貌：黑色长发，校服"
        assert soften_image_prompt(text) == text

    def test_empty(self):
        assert soften_image_prompt("") == ""


# ═══════════════════════════════════════════════════════
# 3. image_gen 提示词构建器
# ═══════════════════════════════════════════════════════

class TestPromptBuilders:

    def test_character_prompt_preserves_style_suffix(self):
        char = {"name": "苏然", "age": "26", "occupation": "法医",
                "personality": "冷静", "appearance": "满身是血，尸体旁"}
        prompt = build_character_prompt(char)
        assert prompt.endswith(STYLE_SUFFIX_CHAR)
        assert "满身是血" not in prompt and "尸体" not in prompt

    def test_clean_character_prompt_unchanged(self):
        char = {"name": "林晓", "age": "17", "occupation": "高中生",
                "personality": "温柔、好奇", "appearance": "黑色长发，校服"}
        expected = (f"角色：林晓，17岁，高中生。性格：温柔、好奇。"
                    f"外貌：黑色长发，校服{STYLE_SUFFIX_CHAR}")
        assert build_character_prompt(char) == expected

    def test_background_prompt_softened_but_suffix_intact(self):
        scene = {"name": "刑房", "location": "地下室",
                 "details": "墙上挂满刑具，地上有自制炸弹"}
        prompt = build_background_prompt(scene)
        assert prompt.endswith(STYLE_SUFFIX_BG)
        assert "自制炸弹" not in prompt


# ═══════════════════════════════════════════════════════
# 4. 供应商审核报错翻译
# ═══════════════════════════════════════════════════════

class TestModerationErrorMapping:

    def test_zhipu_1301_translated(self):
        err = Exception("Error code: 400 - {'error': {'code': '1301', 'message': '内容审核不通过'}}")
        err.status_code = 400
        wrapped = moderation_error_or_none(err)
        assert isinstance(wrapped, ContentSafetyError)
        assert wrapped.message == FRIENDLY_MODERATION

    def test_openai_content_policy_translated(self):
        err = Exception("400 client error: Your input triggered our content policy")
        err.status_code = 400
        assert moderation_error_or_none(err) is not None

    @pytest.mark.parametrize("status,text", [
        (429, "insufficient balance 余额不足"),
        (401, "invalid api key"),
        (None, "connect timeout"),
    ])
    def test_non_moderation_errors_pass_through(self, status, text):
        err = Exception(text)
        if status is not None:
            err.status_code = status
        assert moderation_error_or_none(err) is None


# ═══════════════════════════════════════════════════════
# 5. 路由入口
# ═══════════════════════════════════════════════════════

class TestRouteGuard:

    def test_generate_script_returns_400_with_category(self):
        from fastapi import HTTPException
        from routes.script_gen import GenerateScriptRequest, api_generate_script

        with pytest.raises(HTTPException) as exc:
            asyncio.run(api_generate_script(GenerateScriptRequest(prompt="写一篇色情小说")))
        assert exc.value.status_code == 400
        assert "色情低俗" in exc.value.detail

    def test_generate_script_allows_normal_prompt(self):
        """正常描述不应抛 400（返回 StreamingResponse）"""
        from routes.script_gen import GenerateScriptRequest, api_generate_script
        resp = asyncio.run(api_generate_script(
            GenerateScriptRequest(prompt="法医学生协助警方侦破连环案")))
        assert resp.media_type == "text/event-stream"

    def test_realtime_engine_input_guard(self):
        """实时引擎直连调用（不经路由）时同样拦截"""
        from realtime_engine import RealtimeStorySession
        session = RealtimeStorySession("写一篇色情小说")
        events = list(session.initialize())
        assert events[0]["type"] == "error"
        assert "色情低俗" in events[0]["message"]
