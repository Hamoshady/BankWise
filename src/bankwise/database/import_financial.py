"""Atomic, hash-checked import of the CTU export. Does not overwrite existing data."""
import argparse
import csv
import gzip
import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
import sqlalchemy as sa
from bankwise.database.config import load_database_settings
from bankwise.database.session import Database
from bankwise.database.financial_schema import TABLES, imports, grants
from bankwise.database.models import User


def import_data(session, directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text())
    if set(manifest['tables']) != set(TABLES):
        raise ValueError('Expected all eight CTU tables')
    old = session.scalar(sa.select(imports.c.manifest).where(imports.c.name == 'berka'))
    if old:
        if old['tables'] != manifest['tables']:
            raise ValueError('A different dataset is already imported; refusing to overwrite')
        return old
    for name, table in TABLES.items():
        path = directory / (name + '.csv.gz')
        expected = manifest['tables'][name]
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected['sha256']:
            raise ValueError('Dataset checksum mismatch: ' + name)
        count, batch = 0, []
        with gzip.open(path, 'rt', encoding='utf-8', newline='') as stream:
            reader = csv.DictReader(stream)
            if set(reader.fieldnames) != set(table.c.keys()):
                raise ValueError('Unexpected columns: ' + name)
            for record in reader:
                values = {}
                for column in table.c:
                    value = record[column.name]
                    if value == '\\N': value = None
                    elif isinstance(column.type, sa.Date): value = date.fromisoformat(value)
                    elif isinstance(column.type, sa.Integer): value = int(value)
                    elif isinstance(column.type, sa.Numeric): value = Decimal(value)
                    values[column.name] = value
                batch.append(values)
                count += 1
                if len(batch) == 2000:
                    session.execute(table.insert(), batch)
                    batch = []
            if batch: session.execute(table.insert(), batch)
        if count != expected['rows']:
            raise ValueError('Row count mismatch: ' + name)
        print(name, count, flush=True)
    session.execute(imports.insert().values(name='berka', manifest=manifest))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--grant', action='append', default=[], help='username:client_id (research sample only)')
    args = parser.parse_args()
    db = Database(load_database_settings())
    try:
        with db.session() as session, session.begin():
            session.execute(sa.text('SELECT pg_advisory_xact_lock(81293418)'))
            import_data(session, args.directory)
            for item in args.grant:
                username, client_id = item.rsplit(':',1)
                owner = session.scalar(sa.select(User.id).where(User.username == username))
                if owner is None: raise ValueError('Unknown application user')
                exists = session.scalar(sa.select(grants.c.client_id).where(grants.c.owner_id==owner,grants.c.client_id==int(client_id)))
                if exists is None: session.execute(grants.insert().values(owner_id=owner,client_id=int(client_id)))
    finally: db.close()


if __name__ == '__main__': main()
