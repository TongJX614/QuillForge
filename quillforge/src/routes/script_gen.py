# -*- coding: utf-8 -*-
"""剧本生成路由：/api/generate-script"""

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from generic_adapter import GenericScriptAdapter
from script_generator import generate_script
from dependencies import uploaded_scripts
from logger import get_logger

logger = get_logger("routes.script_gen")

router = APIRouter(tags=["script_gen"])


class GenerateScriptRequest(BaseModel):
    prompt: str


@router.post("/api/generate-script")
async def api_generate_script(body: GenerateScriptRequest):
    """AI 生成剧本（SSE 流式返回进度）"""
    user_prompt = body.prompt.strip()
    if not user_prompt:
        raise HTTPException(400, "请输入剧本描述")
    if len(user_prompt) > 150:
        raise HTTPException(400, f"描述不能超过150字（当前{len(user_prompt)}字）")

    def event_stream():
        final_script_id = None
        final_path = None
        for event in generate_script(user_prompt):
            evt_type = event.get("type", "progress")
            payload = json.dumps(event, ensure_ascii=False)
            yield f"event: {evt_type}\ndata: {payload}\n\n"
            if evt_type == "done":
                final_script_id = event.get("script_id")
                final_path = event.get("path")

        if final_script_id and final_path:
            try:
                adapter = GenericScriptAdapter()
                adapter.load(final_path)
                summary = adapter.to_summary()
                uploaded_scripts[final_script_id] = {
                    "path": final_path,
                    "adapter": adapter,
                    "summary": summary,
                }
            except Exception as e:
                logger.warning("[script_gen] 剧本摘要缓存失败: %s", e)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
