#!/bin/sh
# Make the folders under /data, then start the server (or whatever command was given).
set -e
mkdir -p "$EPHEMERAL_DATA_DIR/sweph" "$EPHEMERAL_DATA_DIR/cities" "$EPHEMERAL_DATA_DIR/logs" "$EPHEMERAL_DATA_DIR/federation"
if [ -z "$(ls "$EPHEMERAL_DATA_DIR"/sweph/*.se1 2>/dev/null)" ]; then
  echo "Note: no Swiss Ephemeris files in $EPHEMERAL_DATA_DIR/sweph — calculations use the built-in (less precise) Moshier ephemeris." >&2
  echo "      Run 'ephemeral fetch-ephemeris' to download them." >&2
fi
exec "$@"
