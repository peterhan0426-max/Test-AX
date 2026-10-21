"""Consistent local backups. Off-site storage is configured by the hosting owner."""
import argparse
import hashlib
import io
import json
import os
import re
import sqlite3
import tarfile
import tempfile
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .data import KST
from .publication import FILES, published_root
from collector.schedule import due_in

PUBLISHED_METADATA = (*FILES, 'publication.json')

METADATA = ('categories.json', 'product-categories.json', 'category-sources.json',
            'collection-status.json', 'scheduled-last-attempt.json', 'closed-no-data.json', 'closed-review.json')


def create_backup(root, destination, keep=7):
    root, destination = Path(root), Path(destination)
    if destination.resolve().is_relative_to(root.resolve()) or keep < 1:
        raise ValueError('백업은 자료 폴더 밖에 보관하고 최소 한 개 이상 유지해야 합니다.')
    destination.mkdir(parents=True, exist_ok=True)
    destination.chmod(0o700)
    now = datetime.now(KST)
    name = 'saenong-' + now.strftime('%Y%m%dT%H%M%S%f') + '.tar.gz'
    target = destination / name
    temporary = target.with_suffix('.tmp')
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        with tempfile.TemporaryDirectory(dir=destination) as folder:
            copy = Path(folder) / 'sales.sqlite3'
            with closing(sqlite3.connect((root / 'sales.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)) as source:
                with closing(sqlite3.connect(copy)) as db:
                    source.backup(db)
                    expected = {row[0] for row in db.execute('SELECT DISTINCT sha256 FROM snapshots')}
            with tarfile.open(temporary, 'w:gz') as archive:
                archive.add(copy, arcname='sales.sqlite3')
                view = published_root(root)
                if view != root:
                    frozen = Path(folder) / 'published.sqlite3'
                    with closing(sqlite3.connect((view / 'sales.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)) as source:
                        with closing(sqlite3.connect(frozen)) as db:
                            source.backup(db)
                            expected.update(row[0] for row in db.execute('SELECT DISTINCT sha256 FROM snapshots'))
                    archive.add(frozen, arcname='published-view/sales.sqlite3')
                    for key in PUBLISHED_METADATA:
                        path = view / key
                        if path.is_file() and not path.is_symlink():
                            content = path.read_bytes()
                            json.loads(content)
                            member = tarfile.TarInfo('published-view/' + key)
                            member.size, member.mode = len(content), 0o600
                            archive.addfile(member, io.BytesIO(content))
                for key in METADATA:
                    path = root / key
                    if path.is_file() and not path.is_symlink():
                        content = path.read_bytes()
                        json.loads(content)
                        member = tarfile.TarInfo(key)
                        member.size, member.mode = len(content), 0o600
                        archive.addfile(member, io.BytesIO(content))
                included = set()
                for path in sorted((root / 'private-originals').glob('*.xls')):
                    if path.is_symlink() or not re.fullmatch(r'[0-9a-f]{64}\.xls', path.name):
                        continue
                    with path.open('rb') as stream:
                        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                    if digest != path.stem:
                        if path.stem in expected:
                            raise ValueError('참조 원본의 해시가 달라 백업을 중단했습니다.')
                        continue  # A source file may still be downloading.
                    archive.add(path, arcname='private-originals/' + path.name)
                    included.add(path.stem)
                if expected - included:
                    raise ValueError('참조된 원본이 누락되어 백업을 중단했습니다.')
            temporary.chmod(0o600)
            temporary.replace(target)
        for old in sorted(destination.glob('saenong-*.tar.gz'), reverse=True)[keep:]:
            old.unlink()
        status = {'date': now.date().isoformat(), 'finished_at': datetime.now(KST).isoformat(), 'file': name}
        marker = destination / 'backup-status.tmp'
        marker.write_text(json.dumps(status), encoding='utf-8')
        marker.replace(destination / 'backup-status.json')
        return target
    finally:
        temporary.unlink(missing_ok=True)


def restore_backup(archive_path, root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError('복구 대상 폴더는 비어 있어야 합니다.')
    with tarfile.open(archive_path, 'r:gz') as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)) or 'sales.sqlite3' not in names:
            raise ValueError('유효한 백업 구성이 아닙니다.')
        for member in members:
            allowed = member.name in ('sales.sqlite3', *METADATA, *('published-view/' + name for name in ('sales.sqlite3', *PUBLISHED_METADATA))) or re.fullmatch(r'private-originals/[0-9a-f]{64}\.xls', member.name)
            if not member.isfile() or not allowed:
                raise ValueError('백업에 허용되지 않은 경로가 있습니다.')
        # Validate in staging before making any restored file visible.
        with tempfile.TemporaryDirectory(dir=root.parent) as folder:
            stage = Path(folder)
            for member in members:
                path = stage / member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, path.open('wb') as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                path.chmod(0o600)
            hashes = set()
            for database in (stage / 'sales.sqlite3', stage / 'published-view' / 'sales.sqlite3'):
                if not database.exists():
                    continue
                with closing(sqlite3.connect(database)) as db:
                    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('복구 데이터베이스 검증에 실패했습니다.')
                    hashes.update(row[0] for row in db.execute('SELECT DISTINCT sha256 FROM snapshots'))
            for digest in hashes:
                if not re.fullmatch('[0-9a-f]{64}', digest):
                    raise ValueError('복구 원본 해시가 유효하지 않습니다.')
                if not (stage / 'private-originals' / (digest + '.xls')).is_file():
                    raise ValueError('복구할 참조 원본이 누락됐습니다.')
            for path in (stage / 'private-originals').glob('*.xls'):
                with path.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != path.stem:
                        raise ValueError('복구 원본 해시 검증에 실패했습니다.')
            for key in METADATA:
                if (stage / key).exists():
                    json.loads((stage / key).read_text('utf-8'))
            for key in PUBLISHED_METADATA:
                path = stage / 'published-view' / key
                if path.exists():
                    json.loads(path.read_text('utf-8'))
            for path in stage.iterdir():
                path.replace(root / path.name)


def main():
    parser = argparse.ArgumentParser(description='새농 자료 백업 및 복구')
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--backup-dir', type=Path)
    parser.add_argument('--restore', type=Path)
    parser.add_argument('--daily', action='store_true')
    args = parser.parse_args()
    if args.restore:
        restore_backup(args.restore, args.data_dir)
        print('복구 및 원본 해시 검증 완료.')
        return
    if not args.backup_dir:
        parser.error('--backup-dir이 필요합니다.')
    marker = args.backup_dir / 'backup-status.json'
    last = json.loads(marker.read_text('utf-8'))['date'] if marker.exists() else None
    while True:
        delay = due_in(datetime.now(KST), last, 9, 0) if args.daily else 0
        if delay:
            time.sleep(min(delay, 30))
            continue
        try:
            create_backup(args.data_dir, args.backup_dir)
            print('백업 완료: 복구 가능한 원본·분석 자료를 보관했습니다.', flush=True)
        except Exception as error:
            print('백업 실패:', type(error).__name__, flush=True)
            if not args.daily:
                raise SystemExit(1)
        last = datetime.now(KST).date().isoformat()
        if not args.daily:
            return


if __name__ == '__main__':
    main()
