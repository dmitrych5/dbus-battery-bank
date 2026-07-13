"""Applies this project's GUI changes to a checkout of mr-manuel's venus-os_gui-v2 fork
(branch dbus-serialbattery/venus-os_v3.6x/gui-v2_v1.1.x) before the WASM build.

Our whole-page copies (PageBattery, the history page, the debug page) are dropped in whole,
with new pages also registered in the fork's CMakeLists. The settings page is newer on the
branch (ResetSocToApply), so the trip-reset control is inserted instead of replacing the
file. The fork's PageBatteryDbusSerialbattery.qml stays in the build but is unreferenced —
our PageBattery folds its content into the main battery page.
"""

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
PAGES_SUBDIR = "pages/settings/devicelist/battery"

REPLACED_PAGES = ("PageBattery.qml", "PageBatteryHistory.qml", "PageBatteryBankDebug.qml")
CMAKE_REGISTRATION_ANCHOR = f"    {PAGES_SUBDIR}/PageBatteryDbusSerialbattery.qml\n"
NEW_PAGES_TO_REGISTER = ("PageBatteryBankDebug.qml",)

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

    for page_name in REPLACED_PAGES:
        (pages_dir / page_name).write_text((PROJECT_DIR / "qml/gui-v2/3.6x" / page_name).read_text())
        print(f"{page_name} replaced with our edited copy")

    cmake_path = fork_dir / "CMakeLists.txt"
    cmake_text = cmake_path.read_text()
    for page_name in NEW_PAGES_TO_REGISTER:
        if f"{PAGES_SUBDIR}/{page_name}" in cmake_text:
            print(f"{page_name} already registered in CMakeLists.txt")
            continue
        if CMAKE_REGISTRATION_ANCHOR not in cmake_text:
            raise SystemExit("ERROR: CMakeLists registration anchor not found; the branch layout changed")
        cmake_text = cmake_text.replace(CMAKE_REGISTRATION_ANCHOR, CMAKE_REGISTRATION_ANCHOR + f"    {PAGES_SUBDIR}/{page_name}\n")
        print(f"{page_name} registered in CMakeLists.txt")
    cmake_path.write_text(cmake_text)

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
