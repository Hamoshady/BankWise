#!/bin/sh
set -eu
pg_restore --exit-on-error --single-transaction --no-owner --no-privileges --clean --if-exists --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" /snapshot/database.dump
touch "$PGDATA/bankwise_snapshot_restored"
