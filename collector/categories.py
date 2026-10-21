"""Derive the three named groups and their complement from actual source filters."""
import argparse
import json
from datetime import datetime
from pathlib import Path

from .native import Client, STORE_CODES
from saenong.data import KST, month_end

# Authenticated LCLASS_CD options; everything outside these three is 기타.
GROUPS = {'T019': '정육/냉장', 'T018': '과일/채소', 'T020': '상온/냉동'}


def sync_categories(root, period, stores):
    root = Path(root)
    client = Client(root)
    observed, labels, sources = {}, {}, []
    cutoff = min(month_end(period), datetime.now(KST).date()).isoformat()
    for store in stores:
        _, baseline = client.report('monthly-profit', store, period, cutoff)
        expected = {row['product'] for row in baseline['rows']}
        local = {}
        for code, group in GROUPS.items():
            _, report = client.report('monthly-profit', store, period, cutoff, code)
            for row in report['rows']:
                key = row['product']
                if key in local and local[key] != group:
                    raise ValueError('상품이 여러 대분류에 걸쳐 있습니다. 연결을 저장하지 않습니다.')
                local[key] = group
            sources.append({'store': store, 'period': period, 'large_category_code': code,
                            'group': group, 'rows': len(report['rows'])})
            print(f'CATEGORY {store} {code}: {len(report["rows"])}행', flush=True)
        for row in baseline['rows']:
            key = row['product']
            group = local.get(key, '기타')
            if key in observed and observed[key] != group:
                raise ValueError('동일 상품의 매장별 분류가 다릅니다. 연결을 저장하지 않습니다.')
            observed[key] = group
            labels.setdefault(row['subcategory'], set()).add(group)
        sources.append({'store': store, 'period': period, 'group': '기타',
                        'basis': 'comprehensive minus three named source groups',
                        'rows': len(expected - set(local))})
    if not observed:
        raise ValueError('카테고리 근거 자료가 없습니다.')
    target = root / 'product-categories.json'
    existing = json.loads(target.read_text('utf-8')) if target.exists() else {}
    existing.update(observed)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(existing, ensure_ascii=False), encoding='utf-8')
    temporary.replace(target)
    subcategory_file = root / 'categories.json'
    subcategories = json.loads(subcategory_file.read_text('utf-8')) if subcategory_file.exists() else {}
    # Ambiguous labels must never assign old products to a guessed category.
    for name, groups in labels.items():
        if len(groups) == 1:
            subcategories[name] = next(iter(groups))
        else:
            subcategories.pop(name, None)
    temporary = subcategory_file.with_suffix('.tmp')
    temporary.write_text(json.dumps(subcategories, ensure_ascii=False), encoding='utf-8')
    temporary.replace(subcategory_file)
    evidence = root / 'category-sources.json'
    history = json.loads(evidence.read_text('utf-8')) if evidence.exists() else {}
    previous = history.get('history', [])
    if history.get('sources'):
        previous.append({key: value for key, value in history.items() if key != 'history'})
    evidence.write_text(json.dumps({'collected_at': datetime.now(KST).isoformat(),
                                    'sources': sources, 'history': previous}, ensure_ascii=False), encoding='utf-8')
    return len(observed)


def main():
    parser = argparse.ArgumentParser(description='aTPOS 대분류 기준 상품 연결')
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--period', default=datetime.now(KST).strftime('%Y-%m'))
    parser.add_argument('--stores', nargs='+', choices=STORE_CODES, default=list(STORE_CODES))
    args = parser.parse_args()
    total = sync_categories(args.data_dir, args.period, args.stores)
    print(f'상품 {total}개 분류 연결 완료')


if __name__ == '__main__':
    main()
