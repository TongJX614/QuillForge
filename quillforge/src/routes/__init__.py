# -*- coding: utf-8 -*-
"""
路由子包 — 按功能域拆分 API 端点

每个模块使用 FastAPI APIRouter，由 server.py 统一注册。
"""

from .pages import router as pages_router
from .script_gen import router as script_gen_router
from .story import router as story_router
from .upload import router as upload_router
from .auto_run import router as auto_run_router
from .session import router as session_router
from .realtime import router as realtime_router
from .assets import router as assets_router
