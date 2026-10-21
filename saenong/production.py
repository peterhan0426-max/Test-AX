"""Run web, collector and backup together; the host restarts this process on failure."""
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def main():
    password = os.environ.get('SAENONG_VIEWER_PASSWORD', '')
    collect = os.environ.get('SAENONG_AUTOCOLLECT', '1') != '0'
    if len(password) < 20 or (collect and any(not os.environ.get(key) for key in ('ATPOS_ID', 'ATPOS_PASSWORD'))):
        raise SystemExit('운영용 화면 암호와 ATPOS 인증 설정을 확인해야 합니다.')
    base = Path(os.environ.get('SAENONG_ROOT', '/var/saenong'))
    if base.resolve() == Path(base.anchor) or base.resolve() == Path('/app'):
        raise SystemExit('전용 자료 폴더를 사용해야 합니다.')
    data, backups = base / 'data', base / 'backups'
    for directory in (data, backups):
        directory.mkdir(parents=True, exist_ok=True)
        if hasattr(os, 'geteuid') and os.geteuid() == 0:
            directory.chmod(0o700)
            for path in (directory, *directory.rglob('*')):
                if not path.is_symlink():
                    os.chown(path, 10001, 10001)
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        base.chmod(0o700)
        os.chown(base, 10001, 10001)
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    port = str(int(os.environ.get('PORT', '8000')))
    environment = os.environ.copy()
    environment['SAENONG_REQUIRE_AUTH'] = '1'
    stop = False

    def shutdown(*_):
        nonlocal stop
        stop = True

    for name in ('SIGTERM', 'SIGINT'):
        signal.signal(getattr(signal, name), shutdown)
    children = []
    try:
        web_environment = {key: value for key, value in environment.items() if key not in ('ATPOS_ID', 'ATPOS_PASSWORD')}
        web = subprocess.Popen([sys.executable, '-u', '-m', 'saenong.server', '--host', '0.0.0.0',
                                '--port', port, '--data-dir', str(data)], env=web_environment)
        children.append(web)
        for attempt in range(60):
            if stop:
                return
            if web.poll() is not None:
                raise RuntimeError('웹 프로세스가 시작되지 않았습니다.')
            try:
                with urllib.request.urlopen('http://127.0.0.1:' + port + '/health', timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.5)
        else:
            raise RuntimeError('웹 상태 확인에 실패했습니다.')
        worker_environment = {key: value for key, value in environment.items() if key != 'SAENONG_VIEWER_PASSWORD'}
        if collect:
            children.append(subprocess.Popen([sys.executable, '-u', '-m', 'collector.schedule',
                                              '--data-dir', str(data), '--hour', '8'], env=worker_environment))
        backup_environment = {key: value for key, value in worker_environment.items() if key not in ('ATPOS_ID', 'ATPOS_PASSWORD')}
        children.append(subprocess.Popen([sys.executable, '-u', '-m', 'saenong.backup', '--daily',
                                          '--data-dir', str(data), '--backup-dir', str(backups)], env=backup_environment))
        print('운영 프로세스 시작: 매일 08:00 수집, 09:00 백업 (한국 시간).', flush=True)
        while not stop:
            if any(child.poll() is not None for child in children):
                raise RuntimeError('하위 프로세스 종료: 호스팅에서 전체 서비스를 다시 시작해야 합니다.')
            time.sleep(1)
    except Exception as error:
        print('운영 중단:', type(error).__name__, flush=True)
        raise SystemExit(1)
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    main()
