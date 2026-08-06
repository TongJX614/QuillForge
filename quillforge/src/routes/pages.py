# -*- coding: utf-8 -*-
"""静态页面路由：/, /game"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["pages"])

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.get("/")
def index():
    html_path = _STATIC_DIR / "index.html"
    if html_path.exists():
        return FileResponse(html_path, headers={"Cache-Control": "no-cache"})
    return {"message": "QuillForge API v3.0 - Visit /docs"}


@router.get("/game", summary="游戏页面（VN Galgame 引擎）")
def game_page():
    """返回 VN Galgame 游戏页面（打字机/立绘/TTS/结局），从首页跳转进入"""
    html_path = _STATIC_DIR / "game.html"
    if html_path.exists():
        return FileResponse(html_path, headers={"Cache-Control": "no-cache"})
    raise HTTPException(404, "game.html 不存在")
