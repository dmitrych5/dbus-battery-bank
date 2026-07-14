"""Applies this project's GUI changes to a checkout of mr-manuel's venus-os_gui-v2 fork
(branch dbus-serialbattery/venus-os_v3.6x/gui-v2_v1.1.x) before the WASM build.

Our whole-page copies are dropped in whole, with new (non-stock) pages also registered in the
fork's CMakeLists — replacing a stock page needs no registration, adding one does. The fork's
PageBatteryDbusSerialbattery.qml and PageBatteryDbusSerialbatterySettings.qml stay in the
build but are unreferenced: our PageBattery folds their content into the main battery page
and the Debug submenu.
"""

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
PAGES_SUBDIR = "pages/settings/devicelist/battery"

REPLACED_PAGES = (
    "PageBattery.qml",
    "PageBatteryDetails.qml",
    "PageBatteryHistory.qml",
    "PageBatteryBankDebug.qml",
    "PageBatteryCellVoltages.qml",
    "PageBatteryTimeToSoc.qml",
)
CMAKE_REGISTRATION_ANCHOR = f"    {PAGES_SUBDIR}/PageBatteryDbusSerialbattery.qml\n"
NEW_PAGES_TO_REGISTER = ("PageBatteryBankDebug.qml", "PageBatteryCellVoltages.qml", "PageBatteryTimeToSoc.qml")


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


if __name__ == "__main__":
    main()
