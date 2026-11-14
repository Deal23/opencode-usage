#!/usr/bin/env python3
"""Install a checkout into a private environment, without touching usage data."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv


def windows_launcher(target, executable, name):
    content = f'@echo off\n"%~dp0..\\share\\ocusage-app\\Scripts\\{name}.exe" %*\n'
    legacy = f'@echo off\n"{executable}" %*\n'
    if target.exists():
        # Older Windows installers produced CRCRLF, decoded as blank lines.
        existing = target.read_text(encoding='utf-8', errors='replace')
        normalized = '\n'.join(line for line in existing.splitlines() if line.strip()) + '\n'
        if normalized not in (content, legacy):
            raise RuntimeError(f'入口已存在且内容不同，未覆盖：{target}')
    return content


def write_windows_launcher(target, executable, name):
    content = windows_launcher(target, executable, name)
    with target.open('w', encoding='utf-8', newline='') as stream:
        stream.write(content.replace('\n', '\r\n'))


def main():
    parser = argparse.ArgumentParser(description="一键安装/更新 OCU（不清空台账）")
    parser.add_argument("--update", action="store_true", help="先通过 HTTPS 拉取 main 最新代码；需要 Git 和仓库访问权限")
    args = parser.parse_args()
    if sys.version_info < (3, 9):
        parser.error("需要 Python 3.9 或更高版本")
    source = Path(__file__).resolve().parent
    if args.update:
        if not (source / ".git").exists():
            parser.error("ZIP 源码无法自动拉取；请下载新版 ZIP 后运行安装脚本。")
        # Refuse to silently overwrite local source modifications.
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=source, text=True).strip():
            parser.error("源码目录存在未提交修改，请先处理；未执行更新。")
        subprocess.run(["git", "-c", "http.version=HTTP/1.1", "pull", "--ff-only", "https://github.com/Deal23/opencode-usage.git", "main"], cwd=source, check=True)
    windows = os.name == "nt"
    app = Path.home() / ".local/share/ocusage-app"
    bindir = app / ("Scripts" if windows else "bin")
    python = bindir / ("python.exe" if windows else "python")
    commands = Path.home() / '.local/bin'
    # Validate both commands before venv/pip can replace the installed program.
    validate_launchers(commands, bindir, windows)
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(app)
    subprocess.run([str(python), "-m", "pip", "install", "--upgrade", "--force-reinstall", str(source)], check=True)
    commands.mkdir(parents=True, exist_ok=True)
    for name in ("ocu", "ocusage"):
        target = commands / (name + ".cmd" if windows else name)
        executable = bindir / (name + ".exe" if windows else name)
        if windows:
            # ASCII launcher avoids codepage-dependent Unicode installation paths.
            write_windows_launcher(target, executable, name)
        elif not target.is_symlink() and target.exists():
            raise RuntimeError(f"入口已存在且不是符号链接，未覆盖：{target}")
        elif target.is_symlink():
            if target.resolve() != executable.resolve():
                raise RuntimeError(f"入口指向其他程序，未覆盖：{target}")
        else:
            target.symlink_to(executable)
    if windows:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
            try:
                value, kind = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                value, kind = "", winreg.REG_EXPAND_SZ
            if str(commands).casefold() not in [p.casefold() for p in value.split(";")]:
                winreg.SetValueEx(key, "Path", 0, kind, str(commands) + ";" + value)
        print("安装完成。重新登录或打开刷新了环境变量的终端后运行 ocu。")
        subprocess.run([str(python), '-m', 'opencode_usage', '--version'], check=True)
        import ctypes
        from ctypes import wintypes
        send = ctypes.windll.user32.SendMessageTimeoutW
        send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                         wintypes.LPCWSTR, wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
        send(0xFFFF, 0x001A, 0, 'Environment', 2, 1000, None)
        print(f'PowerShell 立即启动：& "{bindir / "ocu.exe"}"')
    else:
        shell = Path(os.environ.get("SHELL", "/bin/bash")).name
        if shell not in ("bash", "zsh"):
            print(f"安装完成。请自行将 {commands} 加入 PATH。")
        else:
            rc = Path.home() / (".zshrc" if shell == "zsh" else ".bashrc")
            line = 'export PATH="$HOME/.local/bin:$PATH"'
            text = rc.read_text(encoding='utf-8') if rc.exists() else ""
            if line not in text.splitlines():
                with rc.open("a", encoding='utf-8') as f:
                    f.write("\n# OCU command entry\n" + line + "\n")
            print(f"安装完成。新终端运行 ocu；当前终端执行：source {rc}")
    print(f"立即启动：{commands / ('ocu.cmd' if windows else 'ocu')}")
    print(f'程序位置：{app}')
    print('现有台账、工作目录指针及设备身份保持不变。')


def validate_launchers(commands, bindir, windows):
    for name in ('ocu', 'ocusage'):
        target = commands / (name + '.cmd' if windows else name)
        executable = bindir / (name + '.exe' if windows else name)
        if target.is_symlink():
            if windows or target.resolve() != executable.resolve():
                raise RuntimeError(f'入口指向其他程序，未覆盖：{target}')
        elif windows:
            windows_launcher(target, executable, name)
        elif target.exists():
            raise RuntimeError(f'入口已存在且不是符号链接，未覆盖：{target}')


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='replace')
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        sys.exit(f"安装失败：{exc}")
