"""Read-only CTU Financial export. Install PyMySQL in a tooling environment first.
Raw files remain in ignored outputs/. No permission to redistribute is inferred.
"""
import argparse
import csv
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

TABLES = ('district', 'client', 'account', 'disp', 'card', 'loan', 'order', 'trans')


def main():
    import pymysql
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('outputs/datasets/berka'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {'dataset': 'PKDD99 Financial / CTU', 'source_url': 'https://relational.fel.cvut.cz/dataset/Financial',
                'downloaded_at': datetime.now(timezone.utc).isoformat(), 'license': 'Not explicitly specified by upstream; local research use, no redistribution.', 'tables': {}}
    connection = pymysql.connect(host='relational.fel.cvut.cz', user='guest', password='ctu-relational',
                                 database='financial', connect_timeout=20, read_timeout=90,
                                 cursorclass=pymysql.cursors.SSCursor, charset='utf8mb4')
    try:
        for table in TABLES:
            destination = args.output / (table + '.csv.gz')
            temporary = destination.with_suffix('.partial')
            with connection.cursor() as cursor, gzip.open(temporary, 'wt', encoding='utf-8', newline='') as stream:
                cursor.execute('SELECT * FROM `' + table + '`')
                fields = [col[0] for col in cursor.description]
                writer = csv.writer(stream)
                writer.writerow(fields)
                count = 0
                for row in cursor:
                    writer.writerow(['\\N' if v is None else v for v in row])
                    count += 1
            temporary.replace(destination)
            manifest['tables'][table] = {'rows': count, 'columns': fields, 'bytes': destination.stat().st_size,
                                         'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}
            print(table, count, destination.stat().st_size, flush=True)
    finally:
        connection.close()
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
