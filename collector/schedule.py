"""Daily collection worker. Processes must be restarted after snapshot restore."""
import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from .native import PATHS, STORE_CODES
from .run import collect_job
from saenong.data import KST


def due_in(now, last_attempt, hour, minute):
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= scheduled and last_attempt != now.date().isoformat():
        return 0
    if now >= scheduled:
        scheduled += timedelta(days=1)
    return max(0, (scheduled - now).total_seconds())


def main():
    parser = argparse.ArgumentParser(description='새농 매일 자동 수집 (한국 시간)')
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--hour', type=int, default=8, choices=range(24))
    parser.add_argument('--minute', type=int, default=0, choices=range(60))
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    marker = args.data_dir / 'scheduled-last-attempt.json'
    last = json.loads(marker.read_text())['date'] if marker.exists() else None
    print(f'자동 수집 대기: 매일 {args.hour:02}:{args.minute:02} 한국 시간', flush=True)
    try:
        while True:
            now = datetime.now(KST)
            delay = due_in(now, last, args.hour, args.minute)
            if delay:
                time.sleep(min(30, delay))
                continue
            current = now.strftime('%Y-%m')
            previous = (now.date().replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
            result = collect_job(args.data_dir, [previous, current], list(STORE_CODES), list(PATHS))
            last = now.date().isoformat()
            marker.write_text(json.dumps({'date': last, 'failed': bool(result['failures'])}), encoding='utf-8')
            # Do not repeatedly retry a failed password/permission without a changed prerequisite.
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
