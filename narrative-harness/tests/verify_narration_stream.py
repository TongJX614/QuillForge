# -*- coding: utf-8 -*-
"""验证旁白流式输出：测量首个 narration_delta 延迟 vs 完整旁白到达时间"""
import json
import time
import requests

BASE = "http://127.0.0.1:8767"

r = requests.post(f"{BASE}/api/load-folder", json={"path": "samples/回响-DEMO"})
sid = r.json()["script_id"]
r2 = requests.post(f"{BASE}/api/start", params={"script_id": sid})
sess = r2.json()["session_id"]
print(f"session: {sess}")

t0 = time.time()
r3 = requests.post(f"{BASE}/api/session/{sess}/generate-stream", stream=True)

first_delta_t = None
narr_complete_t = None
n_deltas = 0
narr_text = ""
accumulated = ""

for line in r3.iter_lines():
    if not line:
        continue
    text = line.decode("utf-8")
    if not text.startswith("data:"):
        continue
    try:
        evt = json.loads(text[5:].strip())
    except json.JSONDecodeError:
        continue
    etype = evt.get("event", "")
    if etype == "narration_start":
        print(f"[{int((time.time()-t0)*1000)}ms] narration_start")
    elif etype == "narration_delta":
        if first_delta_t is None:
            first_delta_t = time.time() - t0
        n_deltas += 1
        accumulated += evt.get("delta", "")
    elif etype == "narration":
        narr_complete_t = time.time() - t0
        narr_text = evt.get("text", "")
        break  # 拿到完整旁白即可

print(f"\n=== 旁白流式验证结果 ===")
print(f"首个增量延迟: {int(first_delta_t*1000) if first_delta_t else 'N/A'}ms")
print(f"完整旁白到达: {int(narr_complete_t*1000) if narr_complete_t else 'N/A'}ms")
print(f"增量块数量: {n_deltas}")
print(f"增量累积长度: {len(accumulated)} 字")
print(f"最终旁白长度: {len(narr_text)} 字")
print(f"累积与最终一致: {accumulated.strip() == narr_text.strip() or accumulated == narr_text}")
print(f"\n旁白前80字: {narr_text[:80]}...")

if first_delta_t and narr_complete_t:
    print(f"\n结论: 用户在 {int(first_delta_t*1000)}ms 即可看到旁白开始显示（而非等待 {int(narr_complete_t*1000)}ms）")
