#!/usr/bin/env python3
"""Regenerate the README dashboard image using fictional, isolated data."""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from opencode_usage import core, dashboard


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', default='/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge')
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / 'docs/images/dashboard.png'
    with TemporaryDirectory() as tmp:
        home = Path(tmp)
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        rows = []
        for day in range(30):
            for index in range(5 + day % 6):
                timestamp = start + timedelta(days=day, hours=2 + index * 2)
                rows.append(dict(zip(core.FIELDS, (
                    f'message-{day}-{index}', f'session-{day // 3}', int(timestamp.timestamp() * 1000),
                    'example', ('model-alpha', 'model-beta')[index % 2],
                    125000 + day * 4100, 15000 + index * 1200, 0, 80000 + index * 6000, 0, 0.12,
                ))))
        ledger, page = home / 'ledger.db', home / 'report.html'
        core.store(rows, ledger)
        dashboard.render(ledger, page, device_name='示例设备', local_sessions=[])
        subprocess.run([args.browser, '--headless', '--disable-gpu', '--hide-scrollbars',
                        '--force-device-scale-factor=1', '--window-size=1440,1500',
                        f'--user-data-dir={home / "browser"}', f'--screenshot={output}', page.as_uri()], check=True)
    print(f'已生成 {output}')


if __name__ == '__main__':
    main()
