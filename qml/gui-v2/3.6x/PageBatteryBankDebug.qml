/*
** dbus-battery-bank: the live control diagnostics texts rendered by the driver
** (/Info/ChargeModeDebug and friends). The aggregate shows the bank state machine and limit
** picture; a pack shows its own contribution to the bank decision, plus the manual SoC reset
** control (a maintenance action, so it lives here). Reached from the "Debug" entry on the
** battery page.
*/

import QtQuick
import Victron.VenusOS

Page {
	id: root

	property string bindPrefix

	GradientListView {
		model: VisibleItemModel {
			ListItem {
				//% "General Values"
				text: qsTrId("dbus_serialbattery_general_values")

				bottomContentChildren: [
					PrimaryListLabel {
						topPadding: 0
						bottomPadding: 0
						color: Theme.color_font_secondary
						text: chargeModeDebug.valid ? chargeModeDebug.value : "--"
						horizontalAlignment: Text.AlignHCenter
					}
				]

				preferredVisible: chargeModeDebug.valid && chargeModeDebug.value !== ""

				VeQuickItem {
					id: chargeModeDebug
					uid: root.bindPrefix + "/Info/ChargeModeDebug"
				}
			}

			ListItem {
				//% "Switch to Float Requirements"
				text: qsTrId("dbus_serialbattery_general_switch_to_float_requirements")

				bottomContentChildren: [
					PrimaryListLabel {
						topPadding: 0
						bottomPadding: 0
						color: Theme.color_font_secondary
						text: chargeModeDebugFloat.valid ? chargeModeDebugFloat.value : "--"
						horizontalAlignment: Text.AlignHCenter
					}
				]

				preferredVisible: chargeModeDebugFloat.valid && chargeModeDebugFloat.value !== ""

				VeQuickItem {
					id: chargeModeDebugFloat
					uid: root.bindPrefix + "/Info/ChargeModeDebugFloat"
				}
			}

			ListItem {
				//% "Switch to Bulk Requirements"
				text: qsTrId("dbus_serialbattery_general_switch_to_bulk_requirements")

				bottomContentChildren: [
					PrimaryListLabel {
						topPadding: 0
						bottomPadding: 0
						color: Theme.color_font_secondary
						text: chargeModeDebugBulk.valid ? chargeModeDebugBulk.value : "--"
						horizontalAlignment: Text.AlignHCenter
					}
				]

				preferredVisible: chargeModeDebugBulk.valid && chargeModeDebugBulk.value !== ""

				VeQuickItem {
					id: chargeModeDebugBulk
					uid: root.bindPrefix + "/Info/ChargeModeDebugBulk"
				}
			}

			ListButton {
				//% "Reset SoC to"
				text: qsTrId("dbus_serialbattery_settings_reset_soc_to")
				secondaryText: Units.getCombinedDisplayText(VenusOS.Units_Percentage, resetSocToItem.value)
				preferredVisible: resetSocToItem.valid
				onClicked: Global.dialogLayer.open(resetSocToDialogComponent)

				Component {
					id: resetSocToDialogComponent

					ModalDialog {

						property int resetSocTo: resetSocToItem.value

						//% "Reset SoC to"
						title: qsTrId("dbus_serialbattery_settings_reset_soc_to")

						onAccepted: resetSocToItem.setValue(resetSocTo)

						contentItem: ModalDialog.FocusableContentItem {
							Column {
								width: parent.width

								Label {
									anchors.horizontalCenter: parent.horizontalCenter
									font.pixelSize: Theme.font_size_h3
									text: "%1%".arg(resetSocTo)
								}

								Item {
									width: 1
									height: Theme.geometry_modalDialog_content_margins / 2
								}

								// No KeyNavigationHighlight here: in the gui-v2 branch the WASM
								// is built from it is an Item, not an attached type, and the
								// attached syntax makes the whole page fail to load (the trap
								// behind an unclickable menu entry).
								Slider {
									id: resetToSocSlider

									anchors.horizontalCenter: parent.horizontalCenter
									width: parent.width - (2 * Theme.geometry_modalDialog_content_horizontalMargin)
									value: resetSocTo
									from: 0
									to: 100
									stepSize: 1
									focus: true
									onMoved: resetSocTo = value
								}
							}
						}
					}

				}

				VeQuickItem {
					id: resetSocToItem
					uid: root.bindPrefix + "/Settings/ResetSocTo"
				}
			}
		}
	}
}
