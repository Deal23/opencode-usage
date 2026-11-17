"""Ask a user-selected OpenCode model for reviewable price proposals."""
import json
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from . import core
from .reporting import initialize_prices, validate, DEFAULTS


def models():
    result = subprocess.run(['opencode', 'models'], capture_output=True, text=True, check=True, timeout=60)
    return sorted({line.strip() for line in result.stdout.splitlines() if '/' in line and not any(c.isspace() for c in line.strip())})


def propose(home, selected, targets):
    prompt = ('检索以下供应商和模型的官方 API Token 价格。仅返回 JSON 数组，'
              '每项包含 provider, model, effective_from（YYYY-MM-DD）, currency（CNY或USD）, '
              'input, output, reasoning, cache_read, cache_write（每百万 token；未知省略）, '
              'source（官方网页完整 HTTPS URL）。仅对能核实的模型返回，不猜测。'
              'DeepKey 按模型官方价一倍；订阅模型用对应官方 API 等价价。'
              '不要读写文件，不要执行命令。目标：' + json.dumps(targets, ensure_ascii=False))
    result = subprocess.run(['opencode', 'run', '--archived', '--model', selected, '--format', 'json', prompt],
                            capture_output=True, text=True, check=True, timeout=300, cwd=str(home))
    texts = []
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') == 'text' and isinstance(event.get('part'), dict):
            texts.append(event['part'].get('text', ''))
    text = '\n'.join(texts).strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    proposals = json.loads(text)
    if not isinstance(proposals, list) or not proposals:
        raise ValueError('AI 未返回可用价格数组')
    allowed = {(item['provider'], item['model']) for item in targets}
    seen = set()
    for item in proposals:
        if not isinstance(item, dict) or (item.get('provider'), item.get('model')) not in allowed:
            raise ValueError('AI 返回了不在目标列表中的模型')
        key = (item['provider'], item['model'], item.get('effective_from'))
        if key in seen:
            raise ValueError('AI 返回重复价格规则')
        seen.add(key)
        url = item.get('source', '')
        if not isinstance(url, str) or urlparse(url).scheme != 'https' or not urlparse(url).hostname:
            raise ValueError('价格规则必须包含 HTTPS 来源')
        if not any(item.get(k) is not None for k in core.COUNTS):
            raise ValueError('价格规则没有任何单价')
    validate(dict(DEFAULTS, prices=proposals))
    return proposals


def save(home, proposals):
    path = initialize_prices(home)
    current = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(current, list):
        raise ValueError('价格文件必须是数组')
    keys = {(p['provider'], p['model'], p['effective_from']) for p in proposals}
    merged = [p for p in current if (p['provider'], p['model'], p['effective_from']) not in keys] + proposals
    validate(dict(DEFAULTS, prices=merged))
    core.atomic_json(path, merged)
    return path
