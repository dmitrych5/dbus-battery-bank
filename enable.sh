#!/bin/bash
# Registers the dbus-battery-bank daemontools service. Idempotent; runs from install.sh and
# from /data/rc.local on every boot (the /service directory does not persist across reboots).

APP_DIR=/data/apps/dbus-battery-bank

chmod +x "$APP_DIR/service/run" "$APP_DIR/service/log/run"
mkdir -p /var/log/dbus-battery-bank

if [ ! -e /service/dbus-battery-bank ]; then
    ln -s "$APP_DIR/service" /service/dbus-battery-bank
fi
