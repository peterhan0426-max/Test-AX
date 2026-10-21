"""Publish one consistent database snapshot after the daily collection finishes."""
import json
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .data import KST

FILES = ('categories.json', 'product-categories.json', 'category-sources.json',
         'collection-status.json', 'closed-review.json', 'closed-no-data.json')


def published_root(root):
    root = Path(root)
    pointer = root / 'published' / 'current.json'
    if not pointer.exists():
        restored = root / 'published-view'
        if (restored / 'sales.sqlite3').is_file() and not restored.is_symlink():
            return restored
        return root  # Initial migration remains visible until the first scheduled publication.
    name = json.loads(pointer.read_text('utf-8')).get('directory', '')
    if not isinstance(name, str) or not re.fullmatch(r'snapshot-[0-9a-f]{32}', name):
        raise ValueError('Invalid publication reference')
    target = pointer.parent / name
    if target.is_symlink() or not (target / 'sales.sqlite3').is_file():
        raise ValueError('Published database unavailable')
    return target


def publication_status(root):
    target = published_root(root)
    metadata = target / 'publication.json'
    if target == Path(root) or not metadata.exists():
        return {'cutoff': None, 'published_at': None}
    value = json.loads(metadata.read_text('utf-8'))
    return {key: value.get(key) for key in ('cutoff', 'published_at')}


def publish(root, cutoff, required_stores=None):
    root = Path(root)
    base = root / 'published'
    base.mkdir(mode=0o700, exist_ok=True)
    identifier = uuid.uuid4().hex
    stage = base / ('staging-' + identifier)
    target = base / ('snapshot-' + identifier)
    stage.mkdir(mode=0o700)
    pointer = base / ('current-' + identifier + '.tmp')
    try:
        with closing(sqlite3.connect((root / 'sales.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(stage / 'sales.sqlite3')) as destination:
                source.backup(destination)
                if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Publication database is invalid')
                if destination.execute('SELECT 1 FROM active a JOIN snapshots s ON a.snapshot_id=s.id WHERE s.as_of>? LIMIT 1', (cutoff,)).fetchone():
                    raise ValueError('Source snapshot exceeds publication cutoff')
                for store in required_stores or []:
                    row = destination.execute('SELECT s.as_of FROM active a JOIN snapshots s ON a.snapshot_id=s.id WHERE a.kind=? AND a.store=? AND a.period=?',
                                              ('daily-sales', store, cutoff[:7])).fetchone()
                    if not row or row[0] != cutoff:
                        raise ValueError('Complete daily sales required before publication')
                # Stale optional reports stay archived, but are not mixed into today's view.
                destination.execute('DELETE FROM active WHERE period=? AND snapshot_id IN (SELECT id FROM snapshots WHERE as_of<>?)', (cutoff[:7], cutoff))
                destination.commit()
        (stage / 'sales.sqlite3').chmod(0o600)
        for name in FILES:
            source = root / name
            if source.is_file() and not source.is_symlink():
                value = source.read_bytes()
                json.loads(value)
                (stage / name).write_bytes(value)
                (stage / name).chmod(0o600)
        metadata = {'cutoff': cutoff, 'published_at': datetime.now(KST).isoformat()}
        (stage / 'publication.json').write_text(json.dumps(metadata), encoding='utf-8')
        stage.replace(target)
        pointer.write_text(json.dumps({'directory': target.name}), encoding='utf-8')
        pointer.chmod(0o600)
        os.replace(pointer, base / 'current.json')
        # Keep a previous complete view for requests already reading it.
        versions = sorted(base.glob('snapshot-*'), key=lambda item: item.stat().st_mtime, reverse=True)
        for old in versions[2:]:
            if old != target and old.is_dir() and not old.is_symlink():
                shutil.rmtree(old, ignore_errors=True)
        return metadata
    finally:
        pointer.unlink(missing_ok=True)
        if stage.exists():
            shutil.rmtree(stage)
