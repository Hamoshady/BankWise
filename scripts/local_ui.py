"""Start/stop the local Streamlit workspace without changing the banking API."""
import argparse
import hashlib
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
NAME = 'bankwise-ui-local'
IMAGE = 'bankwise-ai:streamlit'


def inspect(template, target):
    result = subprocess.run(['docker', 'inspect', '--format', template, target], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['up', 'stop', 'status', 'logs'], nargs='?', default='up')
    args = parser.parse_args()
    if args.command != 'up':
        commands = {'stop': ['stop', NAME], 'logs': ['logs', '--tail', '60', NAME],
                    'status': ['ps', '-a', '--filter', 'name=^/' + NAME + '$']}
        return subprocess.call(['docker'] + commands[args.command])
    digest = hashlib.sha256()
    for file in [ROOT / 'Dockerfile.ui', ROOT / 'requirements-ui.txt', *sorted((ROOT / 'ui').glob('*'))]:
        if file.is_file():
            digest.update(file.name.encode())
            digest.update(file.read_bytes())
    fingerprint = digest.hexdigest()[:16]
    if inspect('{{index .Config.Labels "bankwise.ui.sources"}}', IMAGE) != fingerprint:
        # Reuse installed UI dependencies for source-only changes; avoid a base-tag
        # refresh downloading the entire Python/Arrow stack on metered connections.
        installed = subprocess.run(['docker', 'run', '--rm', '--entrypoint', 'cat', IMAGE,
                                    '/app/requirements-ui.txt'], capture_output=True, text=True)
        if installed.returncode == 0 and installed.stdout == (ROOT / 'requirements-ui.txt').read_text():
            subprocess.run(['docker', 'build', '-f', '-', '-t', IMAGE,
                            '--label', 'bankwise.ui.sources=' + fingerprint, '.'],
                           input='FROM ' + IMAGE + '\nCOPY ui/ /app/ui/\n', text=True, cwd=ROOT, check=True)
        else:
            subprocess.run(['docker', 'build', '-f', 'Dockerfile.ui', '-t', IMAGE,
                            '--label', 'bankwise.ui.sources=' + fingerprint, '.'], cwd=ROOT, check=True)
    current_image = inspect('{{.ID}}', IMAGE)
    if inspect('{{.Image}}', NAME) != current_image:
        if inspect('{{.Id}}', NAME):
            subprocess.run(['docker', 'rm', '-f', NAME], check=True)
        subprocess.run(['docker', 'run', '-d', '--name', NAME, '--network', 'host',
                        '--restart', 'unless-stopped', '--security-opt', 'no-new-privileges',
                        '--cap-drop', 'ALL', IMAGE], check=True)
    elif inspect('{{.State.Running}}', NAME) != 'true':
        subprocess.run(['docker', 'start', NAME], check=True)
    print('Streamlit: http://127.0.0.1:8501')
    print('Requires the banking API at http://127.0.0.1:8000; use scripts/local_api.py up if needed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
