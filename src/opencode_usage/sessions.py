"""Manage local OpenCode sessions through its authenticated CLI API."""
import json
import subprocess
import unicodedata
from datetime import datetime
from urllib.parse import urlparse


def api(method, path, body=None):
    command = ['opencode', 'api', method, path]
    if body is not None:
        command += ['--data', json.dumps(body, ensure_ascii=False)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except FileNotFoundError as exc:
        raise ValueError('未找到 OpenCode 命令，无法管理会话。') from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError('OpenCode 接口超时；请先确认操作结果，再重试。') from exc
    if result.returncode:
        raise ValueError('OpenCode 接口请求失败；请检查本机服务状态。')
    if not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError('OpenCode 接口返回了无法识别的数据。') from exc


def local_service():
    info = api('get', '/api/info')
    urls = info.get('urls') if isinstance(info, dict) else None
    if not urls or any(urlparse(url).hostname not in ('127.0.0.1', '::1', 'localhost') for url in urls):
        raise ValueError('无法确认 OpenCode 服务仅在本机运行，已停止操作。')


def list_sessions():
    local_service()
    sessions = {}
    for archived in ('false', 'true'):
        cursor = None
        seen = set()
        while True:
            from urllib.parse import urlencode
            params = {'limit': '100', 'archived': archived}
            if cursor:
                params['cursor'] = cursor
            page = api('get', '/api/session?' + urlencode(params))
            if not isinstance(page, dict) or not isinstance(page.get('data'), list) or not isinstance(page.get('cursor'), dict):
                raise ValueError('无法读取完整会话列表，已停止操作。')
            for item in page['data']:
                if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'].startswith('ses'):
                    raise ValueError('会话列表格式无效，已停止操作。')
                sessions[item['id']] = item
            cursor = page['cursor'].get('next')
            if not cursor:
                break
            if not isinstance(cursor, str) or cursor in seen:
                raise ValueError('会话分页异常，已停止操作。')
            seen.add(cursor)
    return list(sessions.values())


def active_ids():
    result = api('get', '/api/session/active')
    if not isinstance(result, dict) or not isinstance(result.get('data'), dict):
        raise ValueError('无法确认活动会话，已停止操作。')
    return set(result['data'])


def descendants(sessions, selected):
    result = set(selected)
    while True:
        more = {s['id'] for s in sessions if s.get('parentID') in result} - result
        if not more:
            return result
        result.update(more)


def path(session_id):
    from urllib.parse import quote
    return '/api/session/' + quote(session_id, safe='')


def open_session(item):
    """Open an existing session in OpenCode's interactive terminal."""
    local_service()
    response = api('get', path(item['id']))
    current = response.get('data') if isinstance(response, dict) else None
    if not isinstance(current, dict) or current.get('id') != item['id']:
        raise ValueError('无法确认会话仍然存在，未启动 OpenCode。')
    directory = (current.get('location') or {}).get('directory')
    if not isinstance(directory, str) or not directory:
        raise ValueError('无法确认会话工作目录，未启动 OpenCode。')
    try:
        result = subprocess.run(['opencode', '--session', item['id'], directory], check=False)
    except FileNotFoundError as exc:
        raise ValueError('未找到 OpenCode 命令。') from exc
    if result.returncode:
        raise ValueError(f'OpenCode 已退出，退出码 {result.returncode}。')


def parse_selection(value, count):
    """Parse comma-separated numbers and inclusive ranges into zero-based indices."""
    import re
    result = set()
    for part in value.replace('，', ',').split(','):
        match = re.fullmatch(r'\s*([0-9]+)\s*(?:-\s*([0-9]+)\s*)?', part)
        if not match:
            raise ValueError('编号格式无效')
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        if start < 1 or start > end or end > count:
            raise ValueError('编号范围无效')
        result.update(range(start - 1, end))
    return sorted(result)


def display_width(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in text)


def cell(value, width):
    text = ''.join(' ' if unicodedata.category(c).startswith('C') else c for c in str(value or ''))
    result = ''
    for c in text:
        if display_width(result + c) > width:
            while result and display_width(result + '…') > width:
                result = result[:-1]
            result += '…'
            break
        result += c
    return result + ' ' * max(0, width - display_width(result))


def columns(item):
    timestamp = item.get('time', {}).get('updated')
    try:
        updated = datetime.fromtimestamp(timestamp / 1000).strftime('%Y-%m-%d %H:%M') if timestamp else '—'
    except (TypeError, ValueError, OverflowError, OSError):
        updated = '—'
    tokens = item.get('tokens') or {}
    cache = tokens.get('cache') or {}
    values = [tokens.get('input'), tokens.get('output'), tokens.get('reasoning'), cache.get('read'), cache.get('write')]
    total = f"{sum(v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)) / 1_000_000:,.2f} M" if tokens else '—'
    location = item.get('location') or {}
    return (item.get('title') or '未命名会话', updated, total, location.get('directory') or '—')


def table(items):
    headers = ('编号', '会话名称', '最新使用时间', '累计 token', '工作目录')
    widths = (4, 28, 16, 14, 8)
    lines = ['  '.join(cell(v, w) for v, w in zip(headers, widths))]
    lines.append('  '.join('─' * w for w in widths))
    for index, item in enumerate(items, 1):
        title, updated, total, directory = columns(item)
        prefix = '  '.join(cell(v, w) for v, w in zip((str(index), title, updated), widths))
        prefix += '  ' + ' ' * max(0, widths[3] - display_width(total)) + total
        directory = ''.join(' ' if unicodedata.category(c).startswith('C') else c for c in str(directory))
        lines.append(prefix + '  ' + directory)
    return lines


def choose_checkboxes(items):
    """Select in a terminal; return None when unavailable, [] when cancelled."""
    import sys
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None
    try:
        import curses
    except ImportError:
        return None

    def select(screen):
        selected = set()
        current, top = 0, 0
        screen.keypad(True)
        while True:
            screen.erase()
            height, width = screen.getmaxyx()
            count = max(1, height - 3)
            top = min(max(top, current - count + 1), current)
            def line(y, text):
                if y < height and width > 1:
                    try:
                        screen.addstr(y, 0, cell(text, width - 1))
                    except curses.error:
                        pass
            line(0, '↑↓ 移动  空格选择  回车确认选择  q 取消')
            for offset, item in enumerate(items[top:top + count]):
                index = top + offset
                marker = '>' if index == current else ' '
                check = 'x' if index in selected else ' '
                title, updated, total, directory = columns(item)
                line(offset + 1, f'{marker} [{check}] {index + 1}. {title} · {updated} · {total} token · {directory}')
            line(height - 1, f'已选择 {len(selected)} 项；删除前仍需输入 DELETE 确认')
            screen.refresh()
            key = screen.getch()
            if key in (curses.KEY_UP, ord('k')):
                current = max(0, current - 1)
            elif key in (curses.KEY_DOWN, ord('j')):
                current = min(len(items) - 1, current + 1)
            elif key == ord(' '):
                selected.symmetric_difference_update({current})
            elif key in (10, 13, curses.KEY_ENTER):
                return sorted(selected)
            elif key in (27, ord('q')):
                return []
    try:
        return curses.wrapper(select)
    except (curses.error, OSError):
        return None
