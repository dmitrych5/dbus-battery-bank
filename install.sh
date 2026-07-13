#!/bin/bash
# Installs dbus-battery-bank and stops the old dbus-serialbattery + dbus-aggregate-batteries
# stack. Every step is reversible: revert.sh restores the old stack exactly as it was.
#
# The old stack's files are never deleted — its serial-starter config moves into
# old-stack-backup/ and its rc.local lines are commented out with a recognizable marker.
#
# Deliberately no udev/serial-starter exclusion rules in this version: without the old
# serial-starter config, serial-starter probes the battery port with other drivers, which is
# exactly the interference the pollers detect and ride out (proven for half a year on the old
# stack). Exclusion rules can be added later as an optimization.
#
# The old driver's custom GUI-v2 pages must stay installed: the new services reuse them (they
# key on the same ProductIds), so do not run the old stack's custom-gui-uninstall.sh.

set -e

APP_DIR=/data/apps/dbus-battery-bank
BACKUP_DIR="$APP_DIR/old-stack-backup"
RC_LOCAL=/data/rc.local
DISABLED_MARKER="#disabled-by-dbus-battery-bank#"
SERIAL_STARTER_CONF=/data/conf/serial-starter.d/dbus-serialbattery.conf

if [ ! -d /service ]; then
    echo "This script must run on a Venus OS device (no /service directory found)." >&2
    exit 1
fi
if [ ! -f "$APP_DIR/config.ini" ]; then
    echo "No $APP_DIR/config.ini found. Copy config.example.ini to config.ini and adjust it first." >&2
    exit 1
fi

echo "Stopping the old stack (reversible; see revert.sh)..."

# Keep serial-starter from launching dbus-serialbattery for the battery port.
if [ -f "$SERIAL_STARTER_CONF" ]; then
    svc -d /service/serial-starter
    mv "$SERIAL_STARTER_CONF" "$BACKUP_DIR/"
    svc -u /service/serial-starter
    echo "|- Moved dbus-serialbattery serial-starter config to $BACKUP_DIR/"
fi

# Stop the currently running old services.
for service in /service/dbus-serialbattery.* /service/dbus-aggregate-batteries; do
    if [ -e "$service" ]; then
        svc -d "$service"
        echo "|- Stopped $service"
    fi
done

# Keep the old stack from re-registering on reboot, reversibly.
if [ -f "$RC_LOCAL" ]; then
    sed -i "/dbus-serialbattery\|dbus-aggregate-batteries/ { /^$DISABLED_MARKER/! s|^|$DISABLED_MARKER| }" "$RC_LOCAL"
    echo "|- Commented old stack lines out of $RC_LOCAL"
fi

echo "Registering dbus-battery-bank..."
bash "$APP_DIR/enable.sh"

if ! grep -q "dbus-battery-bank/enable.sh" "$RC_LOCAL" 2>/dev/null; then
    echo "bash $APP_DIR/enable.sh # dbus-battery-bank" >> "$RC_LOCAL"
    chmod +x "$RC_LOCAL"
    echo "|- Added enable.sh to $RC_LOCAL"
fi

echo "Done. Watch the log with: tail -f /var/log/dbus-battery-bank/current | tai64nlocal"
