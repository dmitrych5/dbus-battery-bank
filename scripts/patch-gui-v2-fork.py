"""Applies this project's GUI changes to a checkout of mr-manuel's venus-os_gui-v2 fork
(branch dbus-serialbattery/venus-os_v3.6x/gui-v2_v1.1.x) before the WASM build.

PageBattery.qml is byte-identical between that branch and our qml/gui-v2/3.6x base, so our
edited copy is dropped in whole. The settings page is newer on the branch (ResetSocToApply),
so the trip-reset control is inserted instead of replacing the file.
"""

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
PAGES_SUBDIR = "pages/settings/devicelist/battery"

TRIP_RESET_ANCHOR = """\t\t\t\tVeQuickItem {
\t\t\t\t\tid: resetSocToApplyItem
\t\t\t\t\tuid: root.bindPrefix + "/Settings/ResetSocToApply"
\t\t\t\t}
\t\t\t}
"""


def trip_reset_block() -> str:
    """The same control our qml/gui-v2 settings pages carry; extracted from there so the two
    stay in sync."""
    our_settings = (PROJECT_DIR / "qml/gui-v2/3.6x/PageBatteryDbusSerialbatterySettings.qml").read_text()
    start = our_settings.index("\n\t\t\tListButton {\n\t\t\t\ttext: \"Reset protection trips\"")
    end = our_settings.index("uid: root.bindPrefix + \"/Settings/ResetProtectionTrips\"", start)
    end = our_settings.index("\n\t\t\t}\n", end) + len("\n\t\t\t}\n")
    return our_settings[start:end]


def main() -> None:
    fork_dir = Path(sys.argv[1])
    pages_dir = fork_dir / PAGES_SUBDIR

    our_page_battery = PROJECT_DIR / "qml/gui-v2/3.6x/PageBattery.qml"
    (pages_dir / "PageBattery.qml").write_text(our_page_battery.read_text())
    print("PageBattery.qml replaced with our edited copy")

    settings_path = pages_dir / "PageBatteryDbusSerialbatterySettings.qml"
    settings_text = settings_path.read_text()
    if "ResetProtectionTrips" in settings_text:
        print("Settings page already contains the trip-reset control")
        return
    if TRIP_RESET_ANCHOR not in settings_text:
        raise SystemExit("ERROR: trip-reset anchor not found in the fork's settings page; the branch layout changed")
    settings_path.write_text(settings_text.replace(TRIP_RESET_ANCHOR, TRIP_RESET_ANCHOR + trip_reset_block()))
    print("Trip-reset control inserted into the settings page")


if __name__ == "__main__":
    main()
