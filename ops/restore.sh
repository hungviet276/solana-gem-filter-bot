#!/bin/sh
set -eu
FILE="${1:?usage: restore.sh backup.dump}"
test -f "$FILE"
if test -f "$FILE.sha256"; then sha256sum -c "$FILE.sha256"; fi
echo "Restore is destructive for the target database. Set CONFIRM_RESTORE=yes to continue."
test "${CONFIRM_RESTORE:-no}" = "yes"
docker compose exec -T postgres pg_restore -U "${POSTGRES_USER:-gem_filter}" -d "${POSTGRES_DB:-gem_filter}" --clean --if-exists < "$FILE"

