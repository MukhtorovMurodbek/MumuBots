#!/bin/sh
set -e
if [ "$(id -u)" = "0" ]; then
    for path in "$RAILWAY_VOLUME_MOUNT_PATH" "$MUMU_VOLUME_PATH"; do
        case "$path" in
            ""|/) continue ;;
        esac
        if [ -d "$path" ]; then
            chown -R bots:bots "$path" 2>/dev/null || true
        fi
    done
    exec setpriv --reuid=bots --regid=bots --init-groups --no-new-privs --inh-caps=-all --bounding-set=-all "$@"
fi
exec "$@"
