"""Dependency-free interactive entry point."""
from pathlib import Path
import json
import sqlite3
import socket
import sys
from contextlib import closing

from . import core
from . import __version__, update
from .shell import ensure_linux_path
from .terminal import menu, notice, prompt
from .interaction import configuration, identity, discover, state
from .sync import import_snapshot, archive_snapshot
from .actions import show_report
from . import actions, safety
from .paths import local_path
from .workspace import state_path, organize_state, inbox


def ask_path(label, default=None):
    notice(label)
    if default:
        notice(f"默认：{default}（回车使用）")
    value = prompt("路径：").strip()
    if not value and default is None:
        raise ValueError("路径不能为空")
    return local_path(value if value else default)


def import_device_name(data, config):
    """Modern snapshots own their provenance; only legacy data needs a label."""
    if data['version'] != 1:
        return None
    default = data.get('device_name') or data['device']
    notice('旧版快照没有逐条设备来源，以下名称仅用于标记这份旧快照的来源。')
    name = prompt(f'旧快照来源机器 [{default}]：').strip() or default
    config.setdefault('device_aliases', {})[data['device']] = name
    return name


def export_mode():
    notice('默认完整导出主台账已有用量、设备来源、会话标题、项目路径及更新时间。')
    notice('标题和路径含隐私，仅交给可信设备；不包含对话正文或密钥。')
    answer = prompt('回车/1：完整合并导出  2：仅用量（缺标题/项目）  0：取消：').strip()
    if answer in ('', '1'):
        return True
    if answer == '2':
        return False
    if answer == '0':
        return None
    raise ValueError('请选择 1、2 或 0')


def merge_files(paths, home, config):
    """Preview a batch once; preserve failed inputs and external originals."""
    from .sync import accept
    ledger = core.DEFAULT_LEDGER
    totals = dict(added=0, updated=0, same=0, metadata_updated=0, failed=0)
    prepared = []
    notice('预览逐文件与当前台账比较，多份快照可能包含相同消息；最终以实际合并结果为准。')
    unique = {}
    for p in paths:
        unique.setdefault(Path(p).resolve(), Path(p))
    for source in unique.values():
        try:
            data = core.read_snapshot(source)
            info = actions.preview(data, ledger)
            notice(f"{Path(source).name} · {info['device']} · {info['start']} — {info['end']}")
            notice(f"新增 {info['added']} · 更新 {info['updated']} · 重复/旧值 {info['same']} · 冲突 {info['conflicts']} · {'含标题/路径' if info['metadata'] else '无标题/路径'}")
            if info['conflicts']:
                raise ValueError('身份冲突，未写入；请核对源文件')
            prepared.append((source, data))
        except (ValueError, OSError, sqlite3.Error) as exc:
            totals['failed'] += 1
            notice(f'{source.name} 无法合并：{exc}；保留文件。', 'error')
    if not prepared or prompt(f'确认合并以上 {len(prepared)} 份快照？[y/N]：').strip().lower() != 'y':
        return totals
    safety.backup(home, 'import')
    for source, data in prepared:
        try:
            name = import_device_name(data, config)
            result = accept(home, source, data, ledger, name)
            for key in result:
                totals[key] += result[key]
            config['last_import'] = str(Path(source).resolve())
            core.atomic_json(home/'menu-config.json', config)
            names = sorted({d['name'] for ds in data.get('sources', {}).values() for d in ds})
            notice('设备来源：' + (', '.join(names) or name or '来源未知'))
            notice('已合并并清理本地收件箱文件；外部源文件保留。', 'ok')
        except (ValueError, OSError, sqlite3.Error) as exc:
            totals['failed'] += 1
            notice(f'{Path(source).name} 合并未完成：{exc}；保留文件，请修复后重试。', 'error')
    notice(f"合并结果：新增 {totals['added']} · 更新 {totals['updated']} · 会话信息补齐/更新 {totals['metadata_updated']} · 重复/旧值 {totals['same']} · 失败 {totals['failed']} · 主台账 {len(state(ledger)):,} 条")
    return totals


def setup_exchange(home, config):
    from .sync import validate_exchange
    notice(f'本机工作目录：{home}（独立台账，不与其他设备共用）')
    notice(f"快照交换目录：{config.get('exchange') or '未配置'}（各设备共用，由云盘传输）")
    value = prompt('交换目录：输入/拖入路径，? 选择目录，- 关闭，回车保留：').strip()
    if value == '-':
        config.pop('exchange', None)
        return
    if value == '?':
        paths = actions.choose_paths(directory=True)
        value = str(paths[0]) if paths else ''
    if value:
        folder = validate_exchange(home, local_path(value))
        folder.mkdir(parents=True, exist_ok=True)
        config['exchange'] = str(folder)
        notice('默认仅交换用量和设备来源；标题/路径不会共享。')
        if prompt('对可信设备额外共享标题和项目路径？含隐私 [y/N]：').strip().lower() == 'y':
            config['sync_metadata'] = True
        else:
            config['sync_metadata'] = False


def refresh(config, device, home, interactive=True, open_report=None):
    """Compatibility entry point; only interactive input belongs to the menu."""
    from .automation import refresh as run_refresh
    def ask(kind, value):
        if kind == 'source':
            return ask_path('未找到数据库，请指定本机 OpenCode 数据库（不可用时仍同步其他设备）', value)
        return import_device_name(value, config)
    return run_refresh(config, device, home, request_input=ask if interactive else None, open_report=open_report)


def main():
    from .locking import workspace_lock
    from .terminal import configure_output
    configure_output()
    try:
        with workspace_lock(core.DEFAULT_LEDGER.parent):
            _main()
    except (ValueError, OSError) as exc:
        notice(str(exc), 'error')
        raise SystemExit(1)


def preferences(home, config):
    options = [('check_updates', '启动检查更新', True),
               ('open_browser', '刷新后打开报告', True)]
    while True:
        for i, (key, label, default) in enumerate(options, 1):
            notice(f"[{i}] {label}：{'开' if config.get(key, default) else '关'}")
        notice('[0] 返回')
        choice = prompt('选择要修改的项目：').strip()
        if choice == '0':
            return
        if choice in ('1', '2'):
            key, label, default = options[int(choice)-1]
            config[key] = not config.get(key, default)
        else:
            notice('请选择 0–2。', 'warn')
            continue
        core.atomic_json(home/'menu-config.json', config)


def diagnostics(home, config):
    from .automation import diagnose, diagnostic_bundle
    from .sync import status as sync_status
    result = diagnose(home, config)
    labels = {'source': ('本机数据库', '在设置中检查数据库路径'),
              'ledger': ('历史台账', '检查磁盘或从有效备份恢复，不要删除台账'),
              'exchange': ('快照交换目录', '检查目录是否存在且可写')}
    for check in result['checks']:
        label, advice = labels[check['name']]
        notice(f"{label}：{'正常' if check['ok'] else '异常 · ' + advice}", 'ok' if check['ok'] else 'warn')
    if not any(check['name'] == 'ledger' for check in result['checks']):
        notice('历史台账：尚未建立，首次刷新或导入后生成。')
    if not config.get('exchange'):
        notice('快照交换：未配置，可在设置中指定交换目录。')
    notice(f"交换范围：{'全部历史' if config.get('relay_history') else '本机来源'} · {'含标题/项目' if config.get('sync_metadata') else '仅用量'}")
    devices = sync_status(home)['devices']
    notice(f"已接收 {len(devices)} 台设备 · {sum(bool(d.get('stale')) for d in devices.values())} 台超过 24 小时未检查/无新快照")
    if prompt('导出脱敏诊断文件？[y/N]：').strip().lower() == 'y':
        notice(f'诊断文件：{diagnostic_bundle(home, config)}；分享前请检查。')


def settings_action(home, config, device, setting):
    """Keep settings errors local; return True only when a restart is needed."""
    config_path = home/'menu-config.json'
    while True:
        before = json.loads(json.dumps(config))
        try:
            if setting == '0':
                return False
            if setting == '1':
                notice(f"当前设备：{config['device_name']}")
                name = prompt('新名称（回车保留）：').strip()
                if name:
                    if len(name) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in name):
                        raise ValueError('设备名应不超过 80 字，且不含控制字符')
                    with closing(core.connect(core.DEFAULT_LEDGER)) as c, c:
                        c.execute('UPDATE message_sources SET device_name=? WHERE device_id=?', (name, device))
                    config['device_name'] = name
                    config.setdefault('device_aliases', {})[device] = name
                    notice('设备名已修改；刷新后更新报告。', 'ok')
            elif setting == '2':
                from .workspace import migrate
                notice(f'当前目录：{home}')
                notice('请关闭其他 OCU 进程；每台设备使用独立目录。', 'warn')
                destination = ask_path('新工作目录（必须尚不存在）')
                if prompt('复制校验后切换，旧目录保留，继续？[y/N]：').strip().lower() == 'y':
                    safety.backup(home, 'migration')
                    target = migrate(home, destination)
                    notice(f'已切换：{target}；旧目录保留，请重新运行 ocu。', 'ok')
                    return True
            elif setting == '4':
                notice(update.check(force=True))
                if prompt('下载并更新 OCU？保留数据 [y/N]：').strip().lower() == 'y':
                    safety.backup(home, 'upgrade')
                    if update.install():
                        return True
            elif setting == '5':
                db = ask_path('本机 OpenCode 数据库', config.get('db', core.DEFAULT_DB))
                if not db.is_file():
                    raise ValueError('文件不存在，配置未修改')
                config['db'] = str(db.resolve())
            elif setting == '7':
                files = safety.backups(home)
                notice(f"自动备份 {len(files)} 份 · {sum(p.stat().st_size for p in files)/1048576:.1f} MiB · 保留最近 {config.get('backup_keep', 5)} 份")
                operation = prompt('[b] 备份  [r] 恢复  [k] 保留份数  [0] 返回：').strip()
                if operation == 'k':
                    value = prompt('保留份数 1–50（回车保留）：').strip()
                    if value:
                        if not value.isdigit() or not 1 <= int(value) <= 50:
                            raise ValueError('请输入 1–50，原设置不变')
                        config['backup_keep'] = int(value)
                if operation == 'b':
                    notice(f"备份：{safety.backup(home, 'manual') or '暂无台账'}")
                elif operation == 'r':
                    for i, file in enumerate(files, 1):
                        notice(f'[{i}] {file.name}')
                    number = prompt('备份编号（回车返回）：').strip()
                    if number.isdigit() and 1 <= int(number) <= len(files):
                        if prompt('恢复会回退累计记录，保留当前配置；先备份现状。输入 RESTORE 确认：') == 'RESTORE':
                            safety.restore(home, files[int(number)-1])
                            config.pop('processed_snapshots', None)
                            core.atomic_json(state_path(home, 'sync-state.json'), {'devices': {}})
                            show_report(device)
                            notice('已恢复台账；刷新可能重新补回源库和快照记录。', 'ok')
            elif setting == '8':
                diagnostics(home, config)
            else:
                raise ValueError('未知设置操作')
            if config != before:
                core.atomic_json(config_path, config)
            return False
        except (ValueError, OSError, sqlite3.Error) as exc:
            notice(f'设置未完成：{exc}；可修正后重试。', 'error')
            return False


def report_preferences(home):
    from .reporting import settings, validate
    path = home/'report-settings.json'
    while True:
        current = settings(home)
        notice(f"[1] 时区  {current['label']}\n[2] 汇率  {current['usd_to_cny']:g} CNY/USD\n[3] 价格配置文件\n[0] 返回")
        choice = prompt('选择项目：').strip()
        if choice == '0':
            return
        try:
            if choice == '3':
                if not path.exists():
                    core.atomic_json(path, {k: v for k, v in current.items() if k not in ('tz', 'label')})
                notice(f'价格配置：{path}；修改前请备份，保存后刷新生效。')
                actions.open_directory(home)
                continue
            if choice not in ('1', '2'):
                notice('请选择 0–3。', 'warn')
                continue
            key = 'timezone_offset' if choice == '1' else 'usd_to_cny'
            value = prompt('UTC 时差 -12 至 14：' if choice == '1' else '美元兑人民币汇率：').strip()
            if not value:
                continue
            candidate = {k: v for k, v in current.items() if k not in ('tz', 'label')}
            candidate[key] = float(value)
            validate(candidate)
            if candidate[key] != current[key]:
                core.atomic_json(path, candidate)
                notice('已保存，刷新后生效。', 'ok')
        except (ValueError, OSError) as exc:
            notice(f'未修改：{exc}', 'warn')


def sync_preferences(home, config):
    while True:
        notice(f"交换目录：{config.get('exchange') or '未配置'}")
        notice(f"共享范围：{'全部历史' if config.get('relay_history') else '本机来源'} · {'含标题/项目' if config.get('sync_metadata') else '仅用量'}")
        notice('[1] 交换目录\n[2] 共享标题与项目\n[3] 转发全部历史\n[0] 返回')
        choice = prompt('选择项目：').strip()
        if choice == '0':
            return
        before = dict(config)
        try:
            if choice == '1':
                setup_exchange(home, config)
            elif choice in ('2', '3'):
                if not config.get('exchange'):
                    notice('请先配置交换目录。', 'warn')
                    continue
                key = 'sync_metadata' if choice == '2' else 'relay_history'
                if key == 'sync_metadata' and not config.get(key):
                    if prompt('共享标题和项目路径含隐私，仅用于可信设备，开启？[y/N]：').strip().lower() != 'y':
                        continue
                config[key] = not config.get(key, False)
            else:
                notice('请选择 0–3。', 'warn')
            if config != before:
                core.atomic_json(home/'menu-config.json', config)
        except (ValueError, OSError) as exc:
            notice(f'同步设置未完成：{exc}', 'error')


def advanced_settings(home, config, device):
    while True:
        notice('高级\n[1] 数据源\n[2] 迁移目录\n[3] 备份与恢复\n[4] 诊断\n[0] 返回')
        choice = prompt('选择项目：').strip()
        if choice == '0':
            return False
        action = {'1': '5', '2': '2', '3': '7', '4': '8'}.get(choice)
        if action and settings_action(home, config, device, action):
            return True
        if not action:
            notice('请选择 0–4。', 'warn')


def settings_menu(home, config, device):
    while True:
        notice(f"设置\n[1] 设备名称  {config['device_name']}\n[2] 偏好\n[3] 报表\n[4] 更新\n[5] 高级\n[0] 返回")
        choice = prompt('选择项目：').strip()
        try:
            if choice == '0':
                return False
            if choice == '2':
                preferences(home, config)
            elif choice == '3':
                report_preferences(home)
            elif choice == '5':
                if advanced_settings(home, config, device):
                    return True
            elif choice in ('1', '4'):
                if settings_action(home, config, device, choice):
                    return True
            else:
                notice('请选择 0–5。', 'warn')
        except (ValueError, OSError, sqlite3.Error) as exc:
            notice(f'设置未完成：{exc}；可修正后重试。', 'error')


def data_menu(home, config, device):
    """Occasional data operations, separate from everyday report viewing."""
    while True:
        notice('数据')
        for label in ('[1] 导入', '[2] 导出', '[3] 同步设置',
                      '[4] 导出 CSV', '[5] 打开目录', '[0] 返回'):
            notice(label)
        pending = list(inbox(home).glob('*.snapshot.json'))
        if pending:
            notice(f'[r] 重试 {len(pending)} 份待处理导入')
        choice = prompt('选择操作：').strip().lower()
        try:
            if choice == '0':
                return
            if choice in ('1', 'r'):
                if choice == 'r':
                    sources = pending
                else:
                    value = prompt('输入/拖入快照路径，? 选择文件，0 返回：').strip()
                    if value in ('', '0'):
                        continue
                    sources = actions.choose_paths() if value == '?' else [local_path(value)]
                if sources:
                    result = merge_files(sources, home, config)
                    if any(result[k] for k in ('added', 'updated', 'metadata_updated')):
                        show_report(device, open_report=False)
            elif choice == '2':
                dest = ask_path('导出文件路径：回车使用默认路径，或输入自定义完整路径',
                                home/'exports'/actions.export_filename(config['device_name']))
                include_metadata = export_mode()
                if include_metadata is None:
                    continue
                if dest.exists() and prompt('文件已存在，覆盖？[y/N]：').strip().lower() != 'y':
                    continue
                core.export(core.DEFAULT_LEDGER, device, dest, config['device_name'], include_metadata)
                actions.prune_exports(home, dest)
                notice(actions.export_summary(dest, complete=include_metadata), 'ok')
                notice(f'文件：{dest}')
                notice('exports 只保留最新一份；长期保存请复制到其他目录。')
            elif choice == '3':
                sync_preferences(home, config)
            elif choice == '4':
                actions.export_csv(home)
            elif choice == '5':
                notice(f'工作目录：{home}')
                actions.open_directory(home)
            else:
                notice('请选择 0–5，待处理导入可选 r。', 'warn')
        except (ValueError, OSError, sqlite3.Error) as exc:
            notice(f'操作未完成：{exc}；已保存历史保留。', 'error')


def _main():
    ensure_linux_path()
    home = core.DEFAULT_LEDGER.parent
    config_path = home / "menu-config.json"
    try:
        config = configuration(home)
        try:
            device = identity(config, home, lambda text: prompt(text).strip().lower() == 'y')
        except ValueError as exc:
            notice(str(exc), 'warn')
            notice('不会接管原目录；可创建本机独立工作目录，重启后再导入其他设备快照。')
            if prompt('创建本机默认目录？[y/N]：').strip().lower() == 'y':
                from .workspace import pointer
                target = Path.home()/'.local/share'/f'ocusage-{socket.gethostname()}'
                if target.exists():
                    raise ValueError('建议目录已存在，请手动选择独立路径，未覆盖')
                target.mkdir(parents=True)
                core.atomic_json(pointer(), {'workspace': str(target)})
                notice(f'已创建 {target}，请重新启动 ocu。', 'ok')
            return
        config["device"] = device
        organize_state(home)
        update.previous_result(home)
        if config.get('check_updates', True):
            notice('正在检查版本…')
        status = update.check() if config.get('check_updates', True) else ''
        if status in ('已是最新版本', '本机版本高于远端'):
            status = ''
        if not config.get("device_name"):
            notice('首次设置：本机独立保存台账，多设备只交换快照；可随时在设置中修改。')
            config["device_name"] = prompt(f"机器名称 [{socket.gethostname()}]：").strip() or socket.gethostname()
            if sys.stdin.isatty():
                try:
                    found = discover(config)
                    if found:
                        notice(f'发现数据库：{found}')
                        if prompt('使用此数据库？[Y/n]：').strip().lower() != 'n':
                            config['db'] = str(found)
                    else:
                        notice('暂未发现数据库，首次刷新时可指定；也可只导入其他设备快照。', 'warn')
                    setup_exchange(home, config)
                except (ValueError, OSError, sqlite3.Error) as exc:
                    notice(f'可稍后在设置中完成：{exc}', 'warn')
                notice(f"设备：{config['device_name']} · 工作目录：{home} · 交换：{config.get('exchange', '未启用')}")
            core.atomic_json(config_path, config)
        home.mkdir(parents=True, exist_ok=True)
        while True:
            summary = actions.summary(home)
            pending = len(list(inbox(home).glob('*.snapshot.json')))
            if pending:
                summary += f'\n待重试导入 {pending} 份 · 数据 → r'
            menu(__version__, config["device_name"], home.resolve(), '\n'.join(v for v in (status, summary) if v))
            choice = prompt("选择 [0-3/v/?]：").strip().lower()
            if choice in ('5', 'h', '?'):
                from .help import show
                show(home)
                continue
            if choice in ('3', '4'):
                if settings_menu(home, config, device):
                    return
                continue
            if choice == "0":
                return
            try:
                if choice == "1":
                    refresh(config, device, home)
                elif choice == 'v':
                    actions.view_report(home)
                elif choice == 'd':
                    actions.open_directory(home/'reports')
                elif choice == "2":
                    data_menu(home, config, device)
                else:
                    notice("请选择 0–3、v 或 ?。", "warn")
            except (ValueError, OSError, sqlite3.Error) as exc:
                notice(str(exc), "error")
                notice('已提交的历史仍保留；请检查上方失败步骤，可在设置中查看备份。', 'warn')
    except (EOFError, KeyboardInterrupt):
        notice("已退出。")
    except (ValueError, OSError, sqlite3.Error) as exc:
        notice(f"配置读取失败：{exc}", "error")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
