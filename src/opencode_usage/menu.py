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
from .terminal import menu, notice, prompt, paint
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


def manual_export(home, device, config, destination, complete):
    if not destination and config.get('export_dir'):
        folder = local_path(config['export_dir'])
        if not folder.is_dir():
            raise ValueError(f'默认导出目录不可用：{folder}')
        destination = folder
    return actions.export_snapshot(home, device, config['device_name'], destination or None, complete)


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
    result = run_refresh(config, device, home, request_input=ask if interactive else None, open_report=False)
    ledger = core.DEFAULT_LEDGER
    if ledger.is_file():
        try:
            folder = local_path(config['export_dir']) if config.get('export_dir') else home/'exports'
            if config.get('export_dir') and not folder.is_dir():
                raise ValueError(f'默认导出目录不可用：{folder}')
            dest = folder/actions.export_filename(config['device_name'])
            if dest.exists():
                from uuid import uuid4
                dest = dest.with_name(dest.name.removesuffix('.snapshot.json') + '-' + uuid4().hex + '.snapshot.json')
            core.export(ledger, device, dest, config['device_name'], include_metadata=True)
            if not config.get('export_dir'):
                actions.prune_exports(home, dest)
            notice(f'已导出完整累计快照：{dest}', 'ok')
            notice('快照包含已有会话标题和项目路径，请仅交给可信设备。', 'warn')
            result['export'] = str(dest)
        except (ValueError, OSError, sqlite3.Error) as exc:
            result['ok'] = False
            result['errors'].append(f'export: {exc}')
            notice(f'本地快照导出未完成：{exc}；已保存的数据和报告保留。', 'warn')
        core.atomic_json(state_path(home, 'last-sync.json'), result)
    return result


def main():
    from .locking import workspace_lock
    from .terminal import configure_output
    configure_output()
    try:
        with workspace_lock(core.DEFAULT_LEDGER.parent):
            command = _main()
        if command:
            import os
            import subprocess
            try:
                result = subprocess.run(command, check=False)
            except FileNotFoundError as exc:
                raise ValueError('未找到 OpenCode 命令。') from exc
            finally:
                if sys.stdout.isatty() and os.environ.get('TERM') != 'dumb':
                    sys.stdout.write('\x1b[0m\x1b[?25h\x1b[2J\x1b[H')
                    sys.stdout.flush()
            if result.returncode:
                raise ValueError(f'OpenCode 已退出，退出码 {result.returncode}。')
    except (ValueError, OSError) as exc:
        notice(str(exc), 'error')
        raise SystemExit(1)


def preferences(home, config):
    options = [('check_updates', '启动检查更新', True)]
    while True:
        for i, (key, label, default) in enumerate(options, 1):
            notice(f"[{i}] {label}：{'开' if config.get(key, default) else '关'}")
        notice(f"[2] 默认快照导出目录：{config.get('export_dir') or '工作目录默认位置'}")
        notice('[0] 返回')
        choice = prompt('选择要修改的项目：').strip()
        if choice == '0':
            return
        if choice == '1':
            key, label, default = options[int(choice)-1]
            config[key] = not config.get(key, default)
        elif choice == '2':
            value = prompt('已有目录路径（- 恢复默认，回车保留）：').strip()
            if value == '-':
                config.pop('export_dir', None)
            elif value:
                folder = local_path(value)
                if not folder.is_dir():
                    notice('目录不存在或不可用，设置未修改。', 'warn')
                    continue
                config['export_dir'] = str(folder.resolve())
            else:
                continue
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
                mode = prompt('更新旧文件：[1] 覆盖删除  [2] 移入 backups  [0] 取消：').strip()
                if mode in ('1', '2') and prompt('除台账、设备、价格、配置及 backups 外，工作目录内容都将处理，继续？[y/N]：').strip().lower() == 'y':
                    safety.backup(home, 'upgrade')
                    if update.install(home, 'overwrite' if mode == '1' else 'archive'):
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
        notice(f"价格同步文件：{config.get('price_sync_file') or '未配置'}")
        notice(f"直接使用价格文件：{config.get('active_price_file') or '工作目录默认文件'}")
        notice(f"价格同步模式：{ {'append': '追加（冲突时停止）', 'overwrite': '覆盖同键', 'replace': '整体替换'} .get(config.get('price_sync_mode', 'append'), '无效')}")
        notice('[1] 交换目录\n[2] 共享标题与项目\n[3] 转发全部历史\n[4] 价格同步文件\n[5] 价格同步模式\n[6] 直接使用价格文件\n[0] 返回')
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
            elif choice == '4':
                value = prompt('价格同步文件路径（- 关闭，回车保留）：').strip()
                if value == '-':
                    config.pop('price_sync_file', None)
                elif value:
                    if config.get('active_price_file'):
                        raise ValueError('请先恢复本地价格文件，再启用旧版价格同步。')
                    from .prices import sync_file
                    path = local_path(value)
                    sync_file(home, path, config.get('price_sync_mode', 'append'))
                    config['price_sync_file'] = str(path.resolve())
            elif choice == '5':
                mode = prompt('1 追加  2 覆盖同键  3 整体替换  0 返回：').strip()
                if mode == '1':
                    config['price_sync_mode'] = 'append'
                elif mode == '2':
                    if prompt('覆盖本地同键价格，先备份，确认？[y/N]：').strip().lower() == 'y':
                        config['price_sync_mode'] = 'overwrite'
                elif mode == '3':
                    if prompt('整体替换本地价格，移除同步文件中没有的规则，先备份，确认？[y/N]：').strip().lower() == 'y':
                        config['price_sync_mode'] = 'replace'
                elif mode != '0':
                    raise ValueError('请选择 0–3')
            elif choice == '6':
                value = prompt('已有价格文件路径（- 恢复本地，回车保留）：').strip()
                if value == '-':
                    if config.get('active_price_file'):
                        from .prices import sync_file
                        try:
                            sync_file(home, config['active_price_file'], 'append')
                        except (ValueError, OSError) as exc:
                            notice(f'无法合并共享价格：{exc}；本地价格可能过期。', 'warn')
                            if prompt('仍使用本地价格？输入 LOCAL 确认：').strip() != 'LOCAL':
                                continue
                    config.pop('active_price_file', None)
                elif value:
                    from .prices import sync_file
                    path = local_path(value)
                    if config.get('active_price_file') and local_path(config['active_price_file']).resolve() == path.resolve():
                        if not path.is_file():
                            raise ValueError(f'价格文件不可用：{path}')
                        continue
                    if config.get('active_price_file') and local_path(config['active_price_file']).resolve() != path.resolve():
                        sync_file(home, config['active_price_file'], 'append')
                    sync_file(home, path, 'append')
                    config['active_price_file'] = str(path.resolve())
                    config.pop('price_sync_file', None)
            else:
                notice('请选择 0–6。', 'warn')
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


def session_menu():
    from . import sessions
    notice('会话来自当前 OpenCode 服务，可能与 OCU 配置的数据源不同。')
    while True:
        items = sessions.list_sessions()
        if not items:
            notice('本机 OpenCode 服务中没有会话。')
            return
        lines = ['  ' + line for line in sessions.table(items)]
        lines.extend(['  时间为本机时区；累计 token 包含输入、输出、推理及缓存。',
                      '  [o] 进入会话  [r] 重命名  [d] 编号删除  [b] 框选删除  [0] 返回'])
        sys.stdout.write('\n'.join(paint(line, '0') for line in lines) + '\n')
        sys.stdout.flush()
        choice = prompt('选择操作：').strip().lower()
        if choice == '0':
            return
        if choice not in ('o', 'r', 'd', 'b'):
            notice('请选择 o、r、d、b 或 0。', 'warn')
            continue
        if choice == 'b':
            indices = sessions.choose_checkboxes(items)
            if indices is None:
                notice('当前终端不支持框选，请改用编号输入。', 'warn')
                continue
            if not indices:
                notice('未选择会话。')
                continue
            selected = {items[i]['id'] for i in indices}
        else:
            label = '输入一个会话编号（回车取消）：' if choice in ('o', 'r') else '输入会话编号（例：2-3,5,7-9；回车取消）：'
            value = prompt(label).strip()
            if not value:
                continue
            try:
                if choice in ('o', 'r') and (not value.isascii() or not value.isdigit()):
                    raise ValueError('请选择一个会话')
                indices = sessions.parse_selection(value, len(items))
            except ValueError:
                notice('编号无效，未执行操作。', 'warn')
                continue
            selected = {items[i]['id'] for i in indices}
        if choice == 'o':
            if len(selected) != 1:
                notice('进入会话请选择一个编号。', 'warn')
                continue
            command = sessions.session_command(next(item for item in items if item['id'] in selected))
            notice('退出 OCU 并进入 OpenCode；退出会话后返回终端。')
            return command
        if choice == 'r':
            if len(selected) != 1:
                notice('重命名请选择一个会话。', 'warn')
                continue
            title = prompt('新标题（回车取消）：').strip()
            if title:
                sessions.local_service()
                sessions.api('patch', sessions.path(next(iter(selected))), {'title': title})
                notice('会话已重命名；刷新报告后更新看板。', 'ok')
            continue
        affected = sessions.descendants(items, selected)
        if affected & sessions.active_ids():
            notice('所选会话或其子会话正在运行，未执行删除。', 'warn')
            continue
        notice('将从 OpenCode 删除以下会话及其对话；OCU 已采集的用量保留：', 'warn')
        for item in items:
            if item['id'] in affected:
                notice(f"{item.get('title') or '未命名会话'} · {item['id']}")
        if prompt(f'共 {len(affected)} 个会话，输入 DELETE 确认：').strip() != 'DELETE':
            notice('已取消。')
            continue
        current = sessions.list_sessions()
        if sessions.descendants(current, selected) != affected or not selected <= {s['id'] for s in current}:
            notice('会话列表已变化，请重新选择。', 'warn')
            continue
        if affected & sessions.active_ids():
            notice('会话已开始运行，未执行删除。', 'warn')
            continue
        parents = {s['id']: s.get('parentID') for s in current}
        roots = []
        for sid in selected:
            ancestor, seen = parents.get(sid), set()
            while ancestor and ancestor not in seen and ancestor not in selected:
                seen.add(ancestor)
                ancestor = parents.get(ancestor)
            if ancestor not in selected:
                roots.append(sid)
        for sid in sorted(roots):
            try:
                sessions.api('delete', sessions.path(sid))
                notice(f'已删除 {sid} 及其子会话。', 'ok')
            except ValueError as exc:
                notice(f'{sid} 删除未确认：{exc}；请重新查看列表。', 'warn')
                break
        notice('刷新报告后更新看板；已采集的历史用量保留。')


def price_entry(home, device):
    """Prompt for one explicit, validated model price; never infer free rates."""
    from . import prices
    from .reference_costs import refresh as refresh_costs
    from .reporting import report_settings, validate
    from .workspace import active_price_path
    notice(f"模型价格文件：{active_price_path(home)}")
    groups = prices.missing_models(home)
    if not groups:
        notice('没有待补价记录；若尚未刷新，请先刷新台账。')
        return
    labels = {'input': '输入', 'output': '输出', 'reasoning': '推理', 'cache_read': '缓存读', 'cache_write': '缓存写'}
    for i, group in enumerate(groups, 1):
        fields = '、'.join(labels[k] for k in core.COUNTS if k in group['fields'])
        notice(f"[{i}] {group['provider']} / {group['model']} · {group['messages']:,} 条 · {group['tokens']:,} token · {group['first']} 至 {group['last']} · 涉及 {fields}")
    value = prompt('选择缺价模型编号，0 返回：').strip()
    if value in ('', '0'):
        return
    if not value.isdigit() or not 1 <= int(value) <= len(groups):
        raise ValueError('模型编号无效')
    group = groups[int(value)-1]
    mode = prompt('输入价格 [1]，标记该模型免费 [2]，0 返回：').strip()
    if mode in ('', '0'):
        return
    if mode not in ('1', '2'):
        raise ValueError('选项无效')
    currency = 'CNY'
    rates = {}
    if mode == '1':
        currency = (prompt('币种 CNY 或 USD [CNY]：').strip() or 'CNY').upper()
        for key in core.COUNTS:
            value = prompt(f'{labels[key]}单价（每百万 token；留空表示未知）：').strip()
            if value:
                rates[key] = float(value)
    else:
        if prompt('确认该模型所有 token 类别均免费？[y/N]：').strip().lower() != 'y':
            return
        rates = {key: 0 for key in core.COUNTS}
    effective = prompt(f"生效日期 YYYY-MM-DD，回车覆盖所有尚未定价历史 [{group['first']}]：").strip() or group['first']
    source = prompt('价格来源或备注（可留空）：').strip() or ('用户确认免费' if mode == '2' else '用户手动输入')
    from datetime import datetime, timezone
    rule = dict(provider=group['provider'], model=group['model'], effective_from=effective,
                currency=currency, source=source, updated_at=datetime.now(timezone.utc).isoformat(), **rates)
    current = report_settings(home)
    validate(dict(current, prices=[*current['prices'], rule]))
    count = prices.preview_rule(home, rule)
    notice(f'价格预览：{rule}；预计补算 {count} 条。已保存的参考费用不会改变；历史回算仅为估算。')
    if prompt('确认保存并补算？[y/N]：').strip().lower() != 'y':
        return
    prices.save_rule(home, rule)
    try:
        providers = json.loads((Path.home()/'.config/opencode/opencode.json').read_text(encoding='utf-8')).get('providers', {})
    except (OSError, ValueError, AttributeError):
        providers = {}
    before = sum(g['messages'] for g in prices.missing_models(home))
    refresh_costs(Path(home)/'ledger.db', home, report_settings(home), providers)
    after = sum(g['messages'] for g in prices.missing_models(home))
    notice(f'价格已保存；补算 {before-after} 条，仍有 {after} 条缺价。', 'ok')
    if show_report(device, open_report=False) is None:
        notice('价格已保存，但报告更新失败；可稍后刷新。', 'warn')


def data_menu(home, config, device):
    """Occasional data operations, separate from everyday report viewing."""
    while True:
        notice('数据')
        for label in ('[1] 导入', '[2] 同步设置',
                      '[3] 导出 CSV', '[4] 打开目录', '[5] 模型参考价格', '[6] 导出快照', '[0] 返回'):
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
                sync_preferences(home, config)
            elif choice == '3':
                actions.export_csv(home)
            elif choice == '4':
                notice(f'工作目录：{home}')
                actions.open_directory(home)
            elif choice == '5':
                from .prices import overview
                import shutil
                width = max(20, shutil.get_terminal_size((80, 24)).columns - 4)
                for line in overview(home, device, width=width):
                    print(paint('  ' + line, '0'))
                if prompt('[p] 补充缺价模型价格，回车返回数据菜单：').strip().lower() == 'p':
                    price_entry(home, device)
            elif choice == '6':
                complete = export_mode()
                if complete is not None:
                    notice(f"默认导出目录：{config.get('export_dir') or home/'manual-exports'}")
                    value = prompt('导出文件路径或已有目录（回车使用默认目录）：').strip()
                    dest = manual_export(home, device, config, value, complete)
                    notice(actions.export_summary(dest, complete))
                    notice(f'快照已导出：{dest}', 'ok')
            else:
                notice('请选择 0–6，待处理导入可选 r。', 'warn')
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
        from .reporting import initialize_prices
        initialize_prices(home)
        import threading
        operation_lock = threading.Lock()
        restart_required = threading.Event()
        def refresh_from_browser():
            if not operation_lock.acquire(blocking=False):
                raise ValueError('OCU 正在执行其他操作，请稍后再试。')
            try:
                if restart_required.is_set():
                    raise ValueError('请退出并重新运行 OCU。')
                return refresh(config, device, home, interactive=False)
            finally:
                operation_lock.release()
        def session_from_browser(action, data):
            from . import sessions, report_server
            if not operation_lock.acquire(blocking=False):
                raise ValueError('OCU 正在执行其他操作，请稍后再试。')
            try:
                if restart_required.is_set():
                    raise ValueError('请退出并重新运行 OCU。')
                return sessions.list_sessions() if action == 'list' else report_server.perform(action, data)
            finally:
                operation_lock.release()
        def settings_from_browser(action, data):
            from .reporting import settings as report_settings, validate
            if not operation_lock.acquire(blocking=False):
                raise ValueError('OCU 正在执行其他操作，请稍后再试。')
            try:
                if restart_required.is_set():
                    raise ValueError('请退出并重新运行 OCU。')
                if action == 'install-update':
                    if data.get('confirm') is not True:
                        raise ValueError('更新需要确认。')
                    mode = data.get('mode')
                    if mode not in ('overwrite', 'archive'):
                        raise ValueError('请选择覆盖或归档旧文件。')
                    safety.backup(home, 'upgrade')
                    cleanup_result = []
                    if not update.install(home, mode, cleanup_result):
                        raise ValueError('更新未完成，请检查终端输出。')
                    restart_required.set()
                    detail = cleanup_result[0] if cleanup_result else None
                    message = f'旧文件已移至 {detail}，请自行清理。' if isinstance(detail, Path) else str(detail or '更新已完成或准备就绪。')
                    return {'message': message + '请在终端退出 OCU 后重新运行。'}
                if action == 'migrate-workspace':
                    from .workspace import migrate
                    destination = data.get('path')
                    if data.get('confirm') is not True or not isinstance(destination, str) or not destination.strip():
                        raise ValueError('请输入新工作目录并确认迁移。')
                    safety.backup(home, 'migration')
                    target = migrate(home, local_path(destination.strip()))
                    restart_required.set()
                    return {'message': f'已切换至 {target}，旧目录保留。请在终端退出 OCU 后重新运行。'}
                if action == 'open-workspace':
                    if not actions.open_directory(home):
                        raise ValueError('无法打开工作目录。')
                    return {'message': '已打开工作目录。'}
                if action == 'export-csv':
                    actions.export_csv(home)
                    return {'message': f'CSV 已导出至 {home / "reports"}。'}
                if action == 'export-snapshot':
                    path, metadata = data.get('path'), data.get('include_metadata')
                    if not isinstance(path, str) or type(metadata) is not bool:
                        raise ValueError('导出路径或选项无效。')
                    dest = manual_export(home, device, config, path.strip(), metadata)
                    return {'message': f'快照已导出：{dest}。' + actions.export_summary(dest, metadata)}
                if action == 'export-dir-save':
                    value = data.get('path')
                    if not isinstance(value, str):
                        raise ValueError('导出目录无效。')
                    if value.strip():
                        folder = local_path(value.strip())
                        if not folder.is_dir():
                            raise ValueError('目录不存在或不可用，设置未修改。')
                        config['export_dir'] = str(folder.resolve())
                    else:
                        config.pop('export_dir', None)
                    core.atomic_json(home/'menu-config.json', config)
                    return {'message': '默认快照导出目录已保存。'}
                if action in ('prices-get', 'prices-preview', 'prices-save'):
                    from . import prices
                    from .workspace import active_price_path
                    from .reporting import report_settings as pricing_settings, validate as validate_prices
                    if action == 'prices-get':
                        groups = prices.missing_models(home)
                        for group in groups:
                            group['fields'] = sorted(group['fields'])
                        path = active_price_path(home)
                        rules = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
                        return {'path': str(path.resolve()), 'missing': groups, 'rules': rules}
                    rule = data.get('rule')
                    if not isinstance(rule, dict) or any(not isinstance(rule.get(k), str) or not rule[k].strip() for k in ('provider', 'model', 'effective_from', 'currency')):
                        raise ValueError('请输入供应商、模型、日期和币种。')
                    allowed = {'provider', 'model', 'effective_from', 'currency', 'source', *core.COUNTS}
                    if set(rule) - allowed or ('source' in rule and not isinstance(rule['source'], str)):
                        raise ValueError('价格规则包含无效字段。')
                    current = pricing_settings(home)
                    validate_prices(dict(current, prices=[*current['prices'], rule]))
                    count = prices.preview_rule(home, rule)
                    if action == 'prices-preview':
                        return {'count': count}
                    if data.get('confirm') is not True:
                        raise ValueError('保存价格需要确认。')
                    from datetime import datetime, timezone
                    rule = dict(rule, updated_at=datetime.now(timezone.utc).isoformat())
                    prices.save_rule(home, rule)
                    before = sum(g['messages'] for g in prices.missing_models(home))
                    from .reference_costs import refresh as refresh_costs
                    try:
                        providers = json.loads((Path.home()/'.config/opencode/opencode.json').read_text(encoding='utf-8')).get('providers', {})
                    except (OSError, ValueError, AttributeError):
                        providers = {}
                    refresh_costs(core.DEFAULT_LEDGER, home, pricing_settings(home), providers)
                    after = sum(g['messages'] for g in prices.missing_models(home))
                    rendered = actions.show_report(device, open_report=False)
                    return {'message': f'价格已保存；补算 {before-after} 条，仍有 {after} 条缺价。' + ('报告已更新，请刷新页面。' if rendered is not None else '报告更新失败，请稍后刷新。')}
                if action == 'backup-create':
                    from .safety import backup
                    path = backup(home, 'manual')
                    return {'message': f'备份已保存：{path}' if path else '暂无台账可备份。'}
                if action == 'backup-list':
                    from .safety import backups
                    return {'backups': [p.name for p in backups(home)]}
                if action == 'backup-restore':
                    from .safety import backups, restore
                    name = data.get('name')
                    if data.get('confirm') != 'RESTORE' or not isinstance(name, str):
                        raise ValueError('恢复需要输入 RESTORE 确认。')
                    selected = next((p for p in backups(home) if p.name == name), None)
                    if selected is None:
                        raise ValueError('备份不存在，请重新选择。')
                    restore(home, selected)
                    config.pop('processed_snapshots', None)
                    core.atomic_json(state_path(home, 'sync-state.json'), {'devices': {}})
                    if actions.show_report(device, open_report=False) is None:
                        return {'message': '台账已恢复，但报告生成失败；请在终端检查。'}
                    return {'message': '台账已恢复，刷新页面查看报告。'}
                if action == 'diagnose':
                    from .automation import diagnose
                    result = diagnose(home, config)
                    labels = {'source': '本机数据库', 'ledger': '历史台账', 'exchange': '交换目录'}
                    return {'message': '；'.join(f"{labels.get(c['name'], c['name'])}：{'正常' if c['ok'] else '异常'}" for c in result['checks'])}
                if action == 'device-save':
                    name = data.get('name')
                    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in name):
                        raise ValueError('设备名应不超过 80 字，且不含控制字符。')
                    name = name.strip()
                    with closing(core.connect(core.DEFAULT_LEDGER)) as c, c:
                        c.execute('UPDATE message_sources SET device_name=? WHERE device_id=?', (name, device))
                    config['device_name'] = name
                    config.setdefault('device_aliases', {})[device] = name
                    core.atomic_json(home/'menu-config.json', config)
                    return {'message': '设备名称已保存，刷新报告后生效。'}
                if action == 'source-save':
                    value = data.get('path')
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError('请输入数据库路径。')
                    db = local_path(value.strip())
                    if not db.is_file():
                        raise ValueError('文件不存在，配置未修改。')
                    with core.source_connection(db) as source:
                        core.source_rows(source)
                    config['db'] = str(db.resolve())
                    core.atomic_json(home/'menu-config.json', config)
                    return {'message': '数据源已保存，刷新报告后生效。'}
                if action in ('import-preview', 'import-confirm'):
                    value = data.get('path')
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError('请输入快照路径。')
                    source = local_path(value.strip())
                    snapshot = core.read_snapshot(source)
                    info = actions.preview(snapshot, core.DEFAULT_LEDGER)
                    digest = core.content_hash(snapshot)
                    if info['conflicts']:
                        raise ValueError('快照存在身份冲突，未写入。')
                    if action == 'import-preview':
                        return {'preview': info, 'digest': digest, 'legacy': snapshot['version'] == 1}
                    if data.get('digest') != digest:
                        raise ValueError('快照已变化，请重新预览。')
                    alias = data.get('alias')
                    if snapshot['version'] == 1:
                        if not isinstance(alias, str) or not alias.strip() or len(alias) > 80:
                            raise ValueError('请输入旧版快照来源名称。')
                        alias = alias.strip()
                    else:
                        alias = None
                    from .sync import accept
                    safety.backup(home, 'import')
                    result = accept(home, source, snapshot, core.DEFAULT_LEDGER, alias)
                    if alias:
                        config.setdefault('device_aliases', {})[snapshot['device']] = alias
                    config['last_import'] = str(source.resolve())
                    core.atomic_json(home/'menu-config.json', config)
                    return {'message': f"导入完成：新增 {result['added']}，更新 {result['updated']}，重复 {result['same']}。请刷新报告。"}
                if action == 'sync-save':
                    from .sync import validate_exchange
                    exchange = data.get('exchange')
                    metadata, relay = data.get('sync_metadata'), data.get('relay_history')
                    price_file = data.get('price_sync_file', config.get('price_sync_file', ''))
                    price_mode = data.get('price_sync_mode', config.get('price_sync_mode', 'append'))
                    if not isinstance(exchange, str) or type(metadata) is not bool or type(relay) is not bool:
                        raise ValueError('同步设置无效。')
                    if not isinstance(price_file, str):
                        raise ValueError('价格同步文件路径无效。')
                    if price_file.strip() and config.get('active_price_file'):
                        raise ValueError('请先恢复本地价格文件，再启用旧版价格同步。')
                    if price_mode not in ('append', 'overwrite', 'replace'):
                        raise ValueError('价格同步模式无效。')
                    if price_mode in ('overwrite', 'replace') and data.get('confirm_price_overwrite') is not True:
                        raise ValueError('覆盖价格规则需要确认。')
                    if metadata and data.get('confirm_metadata') is not True:
                        raise ValueError('共享标题和路径需要确认。')
                    if price_file.strip():
                        from .prices import sync_file
                        price_path_value = local_path(price_file.strip())
                        sync_file(home, price_path_value, price_mode)
                        config['price_sync_file'] = str(price_path_value.resolve())
                    else:
                        config.pop('price_sync_file', None)
                    config['price_sync_mode'] = price_mode
                    if exchange.strip():
                        folder = validate_exchange(home, local_path(exchange.strip()))
                        folder.mkdir(parents=True, exist_ok=True)
                        config['exchange'] = str(folder)
                    else:
                        config.pop('exchange', None)
                    config['sync_metadata'], config['relay_history'] = metadata, relay
                    core.atomic_json(home/'menu-config.json', config)
                    return {'message': '同步设置已保存。'}
                if action == 'price-file-save':
                    value = data.get('path')
                    if not isinstance(value, str):
                        raise ValueError('价格文件路径无效。')
                    if value.strip():
                        from .prices import sync_file
                        path = local_path(value.strip())
                        if config.get('active_price_file') and local_path(config['active_price_file']).resolve() == path.resolve():
                            if not path.is_file():
                                raise ValueError(f'价格文件不可用：{path}')
                            return {'message': '价格文件设置未改变。'}
                        if config.get('active_price_file') and local_path(config['active_price_file']).resolve() != path.resolve():
                            sync_file(home, config['active_price_file'], 'append')
                        sync_file(home, path, 'append')
                        config['active_price_file'] = str(path.resolve())
                        config.pop('price_sync_file', None)
                    else:
                        if config.get('active_price_file'):
                            from .prices import sync_file
                            try:
                                sync_file(home, config['active_price_file'], 'append')
                            except (ValueError, OSError) as exc:
                                if data.get('confirm_local') != 'LOCAL':
                                    raise ValueError(f'无法合并共享价格：{exc}；本地价格可能过期，需确认切回。') from exc
                        config.pop('active_price_file', None)
                    core.atomic_json(home/'menu-config.json', config)
                    return {'message': '价格文件设置已保存。'}
                current = report_settings(home)
                if action == 'settings-save':
                    candidate = {k: v for k, v in current.items() if k not in ('tz', 'label')}
                    for key in ('timezone_offset', 'usd_to_cny'):
                        value = data.get(key)
                        if type(value) not in (int, float):
                            raise ValueError('请输入有效的时区和汇率。')
                        candidate[key] = value
                    validate(candidate)
                    check_updates = data.get('check_updates')
                    if type(check_updates) is not bool:
                        raise ValueError('更新检查设置无效。')
                    core.atomic_json(home/'report-settings.json', candidate)
                    config['check_updates'] = check_updates
                    core.atomic_json(home/'menu-config.json', config)
                    current = report_settings(home)
                from .workspace import price_path
                return {'timezone_offset': current['timezone_offset'], 'usd_to_cny': current['usd_to_cny'],
                        'check_updates': config.get('check_updates', True), 'exchange': config.get('exchange', ''), 'price_sync_file': config.get('price_sync_file', ''), 'price_sync_mode': config.get('price_sync_mode', 'append'),
                        'sync_metadata': config.get('sync_metadata', False), 'relay_history': config.get('relay_history', False),
                        'device_name': config.get('device_name', ''), 'db': config.get('db', ''),
                        'workspace': str(home.resolve()), 'price_path': config.get('active_price_file') or str(price_path(home, 'model-prices.json').resolve()), 'active_price_file': config.get('active_price_file', ''), 'export_dir': config.get('export_dir', '')}
            finally:
                operation_lock.release()
        while True:
            summary = actions.summary(home)
            pending = len(list(inbox(home).glob('*.snapshot.json')))
            if pending:
                summary += f'\n待重试导入 {pending} 份 · 数据 → r'
            menu(__version__, config["device_name"], home.resolve(), '\n'.join(v for v in (status, summary) if v))
            choice = prompt("选择 [0-4/v/t/?]：").strip().lower()
            if restart_required.is_set() and choice != '0':
                notice('更新或迁移已完成，请选择 0 退出并重新运行 OCU。', 'warn')
                continue
            if choice in ('5', 'h', '?'):
                from .help import show
                show(home)
                continue
            if choice == '4':
                with operation_lock:
                    if restart_required.is_set():
                        notice('请退出并重新运行 OCU。', 'warn')
                        continue
                    if settings_menu(home, config, device):
                        return
                continue
            if choice == "0":
                with operation_lock:
                    pass
                return
            try:
                with operation_lock:
                    if restart_required.is_set():
                        notice('请退出并重新运行 OCU。', 'warn')
                        continue
                    if choice == "1":
                        refresh(config, device, home)
                    elif choice == 'v':
                        actions.view_report(home, refresh=refresh_from_browser, settings=settings_from_browser, active=lambda: not restart_required.is_set(), session_action=session_from_browser)
                    elif choice == 't':
                        actions.view_textboard(home)
                    elif choice == 'd':
                        actions.open_directory(home/'reports')
                    elif choice == "2":
                        data_menu(home, config, device)
                    elif choice == '3':
                        command = session_menu()
                        if command:
                            return command
                    else:
                        notice("请选择 0–4、v、t 或 ?。", "warn")
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
