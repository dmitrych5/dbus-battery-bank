/*
** dbus-battery-bank: the live control diagnostics texts rendered by the driver
** (/Info/ChargeModeDebug and friends). The aggregate shows the bank state machine and limit
** picture; a pack shows its own contribution to the bank decision. Reached from the "Debug"
** entry on the battery page.
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
		}
	}
}
