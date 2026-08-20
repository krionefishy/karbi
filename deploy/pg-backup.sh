#!/bin/sh
# Daily pg_dump loop for the pg-backup service in deploy/compose.yaml.
# Writes custom-format dumps into $BACKUP_DIR and keeps the newest
# $BACKUP_RETENTION of them. Connection settings come from the standard
# PG* environment variables. See docs/BACKUPS.md for restore steps.
set -eu

: "${BACKUP_DIR:=/backups}"
: "${BACKUP_RETENTION:=14}"
: "${BACKUP_INTERVAL_SECONDS:=86400}"

while true; do
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    partial="$BACKUP_DIR/.karbi-$stamp.dump.partial"
    dump="$BACKUP_DIR/karbi-$stamp.dump"
    if pg_dump --format=custom --file="$partial"; then
        mv "$partial" "$dump"
        echo "backup written: $dump ($(du -h "$dump" | cut -f1))"
        # Keep the newest $BACKUP_RETENTION dumps, delete the rest.
        ls -1t "$BACKUP_DIR"/karbi-*.dump 2>/dev/null \
            | tail -n +$((BACKUP_RETENTION + 1)) \
            | while read -r old; do rm -f "$old" && echo "backup pruned: $old"; done
    else
        rm -f "$partial"
        echo "backup FAILED at $stamp" >&2
    fi
    sleep "$BACKUP_INTERVAL_SECONDS"
done
