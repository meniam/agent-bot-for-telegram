#!/bin/sh
# Pre-create the state dirs the config points at. The loader mkdir's logs /
# messages / tasks itself, but commands_dir (and uploads) must pre-exist.
# This must run here, not in the Dockerfile: /app/var is a bind mount, so the
# host dir mounts OVER any build-time dirs and hides them. Only a runtime mkdir
# (after the mount) is visible.
set -e

mkdir -p \
  /app/var/brain/commands \
  /app/var/brain/scripts \
  /app/var/brain/logs \
  /app/var/brain/messages \
  /app/var/brain/tasks \
  /app/var/brain/uploads

exec "$@"
