# -*- coding: utf-8 -*-
"""pytest 配置和公共 fixtures"""

import sys
from pathlib import Path

# 添加 src 目录到 Python 路径
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

import pytest
