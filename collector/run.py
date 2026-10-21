"""Repeatable collection jobs. A scheduler invokes --once; history is explicit."""
import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from .native import Client, PATHS, STORE_CODES
from saenong.data import KST, month_end, active_reports


def periods(first, last):
    current = date.fromisoformat(first + '-01')
    end = date.fromisoformat(last + '-01')
    if current > end:
        raise ValueError('시작 월이 종료 월보다 늦습니다.')
    while current <= end:
        yield current.strftime('%Y-%m')
        current = month_end(current.strftime('%Y-%m')) + timedelta(days=1)


def collect_job(root, months, stores, kinds, refresh=True, skip_existing=False):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    run = {'started_at': datetime.now(KST).isoformat(), 'results': [], 'failures': [], 'no_data': []}
    stage = 'authentication'
    network_failures = 0
    try:
        client = Client(root, refresh=refresh)
        stage = 'collection'
        existing = {(row['kind'], row['store'], row['period']) for row in active_reports(root)
                    if row['as_of'] == month_end(row['period']).isoformat()
                    and (row['kind'] != 'monthly-profit' or json.loads(row['data']).get('source_query', {}).get('check_yn') == '5')} if skip_existing else set()
        today = datetime.now(KST).date()
        for month in months:
            end = min(month_end(month), today).isoformat()
            for store in stores:
                for kind in kinds:
                    if (kind, store, month) in existing:
                        print(f'CACHED {store} {month} {kind}', flush=True)
                        continue
                    try:
                        result = client.collect(kind, store, month, end)
                        network_failures = 0
                        run['results'].append({key: result[key] for key in ('kind', 'store', 'period', 'as_of', 'rows', 'duplicate')})
                        print(f'OK {store} {month} {kind} {result["rows"]}행', flush=True)
                    except Exception as error:
                        # Never print HTTP errors that may contain URLs or session details.
                        reason = str(error) if isinstance(error, ValueError) else type(error).__name__
                        if kind in ('promotion-summary', 'promotion-items') and isinstance(error, ValueError) and '원본이 매출내역 없음' in reason:
                            run['no_data'].append({'store': store, 'period': month, 'kind': kind})
                            print(f'NO DATA {store} {month} {kind}', flush=True)
                            continue
                        run['failures'].append({'store': store, 'period': month, 'kind': kind, 'reason': reason})
                        print(f'FAIL {store} {month} {kind}: {reason}', flush=True)
                        if isinstance(error, (TimeoutError, ConnectionError)):
                            network_failures += 1
                            if network_failures >= 2:
                                raise RuntimeError('연속 연결 실패로 수집을 중단했습니다.')
                            # A timed-out PHP report can keep its session locked on the server.
                            # Use a fresh safe POST session for other reports, without retrying this query.
                            client = Client(root, refresh=True)
    except Exception as error:
        run['failures'].append({'stage': stage, 'reason': type(error).__name__})
        print('FAIL 인증 또는 연결 상태를 확인해야 합니다. 기존 검증 자료는 유지합니다.', flush=True)
    finally:
        run['finished_at'] = datetime.now(KST).isoformat()
        path = root / 'collection-status.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(run, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)
    return run


def main():
    parser = argparse.ArgumentParser(description='aTPOS 보고서 자동 수집')
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--from-month')
    parser.add_argument('--through-month')
    parser.add_argument('--stores', nargs='+', choices=STORE_CODES, default=list(STORE_CODES))
    parser.add_argument('--kinds', nargs='+', choices=PATHS, default=list(PATHS))
    parser.add_argument('--reuse-session', action='store_true', help='검증된 현재 세션을 재사용')
    parser.add_argument('--skip-existing', action='store_true', help='이미 검증된 과거 월 자료 재사용')
    args = parser.parse_args()
    today = datetime.now(KST).date()
    current = today.strftime('%Y-%m')
    if bool(args.from_month) != bool(args.through_month):
        parser.error('--from-month와 --through-month를 함께 지정하세요.')
    if args.from_month:
        if args.through_month > current:
            parser.error('미래 월은 수집하지 않습니다.')
        months = list(periods(args.from_month, args.through_month))
    else:
        previous = (today.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
        months = [previous, current]
    run = collect_job(args.data_dir, months, args.stores, args.kinds, not args.reuse_session, args.skip_existing)
    sys.exit(1 if run['failures'] else 0)


if __name__ == '__main__':
    main()
