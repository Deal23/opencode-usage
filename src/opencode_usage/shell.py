"""Persist Linux command discovery without changing system-wide settings."""
import os
from pathlib import Path
import sys
from .terminal import notice


def ensure_linux_path():
    if not sys.platform.startswith("linux"):
        return
    commands = Path.home() / ".local/bin"
    shell = Path(os.environ.get("SHELL", "/bin/bash")).name
    if shell == "zsh":
        rc = Path(os.environ.get("ZDOTDIR") or Path.home()) / ".zshrc"
    elif shell == "bash":
        rc = Path.home() / ".bashrc"
    else:
        notice(f"请将 {commands} 加入 {shell} 的 PATH；可直接运行 {commands / 'ocu'}。")
        return
    line = 'export PATH="$HOME/.local/bin:$PATH"'
    try:
        text = rc.read_text(encoding="utf-8") if rc.exists() else ""
        if line not in text.splitlines():
            with rc.open("a", encoding="utf-8") as f:
                f.write("\n# OCU command entry\n" + line + "\n")
            notice(f"已配置命令路径：{rc}")
        entries = [Path(p).expanduser() for p in os.environ.get("PATH", "").split(os.pathsep) if p and not p.startswith("~")]
        if commands not in entries:
            notice(f"当前终端尚未载入命令路径，退出后执行：source '{rc}'；或继续用 {commands / 'ocu'}。")
    except OSError as exc:
        notice(f"无法写入 PATH 配置（{exc}）；可直接运行 {commands / 'ocu'}。", 'warn')
