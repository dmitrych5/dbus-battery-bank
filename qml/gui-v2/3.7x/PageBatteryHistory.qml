/*
** Copyright (C) 2023 Victron Energy B.V.
** See LICENSE.txt for license information.
**
** dbus-battery-bank: based on the stock page, with the rows grouped by data source for this
** project's services. The aggregate (ProductId 0xBA44) splits into "Shunt-provided data"
** (the SmartShunt's lifetime history, reset from the shunt itself) and "Driver-provided data"
** (what only the driver can compute), with the Clear button applying to the driver part only.
** Packs (ProductId 0xBA77) split into "BMS-provided data" and "Driver-provided data", and the
** confusing "reset history on the monitor" hint is suppressed (the BMS counters cannot be
** reset from here at all). Other battery services get the stock rows, headerless.
*/

import QtQuick
import Victron.VenusOS

Page {
	id: root

	required property string bindPrefix
	required property BatteryHistory history

	readonly property bool isBatteryBankAggregate: productId.value === 0xBA44
	readonly property bool isBatteryBankPack: productId.value === 0xBA77

	readonly property VeQuickItem productId: VeQuickItem {
		uid: root.bindPrefix + "/ProductId"
	}

	GradientListView {
		model: VisibleItemModel {
			SettingsListHeader {
				text: "Shunt-provided data"
				preferredVisible: root.isBatteryBankAggregate
			}

			SettingsListHeader {
				text: "BMS-provided data"
				preferredVisible: root.isBatteryBankPack
			}

			ListQuantity {
				//% "Deepest discharge"
				text: qsTrId("batteryalarms_deepest_discharge")
				preferredVisible: root.history.allowsDeepestDischarge
				unit: VenusOS.Units_AmpHour
				value: preferredVisible ? root.history.deepestDischarge.value : NaN
			}

			ListQuantity {
				//% "Last discharge"
				text: qsTrId("batteryhistory_last_discharge")
				preferredVisible: root.history.allowsLastDischarge
				unit: VenusOS.Units_AmpHour
				value: preferredVisible ? root.history.lastDischarge.value : NaN
			}

			ListQuantity {
				//% "Average discharge"
				text: qsTrId("batteryhistory_average_discharge")
				preferredVisible: root.history.allowsAverageDischarge
				unit: VenusOS.Units_AmpHour
				value: preferredVisible ? root.history.averageDischarge.value : NaN
			}

			ListText {
				//% "Total charge cycles"
				text: qsTrId("batteryhistory_total_charge_cycles")
				preferredVisible: root.history.allowsChargeCycles
				secondaryText: preferredVisible ? root.history.chargeCycles.value : ""
			}

			ListText {
				//% "Number of full discharges"
				text: qsTrId("batteryhistory_number_of_full_discharges")
				preferredVisible: root.history.allowsFullDischarges
				secondaryText: preferredVisible ? root.history.fullDischarges.value : ""
			}

			ListQuantity {
				//% "Cumulative Ah drawn"
				text: qsTrId("batteryhistory_cumulative_ah_drawn")
				preferredVisible: root.history.allowsTotalAhDrawn
				unit: VenusOS.Units_AmpHour
				value: preferredVisible ? root.history.totalAhDrawn.value : NaN
			}

			// For the aggregate these two carry the shunt's lifetime records and belong to the
			// shunt section; for a pack the same paths carry driver-computed extremes, shown by
			// the duplicate rows in the driver section below.
			ListQuantity {
				text: CommonWords.minimum_voltage
				preferredVisible: root.history.allowsMinimumVoltage && !root.isBatteryBankPack
				unit: VenusOS.Units_Volt_DC
				value: preferredVisible ? root.history.minimumVoltage.value : NaN
			}

			ListQuantity {
				text: CommonWords.maximum_voltage
				preferredVisible: root.history.allowsMaximumVoltage && !root.isBatteryBankPack
				unit: VenusOS.Units_Volt_DC
				value: preferredVisible ? root.history.maximumVoltage.value : NaN
			}

			ListText {
				//% "Synchronisation count"
				text: qsTrId("batteryhistory_synchronisation_count")
				preferredVisible: root.history.allowsAutomaticSyncs
				secondaryText: preferredVisible ? root.history.automaticSyncs.value : ""
			}

			ListQuantity {
				//% "Discharged energy"
				text: qsTrId("batteryhistory_discharged_energy")
				preferredVisible: root.history.allowsDischargedEnergy
				unit: VenusOS.Units_Energy_KiloWattHour
				value: preferredVisible ? root.history.dischargedEnergy.value : NaN
			}

			ListQuantity {
				//% "Charged energy"
				text: qsTrId("batteryhistory_charged_energy")
				preferredVisible: root.history.allowsChargedEnergy
				unit: VenusOS.Units_Energy_KiloWattHour
				value: preferredVisible ? root.history.chargedEnergy.value : NaN
			}

			SettingsListHeader {
				text: "Driver-provided data"
				preferredVisible: root.isBatteryBankAggregate || root.isBatteryBankPack
			}

			ListQuantity {
				text: CommonWords.minimum_voltage
				preferredVisible: root.history.allowsMinimumVoltage && root.isBatteryBankPack
				unit: VenusOS.Units_Volt_DC
				value: preferredVisible ? root.history.minimumVoltage.value : NaN
			}

			ListQuantity {
				text: CommonWords.maximum_voltage
				preferredVisible: root.history.allowsMaximumVoltage && root.isBatteryBankPack
				unit: VenusOS.Units_Volt_DC
				value: preferredVisible ? root.history.maximumVoltage.value : NaN
			}

			ListQuantity {
				//% "Minimum cell voltage"
				text: qsTrId("batteryhistory_minimum_cell_voltage")
				preferredVisible: root.history.allowsMinimumCellVoltage
				unit: VenusOS.Units_Volt_DC
				value: preferredVisible ? root.history.minimumCellVoltage.value : NaN
				precision: 3
			}

			ListQuantity {
				//% "Maximum cell voltage"
				text: qsTrId("batteryhistory_maximum_cell_voltage")
				preferredVisible: root.history.allowsMaximumCellVoltage
				unit: VenusOS.Units_Volt_DC
				value: preferredVisible ? root.history.maximumCellVoltage.value : NaN
				precision: 3
			}

			ListTemperature {
				text: CommonWords.minimum_temperature
				preferredVisible: root.history.allowsMinimumTemperature
				value: preferredVisible ? root.history.minimumTemperature.value : NaN
			}

			ListTemperature {
				text: CommonWords.maximum_temperature
				preferredVisible: root.history.allowsMaximumTemperature
				value: preferredVisible ? root.history.maximumTemperature.value : NaN
			}

			ListText {
				text: CommonWords.low_voltage_alarms
				preferredVisible: root.history.allowsLowVoltageAlarms
				secondaryText: preferredVisible ? root.history.lowVoltageAlarms.value : ""
			}

			ListText {
				text: CommonWords.high_voltage_alarms
				preferredVisible: root.history.allowsHighVoltageAlarms
				secondaryText: preferredVisible ? root.history.highVoltageAlarms.value : ""
			}

			ListText {
				//% "Time since last full charge"
				text: qsTrId("batteryhistory_time_since_last_full_charge")
				preferredVisible: root.history.allowsTimeSinceLastFullCharge
				secondaryText: preferredVisible ? Utils.secondsToString(root.history.timeSinceLastFullCharge.value) : ""
			}

			ListText {
				//% "Low starter battery voltage alarms"
				text: qsTrId("batteryhistory_low_starter_bat_voltage_alarms")
				preferredVisible: root.history.allowsLowStarterVoltageAlarms
				secondaryText: preferredVisible ? root.history.lowStarterVoltageAlarms.value : ""
			}

			ListText {
				//% "High starter battery voltage alarms"
				text: qsTrId("batteryhistory_high_starter_bat_voltage_alarms")
				preferredVisible: root.history.allowsHighStarterVoltageAlarms
				secondaryText: preferredVisible ? root.history.highStarterVoltageAlarms.value : ""
			}

			ListQuantity {
				//% "Minimum starter battery voltage"
				text: qsTrId("batteryhistory_minimum_starter_bat_voltage")
				preferredVisible: root.history.allowsMinimumStarterVoltage
				value: preferredVisible ? root.history.minimumStarterVoltage.value : NaN
				unit: VenusOS.Units_Volt_DC
			}

			ListQuantity {
				//% "Maximum starter battery voltage"
				text: qsTrId("batteryhistory_maximum_starter_bat_voltage")
				preferredVisible: root.history.allowsMaximumStarterVoltage
				value: preferredVisible ? root.history.maximumStarterVoltage.value : NaN
				unit: VenusOS.Units_Volt_DC
			}

			ListResetHistory {
				visible: !clearHistory.visible && !root.isBatteryBankPack
			}

			ListClearHistoryButton {
				id: clearHistory
				bindPrefix: root.bindPrefix
			}
		}
	}
}
