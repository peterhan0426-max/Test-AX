import calendar
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from .reports import read_report

STORES = {'가락': (Decimal('0.05'), 18.5), '야탑': (Decimal('0.05'), 18.5),
          '동천': (Decimal('0.10'), 16.5)}
CATEGORIES = ('정육/냉장', '과일/채소', '상온/냉동', '기타')
KST = timezone(timedelta(hours=9), 'KST')


def month_end(period):
    year, month = map(int, period.split('-'))
    return date(year, month, calendar.monthrange(year, month)[1])


def store_name(value):
    if value in STORES:
        return value
    for name in STORES:
        if value == f'새농푸드마켓({name})':
            return name
    raise ValueError('매장은 가락, 야탑, 동천 중 하나여야 합니다.')


@contextmanager
def connect(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'sales.sqlite3', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS snapshots (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, store TEXT NOT NULL,
        period TEXT NOT NULL, as_of TEXT NOT NULL, sha256 TEXT NOT NULL,
        imported_at TEXT NOT NULL, data TEXT NOT NULL,
        UNIQUE(kind, store, period, as_of, sha256))''')
    db.execute('''CREATE TABLE IF NOT EXISTS active (
        kind TEXT, store TEXT, period TEXT, snapshot_id TEXT NOT NULL,
        PRIMARY KEY(kind, store, period))''')
    try:
        with db:
            yield db
    finally:
        db.close()


def import_report(root, file, store, period, as_of, source_query=None):
    root, file = Path(root), Path(file)
    store = store_name(store)
    last = month_end(period)
    if period != f'{last.year:04}-{last.month:02}':
        raise ValueError('월 형식은 YYYY-MM입니다.')
    cutoff = date.fromisoformat(as_of)
    if cutoff.strftime('%Y-%m') != period:
        raise ValueError('수집 기준일은 조회 월 안에 있어야 합니다.')
    content = file.read_bytes()
    report = read_report(content)
    if source_query is not None:
        report['source_query'] = source_query
    if report['kind'] == 'daily-sales':
        dates = [date.fromisoformat(row['date']) for row in report['rows']]
        if any(day.strftime('%Y-%m') != period or day > cutoff for day in dates):
            raise ValueError('일별 자료의 실제 매출일자가 지정 기간 밖에 있습니다.')
    if report['kind'] == 'promotion-items':
        if any(store_name(row['store']) != store for row in report['rows']):
            raise ValueError('상세 보고서에 지정 매장과 다른 매장 자료가 있습니다.')
        for row in report['rows']:
            date.fromisoformat(row['date'])
        # Selection may span the whole promotion. Keep parsed rows private; API filters actual dates.
    digest = hashlib.sha256(content).hexdigest()
    identifier = uuid.uuid4().hex
    raw = root / 'private-originals' / (digest + '.xls')
    with connect(root) as db:
        old = db.execute('''SELECT s.as_of FROM active a JOIN snapshots s ON s.id=a.snapshot_id
            WHERE a.kind=? AND a.store=? AND a.period=?''',
                         (report['kind'], store, period)).fetchone()
        if old and old['as_of'] > as_of:
            raise ValueError('더 최신 기준일 자료가 있습니다. 과거 자료로 덮어쓰지 않습니다.')
        existing = db.execute('''SELECT id FROM snapshots
            WHERE kind=? AND store=? AND period=? AND as_of=? AND sha256=?''',
                              (report['kind'], store, period, as_of, digest)).fetchone()
        if existing:
            identifier = existing['id']
            if source_query is not None:
                db.execute('UPDATE snapshots SET data=? WHERE id=?',
                           (json.dumps(report, ensure_ascii=False), identifier))
        else:
            raw.parent.mkdir(parents=True, exist_ok=True)
            if not raw.exists():
                with raw.open('xb') as stream:
                    stream.write(content)
            db.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?)',
                       (identifier, report['kind'], store, period, as_of, digest,
                        datetime.now(KST).isoformat(),
                        json.dumps(report, ensure_ascii=False)))
        db.execute('INSERT OR REPLACE INTO active VALUES (?,?,?,?)',
                   (report['kind'], store, period, identifier))
    return {'kind': report['kind'], 'rows': len(report['rows']),
            'totals': report['totals'], 'duplicate': bool(existing)}


def active_reports(root):
    with connect(root) as db:
        return [dict(row) for row in db.execute('''SELECT s.* FROM active a
            JOIN snapshots s ON s.id=a.snapshot_id ORDER BY s.period,s.store,s.kind''')]


def financials(values):
    result = {key: values[key] for key in ('net', 'discount', 'purchases', 'opening', 'closing')}
    result['gross'] = result['net'] + result['discount']
    result['cogs'] = result['opening'] + result['purchases'] - result['closing']
    result['profit'] = result['gross'] - result['cogs']
    result['margin'] = result['profit'] / result['gross'] * 100 if result['gross'] else None
    return result


def sales_target(store, period, lookup):
    year, month = map(int, period.split('-'))
    months = (1, 2) if month in (1, 2) else (9, 10) if month in (9, 10) else (month,)
    base = []
    for value in months:
        previous = f'{year - 1:04}-{value:02}'
        item = lookup.get((store, previous))
        if not item or date.fromisoformat(item['as_of']) < month_end(previous):
            return None
        base.append(Decimal(item['gross']))
    return int((sum(base) / len(base) * (1 + STORES[store][0])).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def dashboard(root, period=None):
    reports = active_reports(root)
    available = sorted({row['period'] for row in reports}, reverse=True)
    selected = period or (available[0] if available else datetime.now(KST).strftime('%Y-%m'))
    month_end(selected)
    mappings_file = Path(root) / 'categories.json'
    mappings = json.loads(mappings_file.read_text('utf-8')) if mappings_file.exists() else {}
    product_file = Path(root) / 'product-categories.json'
    products = json.loads(product_file.read_text('utf-8')) if product_file.exists() else {}
    if any(value not in CATEGORIES for value in [*mappings.values(), *products.values()]):
        raise ValueError('카테고리 연결 설정에 허용되지 않은 분류가 있습니다.')
    monthly, daily_totals, snapshots, category_history, blocked = {}, {}, [], {}, {}
    for source in reports:
        source['parsed'] = json.loads(source.pop('data'))
        if source['kind'] == 'monthly-profit':
            monthly[(source['store'], source['period'])] = {
                **financials(source['parsed']['totals']), 'as_of': source['as_of']}
            mode = source['parsed'].get('source_query', {}).get('check_yn')
            if mode is not None and mode != '5':
                blocked[(source['store'], source['period'])] = '전체 상품 범위의 손익 자료를 확인하지 못했습니다.'
        elif source['kind'] == 'daily-sales':
            daily_totals[(source['store'], source['period'])] = {
                **source['parsed']['totals'], 'as_of': source['as_of']}
        snapshots.append(source)
    for key, value in monthly.items():
        other = daily_totals.get(key)
        if other and other['as_of'] == value['as_of'] and value['as_of'] < datetime.now(KST).date().isoformat() and other['gross'] != value['gross']:
            blocked[key] = '일별·월별 총매출이 달라 이익·마진을 표시하지 않습니다.'
    revenue = {**monthly, **daily_totals}
    trend_values = dict(revenue)
    for key, value in monthly.items():
        if key not in blocked:
            trend_values[key] = value
    for source in snapshots:
        if source['kind'] != 'monthly-profit' or (source['store'], source['period']) in blocked:
            continue
        for row in source['parsed']['rows']:
            category = products.get(row['product'], mappings.get(row['subcategory'], '미분류'))
            values = category_history.setdefault(category, {}).setdefault(source['period'], {})
            values[source['store']] = values.get(source['store'], 0) + row['net'] + row['discount']
    stores, categories, promotions, details, days, warnings = [], {}, [], {}, {}, []
    for store, (_, margin_goal) in STORES.items():
        key = store, selected
        item = monthly.get(key) if key not in blocked else None
        revenue_only = item is None and key in revenue
        if revenue_only:
            item = {**revenue[key], **{field: None for field in ('profit', 'margin', 'cogs', 'purchases', 'opening', 'closing')}}
            warnings.append(f'{store}: ' + blocked.get(key, '매입·재고 자료가 없어 이익·마진을 계산하지 않았습니다.'))
        if item:
            target = sales_target(store, selected, revenue)
            stores.append({'store': store, **item, 'sales_target': target,
                           'revenue_only': revenue_only,
                           'margin_target': margin_goal,
                           'achievement': item['gross'] / target * 100 if target else None,
                           'complete': date.fromisoformat(item['as_of']) == month_end(selected)})
        else:
            stores.append({'store': store, 'missing': True, 'margin_target': margin_goal})
    for source in snapshots:
        if source['period'] != selected:
            continue
        store, parsed = source['store'], source['parsed']
        for key, value in parsed.get('source_differences', {}).items():
            label = {'opening': '기초재고', 'closing': '기말재고'}.get(key, key)
            warnings.append(f'{store}: 원본 {label} 전체 합계와 상품별 표시금액 합계가 {value:+,}원 다릅니다. 전체 지표는 원본 전체 합계를 사용합니다.')
        if source['kind'] == 'monthly-profit':
            if (store, selected) in blocked:
                continue
            for row in parsed['rows']:
                category = products.get(row['product'], mappings.get(row['subcategory'], '미분류'))
                key = store, category
                current = categories.setdefault(key, {key: 0 for key in ('net', 'discount', 'purchases', 'opening', 'closing')})
                for field in current:
                    current[field] += row[field]
        elif source['kind'] == 'promotion-summary':
            grouped = {}
            for row in parsed['rows']:
                grouped[row['type']] = grouped.get(row['type'], 0) + row['discount']
            promotions.append({'store': store, 'as_of': source['as_of'], 'groups': grouped,
                               'difference': parsed['totals']['difference']})
            if any(row['difference'] or row['discount'] != row['item_discount'] for row in parsed['rows']):
                warnings.append(f'{store}: 프로모션 고객별·품목별 할인액이 일치하지 않습니다.')
        elif source['kind'] == 'promotion-items':
            count = sum(1 for row in parsed['rows'] if row['date'][:7] == selected and row['date'] <= source['as_of'])
            total = sum(row['discount'] for row in parsed['rows'] if row['date'][:7] == selected and row['date'] <= source['as_of'])
            details[store] = {'rows': count, 'discount': total, 'as_of': source['as_of']}
        elif source['kind'] == 'daily-sales':
            for row in parsed['rows']:
                if row['date'] >= datetime.now(KST).date().isoformat():
                    continue
                days.setdefault(row['date'], {})[store] = row['gross']
            counterpart = monthly.get((store, selected))
            if counterpart and source['as_of'] == counterpart['as_of'] and source['as_of'] < datetime.now(KST).date().isoformat():
                if any(parsed['totals'][key] != counterpart[key] for key in ('gross', 'net', 'discount')):
                    warnings.append(f'{store}: 같은 기준일의 일별·월별 합계가 다릅니다.')
    if any(category == '미분류' for _, category in categories):
        warnings.append('파일에 대분류가 없어 카테고리 연결 전 항목은 미분류로 표시합니다.')
    if any(not item.get('missing') and item['sales_target'] is None for item in stores):
        warnings.append('전년 기준 월 자료가 부족해 일부 매출 목표를 계산하지 않았습니다.')
    daily_dates = []
    if days:
        current = date.fromisoformat(selected + '-01')
        end = date.fromisoformat(max(days))
        while current <= end:
            daily_dates.append(current.isoformat())
            current += timedelta(days=1)
    trend_months = []
    if trend_values:
        current = min(month for _, month in trend_values)
        end = max(month for _, month in trend_values)
        while current <= end:
            trend_months.append(current)
            day = month_end(current) + timedelta(days=1)
            current = day.strftime('%Y-%m')
    status_file = Path(root) / 'collection-status.json'
    collection = json.loads(status_file.read_text('utf-8')) if status_file.exists() else None
    if collection and collection.get('failures'):
        warnings.append(f'최근 일괄 수집 기록에 {len(collection["failures"])}개 실패 항목이 있습니다. 이후 개별 반영 여부는 자료별 기준일을 따릅니다.')
    return {'period': selected, 'periods': available, 'stores': stores, 'collection': collection,
            'categories': [{'store': store, 'category': category, **financials(values)}
                           for (store, category), values in sorted(categories.items())],
            'promotions': promotions, 'promotion_details': details,
            'daily': [{'date': day, 'stores': days.get(day, {})} for day in daily_dates],
            'trend': [{'period': month, 'stores': {store: value['gross'] for (store, p), value in trend_values.items() if p == month}}
                      for month in trend_months],
            'partial_months': {month: {store: date.fromisoformat(value['as_of']) < month_end(month)
                                      for (store, p), value in trend_values.items() if p == month} for month in trend_months},
            'category_trend': {category: [{'period': month, 'stores': history.get(month, {})} for month in trend_months]
                               for category, history in category_history.items()},
            'sources': [{'kind': row['kind'], 'store': row['store'], 'period': row['period'],
                         'as_of': row['as_of'], 'imported_at': row['imported_at']} for row in snapshots if row['period'] == selected],
            'warnings': warnings}
