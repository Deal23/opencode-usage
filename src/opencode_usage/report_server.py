"""Short-lived, loopback-only bridge for interactive dashboard operations."""
import base64
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import sessions


def choose_snapshot():
    """Show a native file picker without running a GUI toolkit in the HTTP thread."""
    if sys.platform == 'darwin':
        command = ['osascript', '-e', 'POSIX path of (choose file with prompt "选择 OCU 快照")']
    elif os.name == 'nt':
        script = ("Add-Type -AssemblyName System.Windows.Forms; "
                  "$d = New-Object System.Windows.Forms.OpenFileDialog; "
                  "$d.Title = '选择 OCU 快照'; $d.Filter = 'JSON (*.json)|*.json|All files (*.*)|*.*'; "
                  "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($d.FileName) }")
        encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        command = ['powershell.exe', '-NoProfile', '-STA', '-EncodedCommand', encoded]
    else:
        python = shutil.which('python3') if getattr(sys, 'frozen', False) else sys.executable
        if not python or not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
            raise ValueError('当前环境无法打开文件选择器。')
        script = ('import tkinter as tk; from tkinter import filedialog; '
                  'root=tk.Tk(); root.withdraw(); '
                  'path=filedialog.askopenfilename(title="选择 OCU 快照", filetypes=[("JSON", "*.json"), ("所有文件", "*")]); '
                  'print(path); root.destroy()')
        command = [python, '-c', script]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('文件选择超时，请重试。') from exc
    if result.returncode:
        if sys.platform == 'darwin' and ('-128' in result.stderr or 'User canceled' in result.stderr):
            return None
        raise ValueError('无法打开文件选择器，请检查本机图形环境。')
    return result.stdout.rstrip('\r\n') or None


def launch_session(item):
    command = sessions.session_command(item)
    if sys.platform == 'darwin':
        script = 'tell application "Terminal" to do script ' + json.dumps(shlex.join(command), ensure_ascii=False)
        subprocess.run(['osascript', '-e', script], check=True)
    elif os.name == 'nt':
        # Encode a PowerShell command rather than interpolating paths into cmd.exe.
        def quote(value):
            return "'" + value.replace("'", "''") + "'"
        script = '& ' + ' '.join(quote(v) for v in command)
        encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        subprocess.Popen(['powershell.exe', '-NoExit', '-EncodedCommand', encoded],
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        terminal = next((shutil.which(name) for name in ('x-terminal-emulator', 'gnome-terminal', 'konsole', 'xterm') if shutil.which(name)), None)
        if not terminal:
            raise ValueError('未找到支持的终端模拟器，请在终端面板打开会话。')
        subprocess.Popen([terminal, '-e', *command], start_new_session=True)


def perform(action, data):
    items = sessions.list_sessions()
    if action in ('preview-batch-delete', 'batch-delete'):
        ids = data.get('ids')
        if not isinstance(ids, list) or not ids or any(not isinstance(sid, str) for sid in ids):
            raise ValueError('请选择要删除的会话。')
        selected = set(ids)
        if not selected <= {s['id'] for s in items}:
            raise ValueError('会话列表已变化，请重新选择。')
        affected = sessions.descendants(items, selected)
        if affected & sessions.active_ids():
            raise ValueError('所选会话或其子会话正在运行，不能删除。')
        if action == 'preview-batch-delete':
            return [{'id': s['id'], 'title': s.get('title') or '未命名会话'} for s in items if s['id'] in affected]
        expected = data.get('affected')
        if not isinstance(expected, list) or any(not isinstance(value, str) for value in expected) or set(expected) != affected:
            raise ValueError('会话列表已变化，请重新确认删除。')
        current = sessions.list_sessions()
        if not selected <= {s['id'] for s in current} or sessions.descendants(current, selected) != affected or affected & sessions.active_ids():
            raise ValueError('会话状态已变化，请重新确认删除。')
        parents = {s['id']: s.get('parentID') for s in current}
        roots = []
        for selected_id in selected:
            ancestor, seen = parents.get(selected_id), set()
            while ancestor and ancestor not in seen and ancestor not in selected:
                seen.add(ancestor)
                ancestor = parents.get(ancestor)
            if ancestor not in selected:
                roots.append(selected_id)
        done = []
        for root in sorted(roots):
            try:
                latest = sessions.list_sessions()
                branch = sessions.descendants(current, {root})
                if root not in {s['id'] for s in latest} or sessions.descendants(latest, {root}) != branch or branch & sessions.active_ids():
                    raise ValueError('会话状态已变化')
                sessions.api('delete', sessions.path(root))
                done.append(root)
            except ValueError as exc:
                raise ValueError(f'批量删除中止：{exc}。已完成 {len(done)} 项，请刷新会话后检查。') from exc
        return None
    sid = data.get('id')
    item = next((s for s in items if s['id'] == sid), None)
    if not item:
        raise ValueError('会话不存在或已变化，请刷新列表。')
    if action == 'rename':
        title = data.get('title')
        if not isinstance(title, str) or not title.strip() or len(title) > 500:
            raise ValueError('请输入有效的会话标题。')
        sessions.api('patch', sessions.path(sid), {'title': title.strip()})
    elif action == 'open':
        launch_session(item)
    elif action == 'directory':
        from .actions import open_directory
        directory = (item.get('location') or {}).get('directory')
        if not isinstance(directory, str) or not Path(directory).is_dir() or not open_directory(directory):
            raise ValueError('无法打开会话工作目录。')
    elif action in ('preview-delete', 'delete'):
        affected = sessions.descendants(items, {sid})
        if affected & sessions.active_ids():
            raise ValueError('会话或其子会话正在运行，不能删除。')
        if action == 'preview-delete':
            return [{'id': s['id'], 'title': s.get('title') or '未命名会话'} for s in items if s['id'] in affected]
        expected = data.get('affected')
        if not isinstance(expected, list) or any(not isinstance(value, str) for value in expected) or set(expected) != affected:
            raise ValueError('会话列表已变化，请重新确认删除。')
        current = sessions.list_sessions()
        if sid not in {s['id'] for s in current} or sessions.descendants(current, {sid}) != affected or affected & sessions.active_ids():
            raise ValueError('会话状态已变化，请重新确认删除。')
        sessions.api('delete', sessions.path(sid))
    else:
        raise ValueError('未知操作。')
    return None


def start(report, refresh=None, settings=None, active=None, session_action=None):
    """Serve one report while the OCU process remains alive."""
    report = Path(report).resolve()
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, content_type='application/json; charset=utf-8'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def do_GET(self):
            if not self.valid_host() or self.path != '/':
                return self.send(404, b'Not found')
            try:
                self.send(200, report.read_bytes(), 'text/html; charset=utf-8')
            except OSError:
                self.send(404, b'Not found')

        def do_POST(self):
            origin = f'http://127.0.0.1:{self.server.server_port}'
            if not self.valid_host() or self.headers.get('Origin') != origin or not secrets.compare_digest(self.headers.get('X-OCU-Token', ''), token):
                return self.send(403, b'Forbidden')
            try:
                if active is not None and not active():
                    raise ValueError('操作已完成，请退出并重新运行 OCU。')
                length = int(self.headers.get('Content-Length', '0'))
                if length < 2 or length > 65536:
                    raise ValueError('请求大小无效。')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('请求格式无效。')
                action = self.path.lstrip('/')
                if action == 'refresh-report':
                    if refresh is None:
                        raise ValueError('报告刷新不可用，请重新从主菜单打开看板。')
                    result = refresh()
                elif action == 'version-check':
                    from . import __version__, update
                    result = {'version': __version__, 'message': update.check(force=True)}
                elif action == 'choose-snapshot':
                    result = choose_snapshot()
                elif action in ('settings-get', 'settings-save', 'sync-save', 'device-save', 'source-save', 'import-preview', 'import-confirm', 'open-workspace', 'export-csv', 'backup-create', 'backup-list', 'backup-restore', 'diagnose', 'install-update', 'migrate-workspace', 'prices-get', 'prices-preview', 'prices-save'):
                    if settings is None:
                        raise ValueError('设置不可用，请重新从主菜单打开看板。')
                    result = settings(action, data)
                elif action == 'list':
                    items = session_action(action, data) if session_action else sessions.list_sessions()
                    result = [{'id': s['id'], 'title': s.get('title') or '未命名会话',
                               'directory': (s.get('location') or {}).get('directory') or ''}
                              for s in items]
                else:
                    result = session_action(action, data) if session_action else perform(action, data)
                self.send(200, json.dumps({'data': result}, ensure_ascii=False).encode())
            except (ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
                self.send(400, json.dumps({'error': str(exc)}, ensure_ascii=False).encode())

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f'http://127.0.0.1:{server.server_port}/#{token}'
