"""Preserve a Python check's exit status and publish its log in the job summary."""
import os
import subprocess
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        raise SystemExit('A Python check is required.')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    environment = os.environ.copy()
    environment['PYTHONIOENCODING'] = 'utf-8'
    result = subprocess.run([sys.executable, *sys.argv[1:]], env=environment,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            encoding='utf-8', errors='replace')
    print(result.stdout, end='')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with Path(summary).open('a', encoding='utf-8') as stream:
            stream.write('\n### ' + ' '.join(sys.argv[1:]) + '\n\n')
            stream.write('Exit status: ' + str(result.returncode) + '\n\n````text\n')
            stream.write(result.stdout[-60000:] + '\n````\n')
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
