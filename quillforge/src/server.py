# -*- coding: utf-8 -*-
"""
QuillForge — 通用 FastAPI 后端
支持上传任意剧本文件，通用解析，交互式生成

状态机：
  IDLE → (generate) → GENERATED → (choose) → CHOSEN → (generate) → ...
"""

from __future__ import annotations

import io
import sys
import os
from pathlib import Path

# ── Numba 线程安全：必须在 import rembg/numba 之前设置 ──
os.environ["NUMBA_THREADING_LAYER"] = "workqueue"
os.environ["NUMBA_NUM_THREADS"] = "1"

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, 'buffer') and hasattr(sys.stdout.buffer, 'raw'):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception as e:
        # 此时 logger 尚未定义，不能用 logger.warning，否则 NameError 直接崩溃
        print(f"[server] stdout/stderr UTF-8 重编码失败: {e}", file=sys.__stderr__)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from logger import get_logger
from config_manager import get_settings

logger = get_logger("server")


# ═══════════════════════════════════════════════════════
# FastAPI App
# ═══════════════════════════════════════════════════════

app = FastAPI(title="QuillForge API", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 限流（slowapi）──
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── 可选 API Key 认证中间件 ──
_API_KEY = get_settings().quillforge_api_key

if _API_KEY:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as StarletteRequest

    class APIKeyMiddleware(BaseHTTPMiddleware):
        """X-API-Key 头认证（仅当环境变量 QUILLFORGE_API_KEY 非空时启用）"""
        async def dispatch(self, request: StarletteRequest, call_next):
            if request.url.path.startswith("/api/"):
                if request.headers.get("X-API-Key") != _API_KEY:
                    return JSONResponse(status_code=401, content={"detail": "无效的 API Key"})
            return await call_next(request)

    app.add_middleware(APIKeyMiddleware)

# ── 注册所有路由 ──
from routes import (
    pages_router, script_gen_router, story_router,
    upload_router, auto_run_router, session_router,
    realtime_router, assets_router,
)
app.include_router(pages_router)
app.include_router(script_gen_router)
app.include_router(story_router)
app.include_router(upload_router)
app.include_router(auto_run_router)
app.include_router(session_router)
app.include_router(realtime_router)
app.include_router(assets_router)


# ═══════════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════════

def main():
    import argparse
    import uvicorn

    settings = get_settings()

    parser = argparse.ArgumentParser(description="QuillForge Server")
    parser.add_argument("--host", default=settings.server_host)
    parser.add_argument("--port", type=int, default=settings.server_port)
    parser.add_argument("--api-key", default="")
    args = parser.parse_args()

    if args.api_key:
        os.environ["OPENAI_API_KEY"] = args.api_key

    if not (args.api_key or settings.openai_api_key):
        logger.warning("[WARNING] 未设置 API Key")

    logger.info("服务地址: http://%s:%d", args.host, args.port)
    logger.info("API 文档: http://%s:%d/docs", args.host, args.port)

    # 后台预热 rembg 模型（避免首次生成立绘时卡顿）
    def _warmup_rembg():
        try:
            from image_gen import _get_rembg_session, _REMOVE_BG_ENABLED
            if _REMOVE_BG_ENABLED:
                logger.info("[warmup] 正在预加载 rembg (isnet-anime) 模型...")
                _get_rembg_session()
                logger.info("[warmup] rembg 模型预加载完成")
        except Exception as e:
            logger.info("[warmup] rembg 预加载跳过: %s", e)

    import threading
    threading.Thread(target=_warmup_rembg, daemon=True).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
