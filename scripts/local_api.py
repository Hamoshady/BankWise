"""Operate the local Docker API; setup is documented in docs/local_api.md.

`up` is the only command needed for everyday use: it starts PostgreSQL, rebuilds
the image only when the source changed, applies migrations, recreates the API
container only when its image or configuration changed, and waits for
`/health/ready`.
"""
import argparse
import hashlib
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = 'bankwise-api-local'
DATABASE = 'bankwise-postgres'
IMAGE = 'bankwise-ai:semantic'
BUILD_INPUTS = ('Dockerfile.semantic', 'requirements-semantic.txt', 'requirements.txt',
                'pyproject.toml', 'src', 'configs')
RUN_COMMAND = ['uvicorn', 'bankwise.api.main:create_app', '--factory', '--host', '127.0.0.1',
               '--port', '8000', '--workers', '1', '--no-proxy-headers', '--no-access-log']


def run(command, *, check=True, capture=False, quiet=False):
    if not quiet:
        print('$', ' '.join(str(part) for part in command), flush=True)
    return subprocess.run(command, check=check, cwd=ROOT, capture_output=capture,
                          text=capture)


def inspect(format, name=NAME):
    result = run(['docker', 'inspect', '--format', format, name],
                 check=False, capture=True, quiet=True)
    return result.stdout.strip() if result.returncode == 0 else None


def image_id():
    return inspect('{{.ID}}', IMAGE)


def source_fingerprint():
    """Hash of every file copied into the image; newer mtimes alone are not reliable."""
    digest = hashlib.sha256()
    for item in BUILD_INPUTS:
        path = ROOT / item
        if path.is_dir():
            for child in sorted(path.rglob('*')):
                if child.is_file() and '__pycache__' not in child.parts and child.suffix != '.pyc':
                    digest.update(str(child.relative_to(ROOT)).encode())
                    digest.update(child.read_bytes())
        elif path.is_file():
            digest.update(item.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def build_needed():
    return inspect('{{index .Config.Labels "bankwise.sources"}}', IMAGE) != source_fingerprint()


def build_image():
    run(['docker', 'build', '-f', 'Dockerfile.semantic', '-t', IMAGE,
         '--label', 'bankwise.sources=' + source_fingerprint(), '.'])


def setup_hash(env_file, common):
    """Container identity for label comparison: image, mounts, command and env contents."""
    digest = hashlib.sha256(env_file.read_bytes())
    for part in common + [IMAGE] + RUN_COMMAND:
        digest.update(b'\0' + str(part).encode())
    return digest.hexdigest()[:16]


def ensure_database(timeout=60):
    if inspect('{{.State.Running}}', DATABASE) is None:
        raise SystemExit(f"database container '{DATABASE}' is missing; see docs/local_api.md")
    if inspect('{{.State.Running}}', DATABASE) != 'true':
        run(['docker', 'start', DATABASE])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if inspect('{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}', DATABASE) in {'healthy', 'none'}:
            return
        time.sleep(2)
    raise SystemExit(f"database container '{DATABASE}' did not become healthy")


def wait_ready(timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3) as response:
                if response.status == 200:
                    print('API ready: http://127.0.0.1:8000/docs', flush=True)
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    return False


def start_api(args, common):
    ensure_database()
    if any(line.strip().startswith('BANKWISE_LLAMA_URL=') and line.partition('=')[2].strip() for line in args.env_file.read_text().splitlines()):
        run(['python3', str(ROOT / 'scripts/local_llm.py'), 'up'])
    if args.build or build_needed():
        build_image()
    current_image = image_id()
    desired_hash = setup_hash(args.env_file, common)
    container_image = inspect('{{.Image}}')
    container_hash = inspect('{{index .Config.Labels "bankwise.setup"}}')
    running = inspect('{{.State.Running}}') == 'true'
    if (container_image != current_image or container_hash != desired_hash or args.migrate):
        if container_image is not None:
            run(['docker', 'rm', '-f', NAME])
        run(['docker', 'run', '--rm'] + common +
            [IMAGE, 'python', '-m', 'alembic', 'upgrade', 'head'])
        run(['docker', 'run', '-d', '--name', NAME, '--restart', 'unless-stopped',
             '--label', 'bankwise.setup=' + desired_hash] + common + [IMAGE] + RUN_COMMAND)
    elif not running:
        run(['docker', 'start', NAME])
    else:
        print('API container is already up to date.', flush=True)
    if not wait_ready(args.timeout):
        print('\nAPI did not become ready. Recent logs:', flush=True)
        subprocess.call(['docker', 'logs', '--tail', '40', NAME])
        print('\nIf the schema is stale, run: python3 scripts/local_api.py migrate', flush=True)
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['up', 'start', 'stop', 'status', 'logs',
                                            'migrate', 'create-user', 'seed-demo', 'build', 'import-policies'])
    parser.add_argument('--env-file', type=Path,
                        default=Path.home() / '.config/bankwise_ai/local-api.env')
    parser.add_argument('--username')
    parser.add_argument('--roles', nargs='+', default=['knowledge_user'])
    parser.add_argument('--balance', default='10000.00', help='seed-demo simulated EGP balance')
    parser.add_argument('--verified', action='store_true', help='seed-demo simulated KYC approval')
    parser.add_argument('--timeout', type=int, default=300,
                        help='seconds to wait for /health/ready (default: 300)')
    parser.add_argument('--build', action='store_true',
                        help='force an image rebuild even when the source is unchanged')
    parser.add_argument('--migrate', action='store_true',
                        help='force database migration and container recreation')
    parser.add_argument('--force', action='store_true',
                        help='used with import-policies to re-index unchanged files')
    parser.add_argument('--prune', action='store_true',
                        help='used with import-policies to remove policies whose files are missing')
    args = parser.parse_args()
    if args.command in {'status', 'logs', 'stop'}:
        command = {'status': ['ps', '-a', '--filter', 'name=^/' + NAME + '$'],
                   'logs': ['logs', '--tail', '100', NAME], 'stop': ['stop', NAME]}[args.command]
        return subprocess.call(['docker'] + command)
    if not args.env_file.is_file():
        parser.error('local environment file is missing; see docs/local_api.md')
    common = ['--network', 'host', '--env-file', str(args.env_file),
              '-v', 'bankwise-api-models:/models:ro', '-v', 'bankwise-rag-models:/rag-models:ro',
              '-v', 'bankwise-api-outputs:/app/outputs']
    # `start` is kept as an alias so older notes keep working with current images.
    if args.command in {'up', 'start'}:
        try:
            return start_api(args, common)
        except subprocess.CalledProcessError as error:
            print(f"\nCommand failed ({error.returncode}): {' '.join(map(str, error.cmd))}")
            return error.returncode or 1
    if args.command == 'build':
        try:
            build_image()
        except subprocess.CalledProcessError as error:
            print(f"\nCommand failed ({error.returncode}): {' '.join(map(str, error.cmd))}")
            return error.returncode or 1
        return 0
    if args.command == 'import-policies':
        try:
            if inspect('{{.State.Running}}') == 'true':
                run(['docker', 'stop', NAME])
            run(['docker', 'run', '--rm'] + common +
                ['-v', str(ROOT / 'knowledge/cib') + ':/policies:ro', IMAGE,
                 'python', '-m', 'bankwise.rag.import_policies', '/policies'] +
                (['--force'] if args.force else []) + (['--prune'] if args.prune else []))
            return start_api(args, common)
        except subprocess.CalledProcessError as error:
            print(f"\nCommand failed ({error.returncode}): {' '.join(map(str, error.cmd))}")
            return error.returncode or 1
    if inspect('{{.State.Running}}') == 'true':
        parser.error('stop the API before migrations or account provisioning (one audit writer)')
    if args.command == 'migrate':
        return subprocess.call(['docker', 'run', '--rm'] + common +
                               [IMAGE, 'python', '-m', 'alembic', 'upgrade', 'head'])
    if not args.username:
        parser.error('--username is required for create-user or seed-demo')
    if args.command == 'seed-demo':
        return subprocess.call(['docker', 'run', '--rm'] + common +
                               [IMAGE, 'python', '-m', 'bankwise.database.cli', 'seed-demo',
                                '--username', args.username, '--balance', args.balance] +
                               (['--verified'] if args.verified else []))
    return subprocess.call(['docker', 'run', '--rm', '-it'] + common +
                           [IMAGE, 'python', '-m', 'bankwise.database.cli', 'create-user',
                            '--username', args.username, '--roles'] + args.roles)


if __name__ == '__main__':
    raise SystemExit(main())
