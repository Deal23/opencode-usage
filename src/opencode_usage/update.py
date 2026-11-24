"""Best-effort version checks and explicit HTTPS updates; never upload usage."""
import re
import subprocess
import sys
import json
import tempfile
import time
import zipfile
import os
import shutil
from pathlib import Path
from urllib.request import Request, urlopen

from . import __version__
from .terminal import notice
from .workspace import state_path

VERSION_URL = "https://raw.githubusercontent.com/Deal23/opencode-usage/main/pyproject.toml"
API_VERSION_URL = "https://api.github.com/repos/Deal23/opencode-usage/contents/pyproject.toml?ref=main"
ARCHIVE_URLS = (
    "https://codeload.github.com/Deal23/opencode-usage/zip/refs/heads/main",
    "https://github.com/Deal23/opencode-usage/archive/refs/heads/main.zip",
)


def clean_workspace(home, mode):
    """Remove or archive workspace contents other than persistent data."""
    from uuid import uuid4
    from .workspace import active_price_path
    if mode not in ('overwrite', 'archive'):
        raise ValueError('请选择覆盖或归档')
    home = Path(home)
    protected = {'ledger.db', 'ledger.db-wal', 'ledger.db-shm', 'ledger.db-journal',
                 'menu-config.json', 'report-settings.json', 'workspace-owner.json',
                 'prices', 'model-prices.json', 'reverix-prices.json', 'backups', '.ocusage.lock'}
    price = active_price_path(home)
    try:
        protected.add(price.relative_to(home).parts[0])
    except ValueError:
        pass
    state = home / '.state'
    state_keep = {'deepseek-price-migration.json', 'reverix-price-migration.json',
                  'opencode-price-migration.json', 'pending-update.json'}
    entries = [(p, Path(p.name)) for p in home.iterdir() if p.name not in protected and p.name != '.state']
    if state.is_symlink() or (state.exists() and not state.is_dir()):
        raise ValueError('内部状态目录不可用，未清理工作目录')
    if state.exists():
        entries += [(p, Path('.state') / p.name) for p in state.iterdir() if p.name not in state_keep]
    if not entries:
        return None
    destination = None
    if mode == 'archive':
        backups = home / 'backups'
        if backups.is_symlink() or (backups.exists() and not backups.is_dir()):
            raise ValueError('备份目录不可用，未清理工作目录')
        destination = backups / ('update-archive-' + uuid4().hex)
        destination.mkdir(parents=True)
    for path, relative in entries:
        if destination:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            path.rename(target)
        elif path.is_symlink() or not path.is_dir():
            path.unlink()
        else:
            shutil.rmtree(path)
    return destination


def version_tuple(version):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("无法识别版本格式")
    return tuple(map(int, version.split(".")))


def check(force=False):
    """Only fetch public package metadata; no tokens, titles or paths transmitted."""
    from .core import DEFAULT_LEDGER, atomic_json
    cache = state_path(DEFAULT_LEDGER.parent, 'update-check.json')
    if not force:
        try:
            saved = json.loads(cache.read_text(encoding='utf-8'))
            ttl = 300 if saved.get('message') == '暂无法检查更新 · 本地统计不受影响' else 21600
            if saved.get('format') == 2 and saved.get("installed") == __version__ and 0 <= time.time() - saved["at"] < ttl:
                return saved['message']
        except (OSError, ValueError, KeyError, TypeError):
            pass
    def remember(message):
        try:
            atomic_json(cache, dict(format=2, at=time.time(), installed=__version__, message=message))
        except OSError:
            pass
        return message
    for url in (VERSION_URL, API_VERSION_URL):
        try:
            request = Request(url, headers={"User-Agent": "OCU-version-check", "Accept": "application/vnd.github.raw+json"})
            with urlopen(request, timeout=2) as response:
                text = response.read(128 * 1024).decode("utf-8")
            match = re.search(r'^version\s*=\s*"(\d+\.\d+\.\d+)"', text, re.M)
            if not match:
                raise ValueError("无法识别远端版本")
            latest = match[1]
            if version_tuple(latest) > version_tuple(__version__):
                return remember(f"发现新版本 {latest} · 设置 → 更新程序")
            return remember('已是最新版本' if version_tuple(latest) == version_tuple(__version__) else '本机版本高于远端')
        except (OSError, ValueError):
            continue
    return remember('暂无法检查更新 · 本地统计不受影响')


def download_source(folder):
    """Retry whole ZIP downloads; validate before installing. Never execute remote shell."""
    last = None
    for attempt in range(2):
        for index, url in enumerate(ARCHIVE_URLS):
            try:
                notice(f"下载源码 · 第 {attempt + 1}/2 轮")
                archive = folder / "source.zip"
                with urlopen(Request(url, headers={"User-Agent": "OCU-updater"}), timeout=15) as response, archive.open("wb") as f:
                    total = 0
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > 20 * 1024 * 1024:
                            raise ValueError("安装包过大，停止下载")
                        f.write(chunk)
                with zipfile.ZipFile(archive) as z:
                    if sum(i.file_size for i in z.infolist()) > 100 * 1024 * 1024:
                        raise ValueError("解压后文件过大")
                    root = folder / f"source-{attempt}-{index}"
                    for item in z.infolist():
                        path = (root / item.filename).resolve()
                        if root.resolve() not in path.parents or "\\" in item.filename:
                            raise ValueError("安装包路径不安全")
                    if z.testzip() is not None:
                        raise ValueError("安装包校验失败")
                    z.extractall(root)
                projects = list(root.glob("*/pyproject.toml"))
                if len(projects) != 1:
                    raise ValueError("安装包缺少项目元数据")
                text = projects[0].read_text(encoding='utf-8')
                match = re.search(r'^version\s*=\s*"(\d+\.\d+\.\d+)"', text, re.M)
                if not match or version_tuple(match[1]) < version_tuple(__version__):
                    raise ValueError("版本无效或低于当前版本，拒绝降级")
                return projects[0].parent
            except (OSError, ValueError, zipfile.BadZipFile) as exc:
                last = exc
                notice(f"当前通道下载未成功：{exc}", "warn")
    raise OSError(f"所有 HTTPS 下载通道均失败：{last}")


def install(home=None, mode=None, cleanup_result=None):
    if getattr(sys, 'frozen', False):
        notice('独立可执行版本请下载新版并核对 SHA256SUMS 后替换程序；不会执行 pip 自更新。', 'warn')
        return False
    command = [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall"]
    if sys.prefix == sys.base_prefix:
        command.append("--user")
    try:
        # Prefer an existing writable temp root selected by the OS/harness.
        with tempfile.TemporaryDirectory(prefix="ocu-update-") as tmp:
            source = download_source(Path(tmp))
            wheels = Path(tmp) / "wheels"
            build = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--retries", "2", "--timeout", "15", "--wheel-dir", str(wheels), str(source)], check=False)
            if build.returncode:
                notice("构建失败，尚未替换已安装的 OCU。", "error")
                return False
            packages = list(wheels.glob("opencode_usage-*.whl"))
            if len(packages) != 1:
                raise ValueError("未生成唯一的 OCU 安装包")
            if os.name == 'nt':
                # The entrypoint executable may be locked until this process exits.
                # Worker code is independent of the package being replaced.
                stage = Path(tempfile.mkdtemp(prefix='ocu-install-'))
                wheel = stage/packages[0].name
                shutil.copy2(packages[0], wheel)
                script = stage/'install.py'
                script.write_text(WINDOWS_WORKER, encoding='utf-8')
                log = stage/'update.log'
                from .core import DEFAULT_LEDGER, atomic_json
                atomic_json(state_path(DEFAULT_LEDGER.parent, 'pending-update.json'), {'stage': str(stage), 'mode': mode})
                with log.open('wb') as output:
                    subprocess.Popen([sys.executable, str(script), str(os.getpid()),
                                      *command, str(wheel)], stdout=output, stderr=output,
                                     creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
                notice(f'安装包已准备，退出 OCU 后开始安装。结果日志：{log}')
                notice(f'如需手动重试："{sys.executable}" -m pip install --force-reinstall "{wheel}"')
                return True
            result = subprocess.run(command + ["--no-index", "--no-deps", str(packages[0])], check=False)
    except (OSError, ValueError) as exc:
        notice(f"更新未完成：{exc}。可下载 ZIP 后运行 install.py；本地统计不受影响。", "error")
        return False
    if result.returncode:
        notice("安装失败，请检查输出或下载源码运行 install.py；台账未清空。", "error")
        return False
    if home is not None and mode:
        try:
            archived = clean_workspace(home, mode)
            if cleanup_result is not None:
                cleanup_result.append(archived)
            if archived:
                notice(f'旧文件已移至 {archived}，请自行清理。', 'warn')
        except (OSError, ValueError) as exc:
            notice(f'程序已更新，但工作目录清理未完成：{exc}；请检查工作目录及 backups。', 'warn')
            if cleanup_result is not None:
                cleanup_result.append(f'清理未完成：{exc}')
    from .shell import ensure_linux_path
    ensure_linux_path()
    notice("更新完成 · 请重新运行 ocu · 台账和报表已保留", "ok")
    return True


def previous_result(home):
    """Report detached installation outcome on the next menu launch."""
    pointer = state_path(home, 'pending-update.json')
    if not pointer.exists():
        return
    try:
        pending = json.loads(pointer.read_text(encoding='utf-8'))
        stage = Path(pending['stage'])
        result = stage/'result.txt'
        if not result.exists():
            notice(f'上次更新尚未写入结果，请稍后重试；日志：{stage / "update.log"}', 'warn')
            return
        code = result.read_text(encoding='utf-8').strip()
        notice('上次更新已成功完成。' if code == '0' else f'上次更新失败（{code}），可重新安装；日志：{stage / "update.log"}', 'ok' if code == '0' else 'error')
        if code == '0':
            if pending.get('mode'):
                archived = clean_workspace(home, pending['mode'])
                if archived:
                    notice(f'旧文件已移至 {archived}，请自行清理。', 'warn')
            cleanup_stage(stage)
        pointer.unlink()
    except (OSError, ValueError, KeyError) as exc:
        notice(f'无法确认上次更新结果：{exc}', 'warn')


def cleanup_stage(stage):
    """Remove only recognized successful installer artifacts from the OS temp root."""
    stage = Path(stage)
    if stage.is_symlink() or stage.parent.resolve() != Path(tempfile.gettempdir()).resolve() or not stage.name.startswith('ocu-install-'):
        return
    try:
        for item in stage.iterdir():
            if item.is_symlink() or not item.is_file():
                continue
            if item.name in {'install.py', 'update.log', 'result.txt'} or (item.name.startswith('opencode_usage-') and item.name.endswith('.whl')):
                item.unlink()
        stage.rmdir()
    except OSError:
        pass


WINDOWS_WORKER = '''import ctypes
import subprocess
import sys
from pathlib import Path
kernel = ctypes.windll.kernel32
kernel.OpenProcess.restype = ctypes.c_void_p
kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
kernel.CloseHandle.argtypes = [ctypes.c_void_p]
handle = kernel.OpenProcess(0x00100000, False, int(sys.argv[1]))
if handle:
    kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
    kernel.CloseHandle(handle)
result = subprocess.run(sys.argv[2:-1] + ['--no-index', '--no-deps', sys.argv[-1]])
Path(__file__).with_name('result.txt').write_text(str(result.returncode), encoding='utf-8')
print('Update exit code:', result.returncode, flush=True)
'''
