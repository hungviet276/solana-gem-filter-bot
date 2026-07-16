#!/bin/sh
set -eu
OUT_DIR="${1:-backups}"
mkdir -p "$OUT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$OUT_DIR/gem_filter_$STAMP.dump"
docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-gem_filter}" -d "${POSTGRES_DB:-gem_filter}" --format=custom > "$FILE"
sha256sum "$FILE" > "$FILE.sha256"
echo "$FILE"

