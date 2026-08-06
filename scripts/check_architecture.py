# -*- coding: utf-8 -*-
"""
check_architecture — 最小机械化质量门禁

自动检查 AGENTS.md 中的架构约束：
  1. 适配器层（adapter/）不得调用 LLM
  2. 路由层（routes/）不得包含生成逻辑（不得直接导入 LLM 客户端）
  3. 所有 src/ 下 .py 文件必须包含 UTF-8 编码声明头

用法:
    python scripts/check_architecture.py          # 检查全部
    python scripts/check_architecture.py --fix    # 仅报告（不做修改）

退出码:
    0 — 全部通过
    1 — 存在违规
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ═══════════════════════════════════════════════════════
# 项目路径
# ═══════════════════════════════════════════════════════

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "quillforge" / "src"

# ═══════════════════════════════════════════════════════
# 违规记录
# ═══════════════════════════════════════════════════════

violations: list[str] = []


def _rel(path: Path) -> str:
    """返回相对于项目根的路径字符串"""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


# ═══════════════════════════════════════════════════════
# 检查 1: 适配器层不得调用 LLM
# AGENTS.md: "适配器层（adapter/）不得调用 LLM，只做数据解析和归一化"
# ═══════════════════════════════════════════════════════

_LLM_IMPORT_PATTERN = re.compile(
    r"^\s*(from\s+llm_client\s+import|import\s+llm_client|from\s+\.llm_client\s+import)",
    re.MULTILINE,
)


def check_adapter_no_llm() -> None:
    """adapter/ 目录下的文件不得导入 llm_client"""
    adapter_dir = SRC_DIR / "adapter"
    if not adapter_dir.is_dir():
        return
    for py_file in sorted(adapter_dir.rglob("*.py")):
        if py_file.name.startswith("__"):
            continue
        content = py_file.read_text(encoding="utf-8")
        if _LLM_IMPORT_PATTERN.search(content):
            violations.append(
                f"[adapter-no-llm] {_rel(py_file)}: 适配器层不得导入 llm_client"
            )


# ═══════════════════════════════════════════════════════
# 检查 2: 路由层不得包含生成逻辑（不得直接导入 LLM 客户端）
# AGENTS.md: "路由层（routes/）不得包含生成逻辑，只做请求解析、调用引擎、格式化响应"
# ═══════════════════════════════════════════════════════


def check_routes_no_llm() -> None:
    """routes/ 目录下的文件不得直接导入 llm_client"""
    routes_dir = SRC_DIR / "routes"
    if not routes_dir.is_dir():
        return
    for py_file in sorted(routes_dir.rglob("*.py")):
        if py_file.name.startswith("__"):
            continue
        content = py_file.read_text(encoding="utf-8")
        if _LLM_IMPORT_PATTERN.search(content):
            violations.append(
                f"[routes-no-llm] {_rel(py_file)}: 路由层不得直接导入 llm_client"
            )


# ═══════════════════════════════════════════════════════
# 检查 3: 文件头必须包含 UTF-8 编码声明
# AGENTS.md: "文件头：# -*- coding: utf-8 -*- + 模块级 docstring 说明职责"
# ═══════════════════════════════════════════════════════

_CODING_HEADER = "# -*- coding: utf-8 -*-"


def check_file_header() -> None:
    """src/ 下所有非 __init__.py 的 .py 文件第一行必须是编码声明"""
    if not SRC_DIR.is_dir():
        return
    for py_file in sorted(SRC_DIR.rglob("*.py")):
        # 跳过 __pycache__ 和 __init__.py
        if "__pycache__" in py_file.parts:
            continue
        if py_file.name == "__init__.py":
            continue
        first_line = py_file.read_text(encoding="utf-8").split("\n", 1)[0].strip()
        if first_line != _CODING_HEADER:
            violations.append(
                f"[file-header] {_rel(py_file)}: 首行必须为 '{_CODING_HEADER}'"
            )


# ═══════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════


def main() -> int:
    print("=== QuillForge 架构约束检查 ===\n")

    check_adapter_no_llm()
    check_routes_no_llm()
    check_file_header()

    if violations:
        print(f"[FAIL] 发现 {len(violations)} 处违规:\n")
        for v in violations:
            print(f"  {v}")
        print()
        return 1

    print("[PASS] 全部通过 (3 项检查)")
    print("  [adapter-no-llm]  适配器层未调用 LLM")
    print("  [routes-no-llm]   路由层未直接导入 LLM 客户端")
    print("  [file-header]     文件头编码声明完整")
    return 0


if __name__ == "__main__":
    sys.exit(main())
