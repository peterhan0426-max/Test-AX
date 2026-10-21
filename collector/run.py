"""Repeatable collection jobs. A scheduler invokes --once; history is explicit."""
import argparse
import hashlib
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


def closed_snapshot(row, today):
    end = month_end(row['period'])
    if end >= today or row['as_of'] != end.isoformat():
        return False
    parsed = json.loads(row['data'])
    observed = datetime.fromisoformat(parsed.get('collected_at', row['imported_at']))
    return (observed.tzinfo is not None and observed.astimezone(KST).date() > end and
            (row['kind'] != 'monthly-profit' or parsed.get('source_query', {}).get('check_yn') == '5'))


def collect_job(root, months, stores, kinds, refresh=True, skip_existing=False, through_day=None, publish=False):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    run = {'started_at': datetime.now(KST).isoformat(), 'state': 'running',
           'results': [], 'failures': [], 'no_data': [], 'cached': []}
    limit = date.fromisoformat(through_day) if through_day else datetime.now(KST).date()
    run['through_day'] = limit.isoformat()
    kinds = sorted(kinds, key=lambda kind: kind != 'daily-sales')
    def save_status():
        path = root / 'collection-status.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(run, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)

    cache_file = root / 'closed-no-data.json'
    try:
        no_data_cache = json.loads(cache_file.read_text('utf-8'))
        if not isinstance(no_data_cache, dict):
            no_data_cache = {}
    except (OSError, ValueError):
        no_data_cache = {}
    review_file = root / 'closed-review.json'
    try:
        review = json.loads(review_file.read_text('utf-8'))
        if not isinstance(review, dict):
            review = {}
    except (OSError, ValueError):
        review = {}
    # Retain failed closed-period attempts from the last job without repeating them daily.
    try:
        prior = json.loads((root / 'collection-status.json').read_text('utf-8'))
        observed = datetime.fromisoformat(prior['finished_at']).astimezone(KST).date()
        for failure in prior.get('failures', []):
            if all(failure.get(key) for key in ('store', 'period', 'kind')) and month_end(failure['period']) < observed:
                key = '|'.join((failure['kind'], failure['store'], failure['period']))
                review.setdefault(key, {**failure, 'observed_at': prior['finished_at']})
    except (OSError, ValueError, TypeError, KeyError):
        pass
    client = None
    stage = 'authentication'
    network_failures = 0
    try:
        save_status()
        today = datetime.now(KST).date()
        existing = {(row['kind'], row['store'], row['period']) for row in active_reports(root)
                    if row['period'] in months and row['store'] in stores and row['kind'] in kinds
                    and closed_snapshot(row, today)} if skip_existing else set()
        for month in months:
            end = min(month_end(month), today, limit).isoformat()
            if end[:7] != month:
                continue
            for store in stores:
                for kind in kinds:
                    if (kind, store, month) in existing:
                        review.pop('|'.join((kind, store, month)), None)
                        run['cached'].append({'kind': kind, 'store': store, 'period': month})
                        print(f'CACHED {store} {month} {kind}', flush=True)
                        continue
                    cache_key = '|'.join((kind, store, month))
                    if skip_existing and month_end(month) < today and cache_key in review:
                        print(f'REVIEW {store} {month} {kind}: stored; not queried automatically', flush=True)
                        continue
                    negative = no_data_cache.get(cache_key, {})
                    digest = negative.get('sha256', '') if isinstance(negative, dict) else ''
                    if skip_existing and month_end(month) < today and isinstance(digest, str) and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest):
                        original = root / 'private-originals' / (digest + '.xls')
                        if original.is_file() and not original.is_symlink():
                            with original.open('rb') as stream:
                                verified = hashlib.file_digest(stream, 'sha256').hexdigest() == digest
                            if verified:
                                run['no_data'].append({'store': store, 'period': month, 'kind': kind, 'cached': True})
                                print(f'CACHED NO DATA {store} {month} {kind}', flush=True)
                                continue
                    if client is None:
                        client = Client(root, refresh=refresh)
                        stage = 'collection'
                    try:
                        result = client.collect(kind, store, month, end)
                        network_failures = 0
                        run['results'].append({key: result[key] for key in ('kind', 'store', 'period', 'as_of', 'rows', 'duplicate')})
                        no_data_cache.pop(cache_key, None)
                        review.pop(cache_key, None)
                        save_status()
                        print(f'OK {store} {month} {kind} {result["rows"]}행', flush=True)
                    except Exception as error:
                        # Never print HTTP errors that may contain URLs or session details.
                        reason = str(error) if isinstance(error, ValueError) else type(error).__name__
                        if kind in ('promotion-summary', 'promotion-items') and isinstance(error, ValueError) and '원본이 매출내역 없음' in reason:
                            run['no_data'].append({'store': store, 'period': month, 'kind': kind})
                            digest = getattr(client, 'last_original_sha256', None)
                            if month_end(month) < today and digest:
                                no_data_cache[cache_key] = {'sha256': digest, 'observed_at': datetime.now(KST).isoformat()}
                            review.pop(cache_key, None)
                            save_status()
                            print(f'NO DATA {store} {month} {kind}', flush=True)
                            continue
                        run['failures'].append({'store': store, 'period': month, 'kind': kind, 'reason': reason})
                        if month_end(month) < today:
                            review[cache_key] = {**run['failures'][-1], 'observed_at': datetime.now(KST).isoformat()}
                        save_status()
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
        run['state'] = 'failed' if run['failures'] else 'complete'
        run['review_required'] = list(review.values())
        temporary = cache_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(no_data_cache, ensure_ascii=False), encoding='utf-8')
        temporary.replace(cache_file)
        temporary = review_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(review, ensure_ascii=False), encoding='utf-8')
        temporary.replace(review_file)
        save_status()
        if publish:
            try:
                from saenong.publication import publish as publish_view
                run['publication'] = publish_view(root, limit.isoformat(), required_stores=stores)
            except Exception as error:
                run['state'] = 'failed'
                run['failures'].append({'stage': 'publication', 'reason': type(error).__name__})
                save_status()
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
    cutoff = today - timedelta(days=1)
    current = cutoff.strftime('%Y-%m')
    if bool(args.from_month) != bool(args.through_month):
        parser.error('--from-month와 --through-month를 함께 지정하세요.')
    if args.from_month:
        if args.through_month > current:
            parser.error('미래 월은 수집하지 않습니다.')
        months = list(periods(args.from_month, args.through_month))
    else:
        previous = (cutoff.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
        months = [previous, current]
    run = collect_job(args.data_dir, months, args.stores, args.kinds, not args.reuse_session,
                      args.skip_existing, through_day=cutoff.isoformat(), publish=True)
    sys.exit(1 if run['failures'] else 0)


if __name__ == '__main__':
    main()
