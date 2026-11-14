"""Small, dependency-free terminal presentation; works without ANSI support."""
import os
import shutil
import sys
import unicodedata
from pathlib import Path


def supports_color():
    if not sys.stdout.isatty() or 'NO_COLOR' in os.environ or os.environ.get('TERM') == 'dumb':
        return False
    if os.name != 'nt':
        return True
    try:
        import ctypes
        mode = ctypes.c_ulong()
        ctypes.windll.kernel32.GetStdHandle.restype = ctypes.c_void_p
        ctypes.windll.kernel32.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        ctypes.windll.kernel32.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)) and
                    ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 4))
    except (OSError, AttributeError):
        return False


def compatible(text):
    encoding = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    return str(text).encode(encoding, errors='replace').decode(encoding)


def configure_output():
    # Keep native console encoding; use portable UTF-8 for files and pipes.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            options = {'errors': 'replace'}
            if not stream.isatty():
                options['encoding'] = 'utf-8'
            stream.reconfigure(**options)


def paint(text, code="2"):
    text = "".join(c if not unicodedata.category(c).startswith("C") else " " for c in str(text))
    color = supports_color()
    text = compatible(text)
    return f"\033[{code}m{text}\033[0m" if color else text


def notice(text, kind="info"):
    marks = {"info": ("·", "2"), "ok": ("✓", "32"), "warn": ("!", "33"), "error": ("×", "31")}
    mark, code = marks.get(kind, marks["info"])
    for line in str(text).splitlines():
        print(paint(f"  {mark} {line}", code))


def prompt(text):
    return input(paint(f"  › {text} ", "36"))


def menu(version, name, home, status=None):
    home = Path(home)
    color = supports_color()
    width = max(12, min(76, shutil.get_terminal_size((80, 24)).columns - 2))
    inner = width - 4
    def style(text, code):
        text = compatible(text)
        return f"\033[{code}m{text}\033[0m" if color else text
    def row(text="", code="0"):
        part, size = "", 0
        for c in str(text):
            if unicodedata.category(c).startswith("C"):
                c = " "
            n = 0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
            if size + n > inner:
                print("│ " + style(part + " " * (inner-size), code) + " │")
                part, size = "", 0
            part += c
            size += n
        print("│ " + style(part + " " * (inner-size), code) + " │")
    def rule(left="├", right="┤"):
        print(style(left + "─" * (width-2) + right, "2"))
    print()
    rule("╭", "╮")
    row(f"OCU  /  OpenCode 用量", "1;36")
    row(f"设备  {name}    ·    v{version}")
    if status:
        for line in status.splitlines():
            row(line, "2")
    rule()
    row("[1] 刷新    更新统计并打开报告", "1")
    row("[2] 数据    导入、导出与同步")
    row("[3] 设置")
    row()
    row("[v] 报告    [?] 帮助    [0] 退出", "2")
    rule("╰", "╯")
