"""The original ocstats dashboard, fed by the cumulative OCU ledger."""
import csv
import html
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import core
from .core import metadata_schema
from .files import atomic_output

FX = 6.666
BEIJING = timezone(timedelta(hours=8))


def migrate_legacy(source, ledger, device_id=None, device_name=None):
    """Read-only migration. Usage and metadata land in the independent OCU ledger."""
    count = core.import_legacy(source, ledger)
    with closing(core.readonly(source)) as old:
        tables = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = old.execute("SELECT session_id,title,directory,project,duration_min,time_created,time_updated,last_seen FROM session_meta").fetchall() if "session_meta" in tables else []
    with closing(core.connect(ledger)) as c, c:
        metadata_schema(c)
        c.executemany("INSERT OR IGNORE INTO session_meta VALUES (?,?,?,?,?,?,?,?)", rows)
        if device_id:
            with closing(core.readonly(source)) as old:
                mids = [r[0] for r in old.execute("SELECT message_id FROM usage")]
            c.executemany("INSERT OR IGNORE INTO message_sources VALUES (?,?,?)", [(mid, device_id, device_name or device_id) for mid in mids])
    return count


def refresh_metadata(source, ledger):
    """Keep local session metadata without exporting private titles or paths."""
    with core.source_connection(source) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        table = "session_v2" if "session_v2" in tables else "session" if "session" in tables else None
        rows = c.execute(f"SELECT id,title,directory,time_created,time_updated FROM {table}").fetchall() if table else []
    with closing(core.connect(ledger)) as c, c:
        metadata_schema(c)
        for sid, title, directory, created, updated in rows:
            c.execute("INSERT INTO session_meta VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET title=excluded.title,directory=excluded.directory,project=excluded.project,time_updated=excluded.time_updated,last_seen=excluded.last_seen", (sid,title,directory,directory,None,created,updated,int(datetime.now().timestamp()*1000)))


def estimate(row, providers, settings=None):
    """Port the local report's reference pricing, without changing stored cost."""
    fx = settings['usd_to_cny'] if settings else FX
    if settings:
        date = datetime.fromtimestamp(row['created_ms']/1000, settings['tz']).date().isoformat()
        prices = [p for p in settings['prices'] if p['provider'] == row['provider'] and p['model'] == row['model'] and p['effective_from'] <= date]
        if prices:
            price = max(prices, key=lambda p: p['effective_from'])
            if any(row[k] and price.get(k) is None for k in core.COUNTS):
                return 0.0, True
            return sum(row[k] * (price.get(k) or 0) for k in core.COUNTS)/1e6, False
    if row["provider"] in (settings['free_providers'] if settings else {"relay-ai", "intern", "paratera"}):
        return 0.0, False
    if row["provider"] == "deepseek" and not row["cache_write"]:
        rates = {"deepseek-flash": ((1, .02, 4), (2, .04, 8)), "deepseek-v4-pro": ((4.5, .15, 13.5), (9, .30, 27))}
        if row["model"] in rates:
            local = datetime.fromtimestamp(row["created_ms"] / 1000, BEIJING)
            day = local.strftime("%Y-%m-%d")
            holidays = {"2026-09-25", *[f"2026-10-{d:02d}" for d in range(1, 8)]}
            peak = not "2026-09-25" <= day <= "2026-12-31" or (day not in holidays and local.weekday() < 5 and (9 <= local.hour < 12 or 14 <= local.hour < 18))
            inp, cache, output = rates[row["model"]][int(peak)]
            return (row["input"] * inp + row["cache_read"] * cache + row["output"] * output) / 1e6, False
    if row["provider"].startswith("reverix") and row["cost"] == 0 and sum(row[k] for k in core.COUNTS):
        prices = providers.get(row["provider"], {}).get("models", {}).get(row["model"], {}).get("cost")
        prices = [prices] if isinstance(prices, dict) else prices
        if not isinstance(prices, list):
            return 0.0, True
        context = row["input"] + row["cache_read"] + row["cache_write"]
        tiers = [p for p in prices if isinstance(p, dict) and isinstance(p.get("tier"), dict) and p["tier"].get("type") == "context" and isinstance(p["tier"].get("size"), (int, float)) and context > p["tier"]["size"]]
        price = max(tiers, key=lambda p: p["tier"]["size"]) if tiers else next((p for p in prices if isinstance(p, dict) and not p.get("tier")), {})
        cache = price.get("cache") or {}
        counts = [row["input"], row["output"] + row["reasoning"], row["cache_read"], row["cache_write"]]
        rates = [price.get("input"), price.get("output"), cache.get("read"), cache.get("write")]
        if any(n and not isinstance(rate, (int, float)) for n, rate in zip(counts, rates)):
            return 0.0, True
        return sum(n * (rate or 0) for n, rate in zip(counts, rates)) / 1e6 * fx, False
    return row["cost"] * fx, row['cost'] == 0 and any(row[k] for k in core.COUNTS)


def current_sessions(db):
    """Read current local sessions from the live source, never the historical ledger."""
    if db is None or (not isinstance(db, sqlite3.Connection) and not Path(db).is_file()):
        return []
    with core.source_connection(db) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "session_v2" not in tables:
            return []
        rows = c.execute("SELECT id,title,directory,model,parent_id,time_archived,time_suspended,time_updated,cost,tokens_input+tokens_output+tokens_reasoning+tokens_cache_read+tokens_cache_write FROM session_v2 ORDER BY time_updated DESC").fetchall()
    result = []
    for sid, title, directory, model, parent, archived, suspended, updated, cost, total in rows:
        try:
            ref = json.loads(model or "{}")
            name = "/".join(filter(None, [ref.get("providerID"), ref.get("id")]))
        except (ValueError, AttributeError, TypeError):
            name = ""
        status = "已归档" if archived is not None else "已暂停" if suspended is not None else "未归档"
        if parent:
            status += " · 子会话"
        result.append({"会话ID": sid, "会话标题": title or "", "目录": directory or "", "模型": name, "总token": total or 0, "已记录花费CNY": (cost or 0) * FX, "状态": status, "最近更新北京时间": datetime.fromtimestamp(updated / 1000, BEIJING).strftime("%Y-%m-%d %H:%M") if updated else ""})
    return result


def render(ledger, dest, db=None, device_name="本机", local_sessions=None, warnings=None, include_csv=False, csv_only=False):
    from .reporting import settings as read_settings
    settings = read_settings(Path(ledger).parent)
    with closing(core.readonly(ledger)) as c:
        rows = [dict(zip(core.FIELDS, r)) for r in c.execute("SELECT " + ",".join(core.FIELDS) + " FROM usage ORDER BY created_ms DESC")]
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        meta = {r[0]: r[1:] for r in c.execute("SELECT session_id,title,project FROM session_meta")} if "session_meta" in tables else {}
        sources = {}
        if "message_sources" in tables:
            for mid, did, name in c.execute("SELECT message_id,device_id,device_name FROM message_sources"):
                sources.setdefault(mid, []).append((did, name))
    try:
        providers = json.loads((Path.home() / ".config/opencode/opencode.json").read_text(encoding='utf-8')).get("providers", {})
    except (OSError, ValueError):
        providers = {}
    records, models, sessions, projects, daily, hours = [], {}, {}, {}, {}, defaultdict(int)
    missing = 0
    for r in rows:
        local = datetime.fromtimestamp(r["created_ms"] / 1000, settings['tz'])
        date, hour = local.strftime("%Y-%m-%d"), local.hour
        total = sum(r[k] for k in core.COUNTS)
        cost, uncovered = estimate(r, providers, settings)
        missing += uncovered
        title, project = meta.get(r["session_id"], ("", ""))
        title, project = title or "", project or ""
        rec = dict(t=local.strftime("%Y-%m-%d %H:%M"), d=date, h=hour, sid=r["session_id"], title=title, proj=project, prov=r["provider"], model=r["model"], agent="", reason=r["reasoning"], cr=r["cache_read"], cw=r["cache_write"], tot=total, cost=cost, dur=None)
        rec.update({"in": r["input"], "out": r["output"]})
        rec['original_cost'] = r['cost']
        rec['cost_missing'] = bool(uncovered)
        rec['device_ids'] = [did for did, _ in sources.get(r['message_id'], [('unknown', '来源未知')])]
        records.append(rec)
        rec["devices"] = ", ".join(name for _, name in sources.get(r["message_id"], [])) or "来源未知"
        if total:
            m = models.setdefault((r["provider"], r["model"]), dict(prov=r["provider"], model=r["model"], total=0, input=0, output=0, cache=0, messages=0, cost=0, est=None, share=0))
            for field, value in (("total", total), ("input", r["input"]), ("output", r["output"]), ("cache", r["cache_read"]), ("messages", 1), ("cost", cost)):
                m[field] += value
        s = sessions.setdefault(r["session_id"], dict(id=r["session_id"], title=title or "未命名会话", project=project or "未知目录", models=set(), messages=0, total=0, input=0, output=0, cache=0, cost=0, last=rec["t"]))
        s["models"].add(r["provider"] + "/" + r["model"])
        s.setdefault("device_names", set()).update(name for _, name in sources.get(r["message_id"], [("unknown", "来源未知")]))
        for field, value in (("total", total), ("input", r["input"]), ("output", r["output"]), ("cache", r["cache_read"]), ("messages", 1), ("cost", cost)):
            s[field] += value
        p = projects.setdefault(project or "未知目录", dict(name=project or "未知目录", total=0, messages=0, sessions=set()))
        p["total"] += total
        p["messages"] += 1
        p["sessions"].add(r["session_id"])
        p.setdefault("device_names", set()).update(name for _, name in sources.get(r["message_id"], [("unknown", "来源未知")]))
        d = daily.setdefault(date, dict(date=date, total=0, cost=0, **{k: 0 for k in core.COUNTS}))
        d["total"] += total
        d["cost"] += cost
        for k in core.COUNTS:
            d[k] += r[k]
        hours[(date, local.weekday(), hour)] += total
    for s in sessions.values():
        s["models"] = ", ".join(sorted(s["models"]))
        s["devices"] = ", ".join(sorted(s.pop("device_names")))
    for p in projects.values():
        p["sessions"] = len(p["sessions"])
        p["devices"] = ", ".join(sorted(p.pop("device_names")))
    model_total = sum(m["total"] for m in models.values())
    for m in models.values():
        m["share"] = m["total"] / max(1, model_total) * 100
    payload = dict(rows=records, current_sessions=current_sessions(db) if local_sessions is None else local_sessions, models=sorted(models.values(), key=lambda m: -m["total"]), projects=list(projects.values()), sessions=sorted(sessions.values(), key=lambda s: -s["total"]), days=sorted(daily), daily=[daily[d] for d in sorted(daily)], activity_daily=[daily[d] for d in sorted(daily)], activity_hours=[dict(date=d, weekday=w, hour=h, total=n) for (d, w, h), n in hours.items()])
    devices = {}
    for r, rec in zip(rows, records):
        for did, name in sources.get(r["message_id"], [("unknown", "来源未知")]):
            d = devices.setdefault(did, dict(id=did, name=name, messages=0, total=0, cost=0))
            d["messages"] += 1
            d["total"] += rec["tot"]
            d["cost"] += rec["cost"]
    payload["sessions"] = [s for s in payload["sessions"] if s["total"] > 0]
    for session in payload["current_sessions"]:
        session["设备"] = device_name
    payload["local_device"] = device_name
    payload['timezone'] = settings['label']
    payload['timezone_offset'] = settings['timezone_offset']
    payload['warnings'] = warnings or []
    payload['completeness'] = dict(messages=len(rows),
        attributed=sum(bool(sources.get(r['message_id'])) for r in rows),
        sessions=len({r['session_id'] for r in rows}),
        titled=sum(bool(meta.get(sid, ('', ''))[0]) for sid in {r['session_id'] for r in rows}),
        projects=sum(bool(meta.get(sid, ('', ''))[1]) for sid in {r['session_id'] for r in rows}))
    from .reporting import DEFAULTS
    import hashlib
    price_settings = {key: settings[key] for key in DEFAULTS if key != 'timezone_offset'}
    payload['pricing_version'] = hashlib.sha256(json.dumps(price_settings, sort_keys=True).encode()).hexdigest()[:12]
    from .sync import status
    payload['sync'] = status(Path(ledger).parent)
    payload["projects"].sort(key=lambda p: (-p["total"], p["name"]))
    payload["devices"] = list(devices.values())
    payload["message_sources"] = {mid: ", ".join(name for _, name in ds) for mid, ds in sources.items()}
    if csv_only:
        write_csv(rows, records, payload['message_sources'], Path(dest).parent)
        return payload
    total = sum(r["tot"] for r in records)
    cost = sum(r["cost"] for r in records)
    cache = sum(r["cr"] for r in records)
    kpis = [("总 token", f"{total:,}", f"{len(daily)} 个活跃日"), ("输出 token", f"{sum(r['out'] for r in records):,}", f"输入 {sum(r['in'] for r in records):,}"), ("缓存读", f"{cache:,}", f"占总 token {cache/max(1,total)*100:.0f}%"), ("消息数", f"{len(rows):,}", f"{len(sessions)} 个会话"), ("参考花费", f"¥{cost:,.4f}", "沿用本地参考价格口径"), ("原记录费用", f"${sum(r['cost'] for r in rows):,.4f}", "原始美元值，未改写")]
    values = dict(START=min(daily, default="无数据"), END=max(daily, default="无数据"), GENERATED=datetime.now().strftime("%Y-%m-%d %H:%M"), FX=str(FX), MISSING=str(missing), DATA=json.dumps(payload, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c"), KPIS="".join(f'<div class="kpi"><div class="k-label">{html.escape(k)}</div><div class="k-value">{html.escape(v)}</div><div class="k-sub">{html.escape(note)}</div></div>' for k, v, note in kpis))
    template = Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")
    # Source session timestamps remain explicitly labelled Beijing time.
    template = template.replace('·　北京时间', '·　' + settings['label'])
    template = template.replace('时间（北京时间）', '时间（' + settings['label'] + '）')
    template = template.replace('最后消息（北京时间）', '最后消息（' + settings['label'] + '）')
    values['FX'] = str(settings['usd_to_cny'])
    # One pass prevents user-provided text from being interpreted as template tokens.
    import re
    page = re.sub(r"@@([A-Z]+)@@", lambda m: values[m[1]], template)
    dest = Path(dest)
    with atomic_output(dest) as f:
        f.write(page)
    from .markdown import write
    write(payload, dest.with_suffix(".md"))
    if include_csv:
        write_csv(rows, records, payload['message_sources'], dest.parent)
    return payload


def write_csv(rows, records, sources, folder):
    """Export raw usage and reference prices on explicit request."""
    dest = Path(folder)/'report.html'
    with atomic_output(dest.with_name('total.csv'), encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([*core.FIELDS, '设备来源'])
        for r in sorted(rows, key=lambda r: r['message_id']):
            values = [r[k] for k in core.FIELDS] + [sources.get(r['message_id'], '来源未知')]
            writer.writerow(["'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@')) else v for v in values])
    with atomic_output(dest.with_name("reference-costs.csv"), encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["message_id", "原费用USD", "参考费用CNY", "参考费用状态"])
        writer.writerows(("'" + r['message_id'] if r['message_id'].startswith(('=', '+', '-', '@')) else r['message_id'], r["cost"], '' if rec['cost_missing'] else rec["cost"], '缺价' if rec['cost_missing'] else '已知') for r, rec in zip(rows, records))
