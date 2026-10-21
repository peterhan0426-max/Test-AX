import argparse
import json
from pathlib import Path

from .data import import_report


def main():
    parser = argparse.ArgumentParser(description='aTPOS 원본 검증 및 가져오기')
    parser.add_argument('file', type=Path)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--store', required=True, choices=('가락', '야탑', '동천'))
    parser.add_argument('--period', required=True, help='YYYY-MM')
    parser.add_argument('--as-of', required=True, help='실제 조회 기준일 YYYY-MM-DD')
    args = parser.parse_args()
    try:
        result = import_report(args.data_dir, args.file, args.store, args.period, args.as_of)
    except ValueError as error:
        parser.exit(1, f'반영하지 않았습니다: {error}\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
