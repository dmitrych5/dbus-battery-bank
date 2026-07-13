#!/bin/bash
# Reverts install.sh: stops dbus-battery-bank and restores the old dbus-serialbattery +
# dbus-aggregate-batteries stack exactly as it was.

set -e

APP_DIR=/data/apps/dbus-battery-bank
BACKUP_DIR="$APP_DIR/old-stack-backup"
RC_LOCAL=/data/rc.local
DISABLED_MARKER="#disabled-by-dbus-battery-bank#"

echo "Stopping dbus-battery-bank..."
if [ -e /service/dbus-battery-bank ]; then
    svc -d /service/dbus-battery-bank
    rm /service/dbus-battery-bank
fi
sed -i "\|dbus-battery-bank/enable.sh|d" "$RC_LOCAL" 2>/dev/null || true

echo "Restoring the old stack..."
if [ -f "$BACKUP_DIR/dbus-serialbattery.conf" ]; then
    mkdir -p /data/conf/serial-starter.d
    mv "$BACKUP_DIR/dbus-serialbattery.conf" /data/conf/serial-starter.d/
    echo "|- Restored the dbus-serialbattery serial-starter config"
fi
if [ -f "$RC_LOCAL" ]; then
    sed -i "s|^$DISABLED_MARKER||" "$RC_LOCAL"
    echo "|- Uncommented the old stack lines in $RC_LOCAL"
fi

echo "Done. Now start the old stack the usual way:"
echo "  bash /data/apps/dbus-serialbattery/enable.sh"
echo "  bash /data/apps/dbus-aggregate-batteries/restart.sh"
