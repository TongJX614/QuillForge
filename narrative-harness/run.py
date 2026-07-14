# -*- coding: utf-8 -*-
"""
Narrative Harness - Quick Launcher
Double-click run_test.bat to start
"""
import io
import sys
import os
import subprocess

# Windows UTF-8 fix
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DATA = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "\u56de\u54cd-DEMO", "\u56de\u54cd-DEMO"))

def main():
    print("=" * 60)
    print("  \u56de\u54cd x Narrative Harness - \u5feb\u901f\u6d4b\u8bd5")
    print("=" * 60)
    print()

    # Check script data exists
    if not os.path.isdir(SCRIPT_DATA):
        print(f"[ERROR] \u5267\u672c\u76ee\u5f55\u4e0d\u5b58\u5728: {SCRIPT_DATA}")
        print("\u8bf7\u786e\u4fdd \u56de\u54cd-DEMO \u6587\u4ef6\u5939\u5728 narrative-harness \u7684\u4e0a\u4e00\u7ea7\u76ee\u5f55\u4e2d")
        input("\n\u6309\u56de\u8f66\u952e\u9000\u51fa...")
        return

    # Check dependencies
    print("[1/2] \u68c0\u67e5\u4f9d\u8d56...")
    subprocess.run([sys.executable, "-m", "pip", "install", "pyyaml", "openai", "--quiet"],
                    capture_output=True)
    print("      OK")
    print()

    # Check API Key
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("[2/2] \u672a\u68c0\u6d4b\u5230 DEEPSEEK_API_KEY \u73af\u5883\u53d8\u91cf")
        print()
        print("  \u8bf7\u8f93\u5165\u4f60\u7684 DeepSeek API Key\uff08\u4ee5 sk- \u5f00\u5934\uff09\uff1a")
        print("  \u5982\u679c\u8fd8\u6ca1\u6709\uff0c\u53bb https://platform.deepseek.com/ \u6ce8\u518c\u83b7\u53d6")
        print("  \u76f4\u63a5\u56de\u8f66 = Mock \u6a21\u5f0f\uff08\u4e0d\u8c03 AI\uff09")
        print()
        api_key = input("  API Key: ").strip()

    # Run test
    print()
    cmd = [sys.executable, "tests/test_echo.py", SCRIPT_DATA]
    if api_key:
        cmd.extend(["--live", "--api-key", api_key])
        print(f"[2/2] Live \u6a21\u5f0f - \u8c03\u7528 DeepSeek \u751f\u6210\u53d9\u4e8b")
    else:
        print("[2/2] Mock \u6a21\u5f0f - \u4e0d\u8c03\u7528 AI\uff0c\u9a8c\u8bc1\u6d41\u6c34\u7ebf\u903b\u8f91")

    print()
    print("-" * 60)
    subprocess.run(cmd, cwd=SCRIPT_DIR)
    print("-" * 60)

    print()
    print("=" * 60)
    print("  \u6d4b\u8bd5\u5b8c\u6210\uff01")
    print("=" * 60)

if __name__ == "__main__":
    main()
