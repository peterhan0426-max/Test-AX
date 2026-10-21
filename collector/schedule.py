"""Daily collection worker. Processes must be restarted after snapshot restore."""
import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from .native import PATHS, STORE_CODES
from .run import collect_job
from .credentials import version
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
    parser.add_argument('--no-catch-up', action='store_true', help='오늘 정시가 지났으면 다음 정시까지 대기')
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    marker = args.data_dir / 'scheduled-last-attempt.json'
    last = json.loads(marker.read_text())['date'] if marker.exists() else None
    previous_version = json.loads(marker.read_text()).get('credential_version') if marker.exists() else None
    now = datetime.now(KST)
    if args.no_catch_up and now >= now.replace(hour=args.hour, minute=args.minute, second=0, microsecond=0):
        last = now.date().isoformat()
        previous_version = version(args.data_dir)
    print(f'자동 수집 대기: 매일 {args.hour:02}:{args.minute:02} 한국 시간', flush=True)
    try:
        while True:
            credential_version = version(args.data_dir)
            if credential_version is None:
                time.sleep(10)
                continue
            if credential_version != previous_version:
                last = None
                previous_version = credential_version
            now = datetime.now(KST)
            delay = due_in(now, last, args.hour, args.minute)
            if delay:
                time.sleep(min(30, delay))
                continue
            current = now.strftime('%Y-%m')
            previous = (now.date().replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
            result = collect_job(args.data_dir, [previous, current], list(STORE_CODES), list(PATHS))
            last = now.date().isoformat()
            marker.write_text(json.dumps({'date': last, 'failed': bool(result['failures']),
                                         'credential_version': credential_version}), encoding='utf-8')
            # Do not repeatedly retry a failed password/permission without a changed prerequisite.
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
