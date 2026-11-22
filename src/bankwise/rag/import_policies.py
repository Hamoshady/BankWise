"""Operator-only import: stop API first; no automatic startup writes or file deletion.

Pass ``force=True`` (CLI ``--force``) after changing the chunker/embedding pipeline so
unchanged files are re-indexed. Pass ``prune=True`` (CLI ``--prune``) to remove shared
policies whose files no longer exist; pruned rows are deleted only inside this explicit
operator command.
"""
import argparse
import hashlib
import secrets
from pathlib import Path
from sqlalchemy import select
from bankwise.core.bootstrap import build_security
from bankwise.core.passwords import hash_password
from bankwise.core.pii import redact_text
from bankwise.database.config import load_database_settings
from bankwise.database.session import Database
from bankwise.database.models import User, RagDocument
from bankwise.rag.postgres_store import PostgresVectorStore
from bankwise.rag.service import RAGService


def import_policies(database, runtime, directory, *, force=False, prune=False):
    files = sorted(Path(directory).glob('*.md'))
    if not files:
        raise ValueError('No policy files found')
    desired = {path.stem for path in files}
    changed = 0
    with database.session() as session, session.begin():
        # Serialize imports, including first creation of the disabled publisher account.
        from sqlalchemy import text
        session.execute(text('SELECT pg_advisory_xact_lock(81293417)'))
        owner = session.scalar(select(User).where(User.username == 'bankwise-policy-library'))
        if owner is None:
            owner = User(username='bankwise-policy-library', password_hash=hash_password(secrets.token_urlsafe(48)), active=False)
            session.add(owner)
            session.flush()
        if owner.active or owner.roles:
            raise ValueError('Policy publisher must be disabled and have no roles')
        service = RAGService(PostgresVectorStore(session, owner.id))
        for path in files:
            import re
            if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}', path.stem) or path.stat().st_size > 16384:
                raise ValueError('Invalid policy filename or size')
            content = path.read_text(encoding='utf-8')
            if not content.strip() or '\x00' in content or redact_text(content) != content or len(content)>8000:
                raise ValueError('Policy text rejected')
            digest = hashlib.sha256(content.encode()).hexdigest()
            row = session.scalar(select(RagDocument).where(RagDocument.owner_id == owner.id, RagDocument.source == path.stem))
            if not force and row is not None and row.shared and row.content_hash == digest:
                continue
            service.ingest_text(content, path.stem)
            row = session.scalar(select(RagDocument).where(RagDocument.owner_id == owner.id, RagDocument.source == path.stem))
            row.shared, row.content_hash = True, digest
            session.flush()
            changed += 1
        if prune:
            stale = session.scalars(select(RagDocument.source).where(
                RagDocument.owner_id == owner.id, RagDocument.shared.is_(True),
                ~RagDocument.source.in_(desired))).all()
            for source in stale:
                service.delete_source(source)
        runtime.service.record_access('rag.import_authorized', str(owner.id))
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--force', action='store_true', help='re-index files even when their content hash is unchanged')
    parser.add_argument('--prune', action='store_true', help='delete shared policies whose files were removed')
    args = parser.parse_args()
    database = Database(load_database_settings())
    try:
        database.ready()
        print('Changed policies:', import_policies(database, build_security(), args.directory,
                                                   force=args.force, prune=args.prune))
    finally:
        database.close()

if __name__ == '__main__':
    main()
