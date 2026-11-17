"""A small, browser-free usage snapshot, sharing the dashboard's aggregates."""
from datetime import datetime, timedelta, timezone
import unicodedata

from .files import atomic_output
from .markdown import compact


def wrap(text, width):
    """Wrap by terminal cells and strip controls from untrusted metadata."""
    part, size = '', 0
    for char in str(text):
        if unicodedata.category(char).startswith('C'):
            char = ' '
        cells = 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
        if size + cells > width:
            yield part
            part, size = '', 0
        part += char
        size += cells
    yield part


def cell_width(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in str(text))


def columns(rows, align_right=()):
    widths = [max(cell_width(row[i]) for row in rows) for i in range(len(rows[0]))]
    result = []
    for row in rows:
        cells = []
        for i, value in enumerate(row):
            value = str(value)
            padding = ' ' * (widths[i] - cell_width(value))
            cells.append(padding + value if i in align_right else value + padding)
        result.append('  '.join(cells).rstrip())
    return result


def render(payload, now=None, width=10000):
    tz = timezone(timedelta(hours=payload.get('timezone_offset', 8)))
    now = now.astimezone(tz) if now is not None else datetime.now(tz)
    today = now.date()
    rows = payload['rows']
    days = sorted(payload['days'])
    total = sum(r['tot'] for r in rows)
    missing = sum(bool(r.get('cost_missing')) for r in rows)
    lines = [f"OCU  /  用量报告    {now:%Y-%m-%d %H:%M}  {payload.get('timezone', 'UTC+8')}",
             f"统计区间  {days[0]} — {days[-1]}" if days else '统计区间  暂无记录']
    lines += ['', '── 累计总览 ──',
              f"总 Token  {total:,}    参考费用  ¥{sum(r['cost'] for r in rows):,.4f}",
              f"消息  {len(rows):,}    会话  {len({r['sid'] for r in rows}):,}    活跃  {len(days):,} 天",
              f"输入  {compact(sum(r['in'] for r in rows))}    输出  {compact(sum(r['out'] for r in rows))}    推理  {compact(sum(r['reason'] for r in rows))}",
              f"缓存读  {compact(sum(r['cr'] for r in rows))}    缓存写  {compact(sum(r['cw'] for r in rows))}"]
    if not rows:
        lines += ['暂无用量记录；在主菜单选 1 刷新，或在数据菜单导入快照。']

    periods = [('范围', 'Token', '消息', '参考费用')]
    for label, span in (('今日', 1), ('近 7 天', 7), ('近 30 天', 30)):
        start = (today - timedelta(days=span-1)).isoformat()
        subset = [r for r in rows if start <= r['d'] <= today.isoformat()]
        periods.append((label, compact(sum(r['tot'] for r in subset)), f'{len(subset):,}', f"¥{sum(r['cost'] for r in subset):,.4f}"))
    lines += ['', '── 近期用量 ──', *columns(periods, (1, 2, 3))]

    daily = {d['date']: d for d in payload['activity_daily']}
    week = [(today - timedelta(days=n)).isoformat() for n in range(6, -1, -1)]
    maximum = max((daily.get(day, {}).get('total', 0) for day in week), default=0)
    lines += ['', '── 近 7 天趋势 ──']
    for day in week:
        n = daily.get(day, {}).get('total', 0)
        bar = '#' * max(1, round(n / maximum * 12)) if n and maximum else '-'
        lines += [f'{day}  {compact(n):>8}  {bar}']
    if maximum:
        peak = max(week, key=lambda day: daily.get(day, {}).get('total', 0))
        week_total = sum(daily.get(day, {}).get('total', 0) for day in week)
        lines += [f'日均 {compact(round(week_total / 7))} · 最高 {peak}  {compact(maximum)}']

    lines += ['', '── 模型 Top 5 · 累计 ──']
    model_rows = [('#', 'Token', '占比', '参考费用', '模型')]
    for i, model in enumerate(sorted(payload['models'], key=lambda m: -m['total'])[:5], 1):
        model_rows.append((f'{i}.', compact(model['total']), f"{model['total']/max(1, total):.1%}",
                           f"¥{model['cost']:,.4f}", f"{model['prov']}/{model['model']}"))
    if payload['models']:
        lines += columns(model_rows, (1, 2, 3))
    if not payload['models']:
        lines += ['暂无模型用量。']

    lines += ['', '── 最近会话 · Top 5 ──']
    recent = sorted(payload['sessions'], key=lambda s: s['last'], reverse=True)[:5]
    for i, session in enumerate(recent, 1):
        lines += [f"{i}. {session['last']}  {compact(session['total']):>8}  ¥{session['cost']:,.4f}  {session['title']}"]
    if not recent:
        lines += ['暂无有用量的会话。']

    devices = payload.get('devices', [])
    lines += ['', f'── 设备来源 · {len(devices)} 个（可能重叠） ──']
    for device in sorted(devices, key=lambda d: -d['total'])[:5]:
        lines += [f"{device['name']} · {compact(device['total'])} token · {device['messages']:,} 条"]
    if not devices:
        lines += ['暂无设备来源。']
    alerts = list(payload.get('warnings', []))
    if missing:
        models = sorted({(r['prov'], r['model']) for r in rows if r.get('cost_missing')})
        alerts += ['缺价模型：', *[f'  {provider} / {model}' for provider, model in models]]
    sync_devices = payload.get('sync', {}).get('devices', {}).values()
    stale = sum(bool(d.get('stale')) for d in sync_devices)
    failed = sum(d.get('collection_ok') is False for d in sync_devices)
    if stale:
        alerts += [f'{stale} 台设备超过 24 小时未检查或无新快照。']
    if failed:
        alerts += [f'{failed} 台设备最近一次采集失败，请检查对应设备。']
    if alerts:
        lines += ['', '── 数据状态 ──', *alerts]
    return '\n'.join(part for line in lines for part in wrap(line, max(1, width))) + '\n'


def write(payload, dest):
    with atomic_output(dest) as stream:
        stream.write(render(payload))
