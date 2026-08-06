# -*- coding: utf-8 -*-
"""AI 实时生成路由：/api/realtime/*"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from realtime_engine import RealtimeStorySession
from dependencies import realtime_sessions, get_image_generator, get_tts_generator
from logger import get_logger

logger = get_logger("routes.realtime")

router = APIRouter(tags=["realtime"])
limiter = Limiter(key_func=get_remote_address)

IMAGE_CONCURRENCY = int(os.getenv("IMAGE_CONCURRENCY", "7"))


class RealtimeStartRequest(BaseModel):
    prompt: str


class RealtimeChooseRequest(BaseModel):
    choice_index: int


@router.post("/api/realtime/start", summary="AI实时生成剧本-开始（SSE流式）")
@limiter.limit("10/minute")
async def realtime_start(request: Request, body: RealtimeStartRequest):
    """
    输入一句话描述，流式返回故事种子 + 第一个场景。
    TTS 语音与场景事件实时交错推送。
    """
    user_prompt = body.prompt.strip()
    if not user_prompt:
        raise HTTPException(400, "请输入剧本描述")
    if len(user_prompt) > 150:
        raise HTTPException(400, f"描述不能超过150字（当前{len(user_prompt)}字）")

    session = RealtimeStorySession(user_prompt)
    realtime_sessions[session.session_id] = session

    image_gen = get_image_generator()

    return StreamingResponse(
        session.stream_realtime_sse(
            session.initialize(),
            image_gen=image_gen,
            tts_gen=get_tts_generator(),
            on_story_seed=lambda: session._start_char_gen_bg(image_gen),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "close"},
    )


@router.post("/api/realtime/{session_id}/choose", summary="AI实时生成剧本-选择（SSE流式）")
async def realtime_choose(session_id: str, body: RealtimeChooseRequest):
    """玩家做出选择，流式返回下一场景。TTS 实时推送。"""
    session = realtime_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "实时生成会话不存在")

    return StreamingResponse(
        session.stream_realtime_sse(
            session.choose(body.choice_index),
            image_gen=get_image_generator(),
            tts_gen=get_tts_generator(),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "close"},
    )


@router.get("/api/realtime/{session_id}/state", summary="AI实时生成剧本-状态查询")
def realtime_state(session_id: str):
    """查询实时生成会话的当前状态。"""
    session = realtime_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "实时生成会话不存在")
    return session.get_state()


@router.get("/api/realtime/{session_id}/assets-stream", summary="AI实时生成-预生成角色立绘+场景背景（SSE）")
def realtime_assets_stream(session_id: str):
    """
    为实时生成会话预生成美术素材（SSE 推送进度）。
    Phase 1（阻塞）：所有角色立绘 + 已生成场景的背景图 → assets_ready
    """
    session = realtime_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "实时生成会话不存在")

    def sse():
        for evt in session.stream_assets_sse(get_image_generator(), image_concurrency=IMAGE_CONCURRENCY):
            evt_type = evt.get("event", "message")
            yield f"event: {evt_type}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
