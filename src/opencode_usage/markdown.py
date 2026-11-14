"""Terminal-friendly companion to the HTML dashboard (standard library only)."""
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from .files import atomic_output


def cell(value):
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")


def table(headers, rows):
    return "\n".join(["| " + " | ".join(map(cell, headers)) + " |", "| " + " | ".join("---" for _ in headers) + " |", *["| " + " | ".join(map(cell, row)) + " |" for row in rows]])


def compact(n):
    for scale, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= scale:
            return f"{n/scale:.2f}{suffix}"
    return f"{n:,}"


def write(payload, dest):
    rows, models, sessions = payload["rows"], payload["models"], payload["sessions"]
    total = sum(r["tot"] for r in rows)
    cost = sum(r["cost"] for r in rows)
    days = sorted(payload["days"])
    lines = ["# OpenCode 用量报告", "", f"生成时间：{datetime.now():%Y-%m-%d %H:%M}（本机时间）；数据日期及小时：北京时间。", "", f"统计范围：{days[0] if days else '无数据'} 至 {days[-1] if days else '无数据'}。汇总本机与已成功导入的累计历史。", "", "## 总览", "", table(["指标", "数值"], [
        ("总 token", f"{total:,}"), ("输入", f"{sum(r['in'] for r in rows):,}"), ("输出", f"{sum(r['out'] for r in rows):,}"), ("推理", f"{sum(r['reason'] for r in rows):,}"), ("缓存读", f"{sum(r['cr'] for r in rows):,}"), ("缓存写", f"{sum(r['cw'] for r in rows):,}"), ("消息 / 会话 / 活跃天数", f"{len(rows):,} / {len(sessions):,} / {len(days):,}"), ("参考花费", f"¥{cost:.4f}")]), "", "## 时间范围", ""]
    lines[-2] = "## 设备来源"
    lines += [table(["设备", "消息数", "Token", "参考花费"], [(d["name"], d["messages"], f"{d['total']:,}", f"¥{d['cost']:.4f}") for d in payload.get("devices", [])]), ""]
    periods = []
    end = datetime.strptime(days[-1], "%Y-%m-%d") if days else None
    for label, span in (("全部", None), ("近 30 天", 30), ("近一周", 7)):
        start = max(days[0], (end - timedelta(days=span-1)).strftime("%Y-%m-%d")) if span and end else days[0] if days else ""
        subset = [r for r in rows if r["d"] >= start]
        periods.append((label, start or "—", days[-1] if days else "—", f"{sum(r['tot'] for r in subset):,}", f"¥{sum(r['cost'] for r in subset):.4f}"))
    lines += ["## 时间范围", "", table(["范围", "起始", "结束", "总 token", "参考花费"], periods)]
    for heading, key, fmt in (("模型 token 分布", "total", compact), ("模型花费分布", "cost", lambda n: f"¥{n:.4f}")):
        sorted_models = sorted(models, key=lambda m: -m[key])
        denominator = sum(m[key] for m in models)
        distribution = [(m["prov"] + " / " + m["model"], m[key]) for m in sorted_models[:5]]
        if len(sorted_models) > 5:
            distribution.append(("其他模型", sum(m[key] for m in sorted_models[5:])))
        lines += ["", f"## {heading}", "", table(["模型", "用量" if key == "total" else "参考花费", "占比"], [(name, fmt(n), f"{n/denominator*100:.2f}%" if denominator else "0.00%") for name, n in distribution])]
    daily = payload["activity_daily"]
    maximum = max((d["total"] for d in daily), default=0)
    lines += ["", "## 每日 token 与花费", "", "仅列有记录的日期；条形相对最高日，便于纯文本阅读。", "", table(["日期", "Token", "参考花费", "相对用量"], [(d["date"], f"{d['total']:,}", f"¥{d['cost']:.4f}", "█" * max(1, round(d["total"] / maximum * 20)) if d["total"] and maximum else "") for d in daily])]
    matrix = defaultdict(int)
    for h in payload["activity_hours"]:
        matrix[(h["weekday"], h["hour"])] += h["total"]
    lines += ["", "## 活跃时段", "", "每格为对应星期、小时的累计 token；零用量显示 —。", ""]
    for start in (0, 12):
        lines += [table(["星期 / 小时", *[f"{h:02d}" for h in range(start, start+12)]], [(name, *[compact(matrix[(w, h)]) if matrix[(w, h)] else "—" for h in range(start, start+12)]) for w, name in enumerate(("周一", "周二", "周三", "周四", "周五", "周六", "周日"))]), ""]
    lines += ["## 模型排行", "", "与看板一致，排除输入和输出均为零的消息。", "", table(["提供商 / 模型", "Token", "消息数", "参考花费"], [(m["prov"] + " / " + m["model"], f"{m['total']:,}", m["messages"], f"¥{m['cost']:.4f}") for m in models]), "", "## 历史会话排行（前 20）", "", table(["会话", "会话 ID", "Token", "参考花费", "最后消息"], [(s["title"], s["id"], f"{s['total']:,}", f"¥{s['cost']:.4f}", s["last"]) for s in sessions[:20]]), "", "## 本机现存会话", "", "仅包含本机当前源库中的会话，归档和子会话也包含；不重复计入累计用量。", "", table(["会话", "模型", "Token", "状态", "最近更新"], [(s["会话标题"], s["模型"], f"{s['总token']:,}", s["状态"], s["最近更新北京时间"]) for s in payload["current_sessions"]]), "", "## 文件与统计口径", "", "- 完整消息总表：`total.csv`；费用对照：`reference-costs.csv`；交互看板：`report.html`。", "- 总台账只增不删；各类 token 和原费用保留历史最大值，不等于账单。", "- 参考费用沿用本地旧版人民币规则（1 USD = 6.666 CNY），并非实时价格。", "- DeepSeek 历史价格和节假日覆盖有限，未知日期按峰价；Reverix 零费用估算依赖本机价格配置，缺价可能仍显示零。", "- 免费渠道：relay-ai、intern、paratera。本地会话标题和路径可能敏感，请勿公开上传报告。", ""]
    lines += ["## 项目与会话设备来源", "", table(["项目", "设备来源", "Token"], [(p["name"], p.get("devices", "来源未知"), f"{p['total']:,}") for p in payload["projects"]]), "", table(["会话 ID", "会话", "设备来源"], [(s["id"], s["title"], s.get("devices", "来源未知")) for s in sessions[:20]]), ""]
    # Keep essential caveats once; long-form methodology belongs in repository docs.
    lines = [line for line in lines if line not in {
        "仅列有记录的日期；条形相对最高日，便于纯文本阅读。",
        "每格为对应星期、小时的累计 token；零用量显示 —。",
        "与看板一致，排除输入和输出均为零的消息。",
        "仅包含本机当前源库中的会话，归档和子会话也包含；不重复计入累计用量。",
    }]
    begin = lines.index("## 文件与统计口径")
    end = lines.index("## 项目与会话设备来源")
    lines[begin:end] = []
    lines += ["参考费用非账单 · 历史高水位统计 · 设备分项可能重叠。", "完整数据：total.csv · 费用对照：reference-costs.csv · 看板：report.html", ""]
    lines = [f"## 本机现存会话 · {cell(payload.get('local_device', '本机'))}" if line == "## 本机现存会话" else line for line in lines]
    lines[2:2] = [cell(w) for w in payload.get('warnings', [])]
    lines = [line.replace('数据日期及小时：北京时间', '数据日期及小时：' + payload.get('timezone', 'UTC+8')) for line in lines]
    missing = sum(bool(r.get('cost_missing')) for r in rows)
    if missing:
        lines += [f'参考费用缺价 {missing} 条；费用汇总仅包含已知部分，不应将未知视为免费。', '']
    with atomic_output(dest) as f:
        f.write("\n".join(lines))
