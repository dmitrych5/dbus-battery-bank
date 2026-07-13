#!/bin/bash
# Installs this project's GUI-v2 pages over the stock ones via the overlay-fs app, leaving the
# root filesystem untouched. enable.sh runs this on every boot, so a firmware upgrade (which
# replaces the stock pages) heals itself on the next start.
#
# Only the local display / Remote Console QML is patched. The browser (WASM) build of GUI-v2
# cannot be patched without a full Qt rebuild and is left as whatever is installed (the old
# driver's prebuilt WASM works for the pack pages, since the ProductIds match).

APP_DIR=/data/apps/dbus-battery-bank
GUI_V2_DIR=/opt/victronenergy/gui-v2
GUI_V2_PAGES_DIR="$GUI_V2_DIR/Victron/VenusOS/pages/settings/devicelist/battery"
OVERLAY_APP_NAME=dbus-battery-bank_gui

if [ ! -d "$GUI_V2_DIR" ]; then
    echo "GUI-v2 is not installed on this system; nothing to do."
    exit 0
fi

# Pick the QML set matching the Venus OS version, e.g. "v3.67" -> minor version 67 -> 3.6x.
venus_version=$(head -n 1 /opt/victronenergy/version)
minor=$(echo "$venus_version" | sed -n 's|^v3\.\([0-9]\).*|\1|p')
case "$minor" in
    6) qml_source_dir="$APP_DIR/qml/gui-v2/3.6x" ;;
    7) qml_source_dir="$APP_DIR/qml/gui-v2/3.7x" ;;
    *)
        echo ">>> WARNING: Venus OS $venus_version has no matching GUI-v2 QML set in this project;"
        echo ">>>          the stock battery pages stay in place until a set is added."
        exit 0
        ;;
esac

if [ ! -d /data/apps/overlay-fs ]; then
    echo "ERROR: The overlay-fs app is required (see github.com/mr-manuel/venus-os_overlay-fs)." >&2
    exit 1
fi
if ! bash /data/apps/overlay-fs/add-app-and-directory.sh "$OVERLAY_APP_NAME" "$GUI_V2_DIR"; then
    echo "ERROR: Could not overlay $GUI_V2_DIR" >&2
    exit 1
fi

files_changed=0
for file in "$qml_source_dir"/*.qml; do
    if ! cmp -s "$file" "$GUI_V2_PAGES_DIR/$(basename "$file")"; then
        cp "$file" "$GUI_V2_PAGES_DIR/"
        echo "|- Installed $(basename "$file")"
        files_changed=$((files_changed + 1))
    fi
done

if [ "$files_changed" -gt 0 ] && [ -e /service/start-gui ]; then
    echo "Restarting the GUI to load the updated pages..."
    svc -t /service/start-gui
fi
echo "GUI-v2 pages up to date ($files_changed file(s) changed)."
