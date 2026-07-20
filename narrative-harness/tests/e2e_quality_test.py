# -*- coding: utf-8 -*-
"""
端到端生成质量测试脚本
对 回响-DEMO 剧本进行 8 轮生成测试（2 个会话 x 4 个场景）
"""
import json
import time
import requests

BASE = "http://127.0.0.1:8767"

# 每个会话的选择路径
SESSION_PATHS = [
    ["立即解密附件", "继续深入", "公开真相", ""],       # 会话A
    ["先调查发件人", "停止修复", "达成协议", ""],       # 会话B
]

EXPECTED_CHOICES = {
    "scene_01": ["立即解密附件", "先调查发件人", "删除邮件"],
    "scene_02": ["继续深入", "停止修复"],
    "scene_03": ["公开真相", "达成协议", "埋下伏笔"],
    "scene_04": [],  # 结局场景可能无选项
}

VALID_SPEAKERS = {"苏然", "林晓", "杜维明"}

results = []


def load_script():
    r = requests.post(f"{BASE}/api/load-folder", json={"path": "samples/回响-DEMO"})
    r.raise_for_status()
    return r.json()["script_id"]


def start_session(script_id):
    r = requests.post(f"{BASE}/api/start", params={"script_id": script_id})
    r.raise_for_status()
    return r.json()["session_id"]


def generate_stream(session_id):
    """调用 SSE 流式生成，收集全部事件"""
    t0 = time.time()
    r = requests.post(f"{BASE}/api/session/{session_id}/generate-stream", stream=True)
    r.raise_for_status()

    narration = ""
    dialogues = []
    choices = []
    worldline_state = {}
    validation = {}
    meta = {}
    events_log = []

    for line in r.iter_lines():
        if not line:
            continue
        text = line.decode("utf-8")
        if text.startswith("data:"):
            payload = text[5:].strip()
            try:
                evt = json.loads(payload)
            except json.JSONDecodeError:
                continue
            etype = evt.get("event", "")
            events_log.append(etype)
            if etype == "narration":
                narration = evt.get("text", "")
            elif etype == "dialogue":
                dialogues.append({
                    "speaker": evt.get("speaker", ""),
                    "text": evt.get("text", ""),
                    "emotion": evt.get("emotion", ""),
                })
            elif etype == "choices":
                choices = evt.get("choices", [])
            elif etype == "done":
                result = evt.get("result", {})
                worldline_state = result.get("data", {}).get("worldlineState", {})
                validation = result.get("validation", {})
            elif etype == "meta":
                meta = evt

    elapsed = int((time.time() - t0) * 1000)
    return {
        "narration": narration,
        "dialogues": dialogues,
        "choices": choices,
        "worldline_state": worldline_state,
        "validation": validation,
        "meta": meta,
        "elapsed_ms": elapsed,
        "events": events_log,
    }


def choose(session_id, choice_text, choice_index=0):
    r = requests.post(f"{BASE}/api/session/{session_id}/choose", json={
        "choice_text": choice_text,
        "choice_index": choice_index,
    })
    r.raise_for_status()
    return r.json()


def check_speakers(dialogues):
    """检查 speaker 合法性"""
    invalid = [d["speaker"] for d in dialogues if d["speaker"] not in VALID_SPEAKERS]
    return invalid


def check_choices(choices, scene_id):
    """检查选项是否与剧本一致"""
    expected = EXPECTED_CHOICES.get(scene_id, [])
    if not expected:
        return True, []
    actual_texts = [c.get("text", "") for c in choices]
    matched = [e for e in expected if e in actual_texts]
    return len(matched) == len(expected), actual_texts


def run_session(script_id, path, session_label):
    """运行一个完整会话（4 个场景）"""
    session_id = start_session(script_id)
    print(f"\n{'='*60}")
    print(f"会话 {session_label} | session_id={session_id} | 路径={path}")
    print(f"{'='*60}")

    for scene_idx in range(4):
        scene_id = f"scene_0{scene_idx+1}"
        print(f"\n--- [{session_label}] {scene_id} 生成中... ---")

        gen = generate_stream(session_id)

        # 检查 speaker
        invalid_speakers = check_speakers(gen["dialogues"])

        # 检查选项
        choices_ok, actual_choices = check_choices(gen["choices"], scene_id)

        # 获取 meta 中的场景信息
        scene_meta = gen["meta"].get("scene_meta", {})

        record = {
            "session": session_label,
            "scene_id": scene_id,
            "scene_name": scene_meta.get("scene_name", ""),
            "elapsed_ms": gen["elapsed_ms"],
            "narration": gen["narration"],
            "dialogues": gen["dialogues"],
            "choices": gen["choices"],
            "worldline_state": gen["worldline_state"],
            "validation_passed": gen["validation"].get("passed", False),
            "invalid_speakers": invalid_speakers,
            "choices_match": choices_ok,
            "actual_choices": actual_choices,
            "events": gen["events"],
        }
        results.append(record)

        # 打印摘要
        ws = gen["worldline_state"]
        print(f"  耗时: {gen['elapsed_ms']}ms")
        print(f"  旁白: {gen['narration'][:80]}...")
        print(f"  对话: {len(gen['dialogues'])} 轮")
        for d in gen["dialogues"][:3]:
            print(f"    {d['speaker']}: {d['text'][:50]}...")
        print(f"  选项: {[c.get('text','') for c in gen['choices']]}")
        print(f"  世界线: node={ws.get('currentNode','')} idx={ws.get('nodeIndex','')} progress={ws.get('progress','')}")
        print(f"  验证: {gen['validation'].get('passed', False)}")
        print(f"  非法speaker: {invalid_speakers if invalid_speakers else '无'}")
        print(f"  选项匹配: {choices_ok}")

        # 选择并推进
        choice_text = path[scene_idx]
        if choice_text and scene_idx < 3:
            # 找到选项索引
            choice_idx = 0
            for i, c in enumerate(gen["choices"]):
                if c.get("text", "") == choice_text:
                    choice_idx = i
                    break
            resp = choose(session_id, choice_text, choice_idx)
            print(f"  选择: '{choice_text}' -> state={resp.get('session_state','')}")
        elif scene_idx == 3:
            print(f"  (结局场景，无需选择)")


def main():
    print("=" * 60)
    print("端到端生成质量测试 - 回响-DEMO")
    print("=" * 60)

    script_id = load_script()
    print(f"剧本加载成功: {script_id}")

    # 会话A
    run_session(script_id, SESSION_PATHS[0], "A")
    # 会话B
    run_session(script_id, SESSION_PATHS[1], "B")

    # 保存完整结果
    with open("e2e_test_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n\n{'='*60}")
    print(f"测试完成: 共 {len(results)} 轮")
    print(f"结果已保存到 e2e_test_results.json")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
