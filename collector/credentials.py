"""Private, explicitly connected source account; excluded from sales backups."""
import json
import os
from pathlib import Path
import tempfile


def credential_path(root):
    return Path(root).parent / 'credentials' / 'atpos.json'


def load(root):
    path = credential_path(root)
    if path.is_file() and not path.is_symlink():
        value = json.loads(path.read_text('utf-8'))
        if not isinstance(value, dict):
            raise ValueError('저장된 연결 정보를 확인해야 합니다.')
        if isinstance(value.get('account'), str) and isinstance(value.get('password'), str) and value['account'] and value['password']:
            return value['account'], value['password']
        raise ValueError('저장된 연결 정보를 확인해야 합니다.')
    return os.environ.get('ATPOS_ID'), os.environ.get('ATPOS_PASSWORD')


def version(root):
    try:
        account, password = load(root)
        if not account or not password:
            return None
        path = credential_path(root)
        return str(path.stat().st_mtime_ns) if path.is_file() else 'environment'
    except (OSError, ValueError, TypeError, KeyError):
        return None


def connect(root, account, password):
    if not isinstance(account, str) or not isinstance(password, str) or not account.strip() or not password or len(account) > 256 or len(password) > 2048:
        raise ValueError('계정 정보를 확인해야 합니다.')
    from .authenticate import establish_session
    path = credential_path(root)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    # Validate first; a failed replacement preserves the previous working connection.
    with tempfile.TemporaryDirectory(dir=path.parent) as folder:
        establish_session(Path(folder), account=account.strip(), password=password)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump({'account': account.strip(), 'password': password}, stream)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
