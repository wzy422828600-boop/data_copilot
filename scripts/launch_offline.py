"""Local launcher: reopen this project's running app, or start it once."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

PROJECT = Path(__file__).resolve().parents[1]
PORT = 8517
URL = f"http://127.0.0.1:{PORT}"


def port_in_use() -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", PORT)) == 0


def healthy() -> bool:
    try:
        with build_opener(ProxyHandler({})).open(URL + "/_stcore/health", timeout=1) as response:
            return response.status == 200 and response.read(32).strip() == b"ok"
    except OSError:
        return False


def is_our_app() -> bool:
    """Do not assume that another service using this port belongs to us."""
    try:
        pids = subprocess.check_output(
            ["/usr/sbin/lsof", "-t", f"-iTCP:{PORT}", "-sTCP:LISTEN"], text=True
        ).split()
        for pid in set(pids):
            cwd = subprocess.check_output(
                ["/usr/sbin/lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"], text=True
            ).splitlines()
            if f"n{PROJECT}" in cwd:
                return True
    except (OSError, subprocess.CalledProcessError):
        pass
    return False


def open_app() -> None:
    print(f"浏览器地址：{URL}", flush=True)
    if not webbrowser.open(URL, new=2):
        print("未能自动打开浏览器，请复制上方地址打开。", flush=True)


def reuse_existing() -> bool:
    if not is_our_app():
        print(f"端口 {PORT} 被其他程序占用，请关闭占用程序后重试。未停止任何程序。", flush=True)
        return False
    for _ in range(20):
        if healthy():
            print("零售BI助手已在运行，正在打开现有页面，无需重复启动。", flush=True)
            open_app()
            return True
        time.sleep(0.25)
    print("已发现助手进程，但尚未就绪。请检查原启动窗口的报错后重试。", flush=True)
    return False


def main() -> int:
    if port_in_use():
        return 0 if reuse_existing() else 1
    env = dict(os.environ, ANTHROPIC_API_KEY="", PYTHONDONTWRITEBYTECODE="1")
    subprocess.run(
        [sys.executable, "scripts/upgrade_demo_data.py", "--quiet"],
        cwd=PROJECT, env=env, check=True,
    )
    print("正在启动离线零售BI助手。保持此窗口打开；按 Control+C 停止。", flush=True)
    child = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py",
         "--server.address", "127.0.0.1", "--server.port", str(PORT),
         "--server.headless", "true", "--server.showEmailPrompt", "false",
         "--browser.gatherUsageStats", "false"],
        cwd=PROJECT, env=env,
    )
    try:
        for _ in range(120):
            if child.poll() is not None:
                if port_in_use() and reuse_existing():
                    return 0
                print("助手未能启动，请查看上方错误信息。", flush=True)
                return child.returncode or 1
            if healthy():
                open_app()
                return child.wait()
            time.sleep(0.25)
        print("启动等待超时，请查看上方日志。", flush=True)
        child.terminate()
        child.wait()
        return 1
    except KeyboardInterrupt:
        if child.poll() is None:
            child.terminate()
            child.wait()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
