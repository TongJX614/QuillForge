# -*- coding: utf-8 -*-
"""
QuillForge Server - Launcher
"""
import io, sys, os, subprocess, webbrowser, time

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"
PORT = 8050


def main():
    print("=" * 60)
    print("  QuillForge - 通用文本生成引擎")
    print("=" * 60)
    print()

    # API Key
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print()
        print("  \u8bf7\u8f93\u5165 DeepSeek API Key\uff08\u76f4\u63a5\u56de\u8f66\u8df3\u8fc7\uff09:")
        api_key = input("  Key: ").strip()
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key
        print("      API Key \u5df2\u8bbe\u7f6e")
    else:
        print("      [WARNING] \u672a\u8bbe\u7f6e API Key")

    print()
    print(f"[1/1] 启动服务器...")
    print(f"      前端: http://{HOST}:{PORT}")
    print(f"      API:  http://{HOST}:{PORT}/docs")
    print()

    def open_browser():
        time.sleep(2)
        webbrowser.open(f"http://{HOST}:{PORT}")

    import threading
    threading.Thread(target=open_browser, daemon=True).start()

    cmd = [sys.executable, os.path.join(SCRIPT_DIR, "src", "server.py"),
           "--host", HOST, "--port", str(PORT)]
    subprocess.run(cmd, cwd=SCRIPT_DIR)


if __name__ == "__main__":
    main()
