#!/bin/sh
# SPEC-001 R-NONROOT: start as root only to give the storage and model-cache volumes to uid 10001 once
# (volumes created by older root images keep their data), then run the real command as 10001.
set -eu

if [ "$(id -u)" = "0" ]; then
  for dir in /app/storage /app/cache; do
    mkdir -p "$dir"
    if [ "$(stat -c %u "$dir")" != "10001" ]; then
      chown -R 10001:10001 "$dir"
    fi
  done
  exec setpriv --reuid 10001 --regid 10001 --init-groups "$@"
fi
exec "$@"
