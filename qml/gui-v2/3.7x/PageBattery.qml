/*
** Copyright (C) 2023 Victron Energy B.V.
** See LICENSE.txt for license information.
**
** dbus-battery-bank: for this project's services (aggregate 0xBA44, packs 0xBA77) the former
** "dbus-serialbattery - General" page is folded into this main page (overview and temperature
** tiles, power, charge mode, limits, allow-to, active alarms), the trip-reset button surfaces
** at the top while a protection trip is latched, and the stock rows/submenus this layout
** supersedes (Battery V/I/P group, battery temperature row, IO and Parameters submenus,
** per-pack Details) are hidden. Other battery services keep the stock layout.
*/

import QtQuick
import Victron.VenusOS

Page {
	id: root

	required property string bindPrefix
	readonly property bool isFiamm48TL: productId.value === ProductInfo.ProductId_Battery_Fiamm48TL
	readonly property bool isParallelBms: nrOfBmses.dataItem.valid
	readonly property bool isBatteryBank: productId.value === 0xBA44 || productId.value === 0xBA77
	readonly property bool isBatteryBankPack: productId.value === 0xBA77

	title: battery.name

	Device {
		id: battery
		serviceUid: root.bindPrefix
	}

	function getActiveAlarmsText(){
		let result = []
		if (alarmLowBatteryVoltage.valid && alarmLowBatteryVoltage.value !== 0) {
			// "Low battery voltage"
			result.push((alarmLowBatteryVoltage.value === 2 ? "⚠️ " : "") + CommonWords.low_battery_voltage);
		}
		if (alarmHighBatteryVoltage.valid && alarmHighBatteryVoltage.value !== 0) {
			// "High battery voltage"
			result.push((alarmHighBatteryVoltage.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_high_battery_voltage"));
		}
		if (alarmHighCellVoltage.valid && alarmHighCellVoltage.value !== 0) {
			// "High cell voltage"
			result.push((alarmHighCellVoltage.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_high_cell_voltage"));
		}
		if (alarmHighChargeCurrent.valid && alarmHighChargeCurrent.value !== 0) {
			// "High charge current"
			result.push((alarmHighChargeCurrent.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_high_charge_current"));
		}
		if (alarmHighCurrent.valid && alarmHighCurrent.value !== 0) {
			// "High current"
			result.push((alarmHighCurrent.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_high_current"));
		}
		if (alarmHighDischargeCurrent.valid && alarmHighDischargeCurrent.value !== 0) {
			// "High discharge current"
			result.push((alarmHighDischargeCurrent.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_high_discharge_current"));
		}
		if (alarmLowSoc.valid && alarmLowSoc.value !== 0) {
			// "Low SOC"
			result.push((alarmLowSoc.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_low_soc"));
		}
		if (alarmStateOfHealth.valid && alarmStateOfHealth.value !== 0) {
			// "State of health"
			result.push((alarmStateOfHealth.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_state_of_health"));
		}
		if (alarmLowStarterVoltage.valid && alarmLowStarterVoltage.value !== 0) {
			// "Low starter voltage"
			result.push((alarmLowStarterVoltage.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_low_starter_voltage"));
		}
		if (alarmHighStarterVoltage.valid && alarmHighStarterVoltage.value !== 0) {
			// "High starter voltage"
			result.push((alarmHighStarterVoltage.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_high_starter_voltage"));
		}
		if (alarmLowTemperature.valid && alarmLowTemperature.value !== 0) {
			// "Low temperature"
			result.push((alarmLowTemperature.value === 2 ? "⚠️ " : "") + CommonWords.low_temperature);
		}
		if (alarmHighTemperature.valid && alarmHighTemperature.value !== 0) {
			// "High temperature"
			result.push((alarmHighTemperature.value === 2 ? "⚠️ " : "") + CommonWords.high_temperature);
		}
		if (alarmBatteryTemperatureSensor.valid && alarmBatteryTemperatureSensor.value !== 0) {
			// "Battery temperature sensor"
			result.push((alarmBatteryTemperatureSensor.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_battery_temperature_sensor"));
		}
		if (alarmMidPointVoltage.valid && alarmMidPointVoltage.value !== 0) {
			// "Midpoint voltage"
			result.push((alarmMidPointVoltage.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_midpoint_voltage"));
		}
		if (alarmFuseBlown.valid && alarmFuseBlown.value !== 0) {
			// "Fuse blown"
			result.push((alarmFuseBlown.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_fuse_blown"));
		}
		if (alarmHighInternalTemperature.valid && alarmHighInternalTemperature.value !== 0) {
			// "High internal temperature"
			result.push((alarmHighInternalTemperature.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_high_internal_temperature"));
		}
		if (alarmLowChargeTemperature.valid && alarmLowChargeTemperature.value !== 0) {
			// "Low charge temperature"
			result.push((alarmLowChargeTemperature.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_low_charge_temperature"));
		}
		if (alarmHighChargeTemperature.valid && alarmHighChargeTemperature.value !== 0) {
			// "High charge temperature"
			result.push((alarmHighChargeTemperature.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_high_charge_temperature"));
		}
		if (alarmInternalFailure.valid && alarmInternalFailure.value !== 0) {
			// "Internal failure"
			result.push((alarmInternalFailure.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_internal_failure"));
		}
		if (alarmCellImbalance.valid && alarmCellImbalance.value !== 0) {
			// "Cell imbalance"
			result.push((alarmCellImbalance.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_cell_imbalance"));
		}
		if (alarmLowCellVoltage.valid && alarmLowCellVoltage.value !== 0) {
			// "Low cell voltage"
			result.push((alarmLowCellVoltage.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_low_cell_voltage"));
		}
		if (alarmBmsCable.valid && alarmBmsCable.value !== 0) {
			// "BMS cable"
			result.push((alarmBmsCable.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_bms_cable"));
		}
		if (alarmContactor.valid && alarmContactor.value !== 0) {
			// "Bad contactor"
			result.push((alarmContactor.value === 2 ? "⚠️ " : "") + qsTrId("batteryalarms_contactor"));
		}

		// Sort the alarms alphabetically and join them with a comma
		result.sort()
		return result.join(", ")
	}

	GradientListView {
		model: VisibleItemModel {
			ListButton {
				text: "Reset protection trips"
				secondaryText: "Reset"
				// Only the aggregate publishes /ProtectionTripped, and only while a trip is
				// latched does the operator need the button here.
				preferredVisible: protectionTrippedItem.valid && protectionTrippedItem.value === 1
				onClicked: Global.dialogLayer.open(resetProtectionTripsDialogComponent)

				Component {
					id: resetProtectionTripsDialogComponent

					ModalDialog {
						title: "Reset protection trips"

						onAccepted: resetProtectionTripsItem.setValue(1)

						contentItem: ModalDialog.FocusableContentItem {
							Column {
								width: parent.width

								Label {
									anchors.horizontalCenter: parent.horizontalCenter
									width: parent.width - (2 * Theme.geometry_modalDialog_content_horizontalMargin)
									wrapMode: Text.Wrap
									horizontalAlignment: Text.AlignHCenter
									text: "Clear the latched protection trips and restore the charge/discharge limits? Only reset after investigating the cause."
								}
							}
						}
					}
				}

				VeQuickItem {
					id: protectionTrippedItem
					uid: root.bindPrefix + "/ProtectionTripped"
				}

				VeQuickItem {
					id: resetProtectionTripsItem
					uid: root.bindPrefix + "/Settings/ResetProtectionTrips"
				}
			}

			ListRadioButtonGroup {
				text: CommonWords.switch_mode
				dataItem.uid: root.bindPrefix + "/Mode"
				preferredVisible: dataItem.valid
				optionModel: [
					{ display: CommonWords.off, value: 4, readOnly: true },
					{ display: CommonWords.standby, value: 0xfc },
					{ display: CommonWords.on, value: 3 },
				]
			}

			ListText {
				text: CommonWords.error
				dataItem.uid: root.bindPrefix + "/ErrorCode"
				preferredVisible: dataItem.valid
				secondaryText: BmsError.description(dataItem.value)
			}

			ListText {
				//% "Battery bank error"
				text: qsTrId("battery_bank_error")
				dataItem.uid: root.bindPrefix + "/ErrorCode"
				preferredVisible: errorComm.valid || errorVoltage.valid || errorNrOfBatteries.valid || errorInvalidConfig.valid
				secondaryText: {
					if (errorComm.valid && errorComm.value) {
						//% "Communication error"
						return qsTrId("battery_bank_error_communication")
					} else if (errorVoltage.valid && errorVoltage.value) {
						//% "Battery voltage not supported"
						return qsTrId("battery_bank_error_voltage_not_supported")
					} else if (errorNrOfBatteries.valid && errorNrOfBatteries.value) {
						//% "Incorrect number of batteries"
						return qsTrId("battery_bank_error_incorrect_number_of_batteries")
					} else if (errorInvalidConfig.valid && errorInvalidConfig.value) {
						//% "Invalid battery configuration"
						return qsTrId("battery_bank_error_invalid_configuration")
					} else {
						return CommonWords.none_errors
					}
				}

				VeQuickItem { id: errorComm; uid: root.bindPrefix + "/Errors/SmartLithium/Communication" }
				VeQuickItem { id: errorVoltage; uid: root.bindPrefix + "/Errors/SmartLithium/Voltage" }
				VeQuickItem { id: errorNrOfBatteries; uid: root.bindPrefix + "/Errors/SmartLithium/NrOfBatteries" }
				VeQuickItem { id: errorInvalidConfig; uid: root.bindPrefix + "/Errors/SmartLithium/InvalidConfiguration" }
			}

			ListItem {
				id: cellOverviewItem
				// "Overview"
				text: qsTrId("nav_overview")
				preferredVisible: root.isBatteryBank
				content.children: [
					Row {
						id: contentRowOverview

						readonly property real itemWidth: (width - (spacing * 6)) / 7

						width: cellOverviewItem.maximumContentWidth
						spacing: Theme.geometry_listItem_content_spacing

						Column {
							width: contentRowOverview.itemWidth

							QuantityLabel {
								width: parent.width
								value: batteryCurrent.value ?? NaN
								unit: VenusOS.Units_Amp
								precision: 3
								font.pixelSize: 22
							}

							Label {
								width: parent.width
								horizontalAlignment: Text.AlignHCenter
								// "Current"
								text: CommonWords.current_amps
								color: Theme.color_font_secondary
								font.pixelSize: Theme.font_size_caption
							}
						}
						Column {
							width: contentRowOverview.itemWidth

							QuantityLabel {
								width: parent.width
								value: cellSumItem.value ?? batteryVoltage.value ?? NaN
								unit: VenusOS.Units_Volt_DC
								precision: 2
								font.pixelSize: 22
							}

							Label {
								width: parent.width
								horizontalAlignment: Text.AlignHCenter
								// "Voltage"
								text: CommonWords.voltage
								color: Theme.color_font_secondary
								font.pixelSize: Theme.font_size_caption
							}
						}
						Column {
							width: contentRowOverview.itemWidth

							QuantityLabel {
								width: parent.width
								value: batteryPower.value ?? NaN
								unit: VenusOS.Units_Watt
								precision: 0
								font.pixelSize: 22
							}

							Label {
								width: parent.width
								horizontalAlignment: Text.AlignHCenter
								// "Power"
								text: CommonWords.power_watts
								color: Theme.color_font_secondary
								font.pixelSize: Theme.font_size_caption
							}
						}
						Column {
							width: contentRowOverview.itemWidth

							QuantityLabel {
								width: parent.width
								value: cellMaxItem.value ?? NaN
								unit: VenusOS.Units_Volt_DC
								precision: 3
								font.pixelSize: 22
							}

							Label {
								width: parent.width
								horizontalAlignment: Text.AlignHCenter
								//% "Cell max"
								text: qsTrId("dbus_serialbattery_general_cell_max")
								color: Theme.color_font_secondary
								font.pixelSize: Theme.font_size_caption
							}
						}
						Column {
							width: contentRowOverview.itemWidth

							QuantityLabel {
								width: parent.width
								value: cellMinItem.value ?? NaN
								unit: VenusOS.Units_Volt_DC
								precision: 3
								font.pixelSize: 22
							}

							Label {
								width: parent.width
								horizontalAlignment: Text.AlignHCenter
								//% "Cell min"
								text: qsTrId("dbus_serialbattery_general_cell_min")
								color: Theme.color_font_secondary
								font.pixelSize: Theme.font_size_caption
							}
						}
						Column {
							width: contentRowOverview.itemWidth

							QuantityLabel {
								width: parent.width
								value: socItem.value ?? NaN
								unit: VenusOS.Units_Percentage
								precision: 2
								font.pixelSize: 22
							}

							Label {
								width: parent.width
								horizontalAlignment: Text.AlignHCenter
								//% "SoC"
								text: qsTrId("dbus_serialbattery_general_soc")
								color: Theme.color_font_secondary
								font.pixelSize: Theme.font_size_caption
							}
						}
						Column {
							width: contentRowOverview.itemWidth

							QuantityLabel {
								width: parent.width
								value: consumedAhItem.value ?? NaN
								unit: VenusOS.Units_AmpHour
								precision: 2
								font.pixelSize: 22
							}

							Label {
								width: parent.width
								horizontalAlignment: Text.AlignHCenter
								text: "Consumed Ah"
								color: Theme.color_font_secondary
								font.pixelSize: Theme.font_size_caption
							}
						}
					}
				]
			}

			ListItem {
				id: temperaturesOverviewItem
				//% "Temperatures"
				text: qsTrId("dbus_serialbattery_general_temperatures")
				preferredVisible: root.isBatteryBank
				content.children: [
					Row {
						id: temperaturesContentRowOverview

						readonly property real itemWidth: (width - (spacing * 6)) / 7

						width: temperaturesOverviewItem.maximumContentWidth
						spacing: Theme.geometry_listItem_content_spacing

						Column {
							width: temperaturesContentRowOverview.itemWidth

							QuantityLabel {
								width: parent.width
								value: airTemperatureItem.value ?? NaN
								unit: Global.systemSettings.temperatureUnit
								precision: 1
								valueColor: (chargeLimitationItem.valid && chargeLimitationItem.value.toLowerCase().indexOf("ambient") !== -1)
									|| (dischargeLimitationItem.valid && dischargeLimitationItem.value.toLowerCase().indexOf("ambient") !== -1)
									? "#BF4845" : Theme.color_font_primary
								font.pixelSize: 22
							}

							Label {
								width: parent.width
								horizontalAlignment: Text.AlignHCenter
								text: "Ambient"
								color: Theme.color_font_secondary
								font.pixelSize: Theme.font_size_caption
							}
						}
						Column {
							width: temperaturesContentRowOverview.itemWidth

							QuantityLabel {
								width: parent.width
								value: temperatureMosItem.value ?? NaN
								unit: Global.systemSettings.temperatureUnit
								precision: 1
								valueColor: (chargeLimitationItem.valid && chargeLimitationItem.value.toLowerCase().indexOf("mosfet") !== -1)
									|| (dischargeLimitationItem.valid && dischargeLimitationItem.value.toLowerCase().indexOf("mosfet") !== -1)
									? "#BF4845" : Theme.color_font_primary
								font.pixelSize: 22
							}

							Label {
								width: parent.width
								horizontalAlignment: Text.AlignHCenter
								//% "MOSFET"
								text: qsTrId("dbus_serialbattery_general_mosfet")
								color: Theme.color_font_secondary
								font.pixelSize: Theme.font_size_caption
							}
						}
						Column {
							width: temperaturesContentRowOverview.itemWidth

							QuantityLabel {
								width: parent.width
								value: temperatureItem.value ?? NaN
								unit: Global.systemSettings.temperatureUnit
								precision: 1
								valueColor: (chargeLimitationItem.valid && chargeLimitationItem.value.toLowerCase().indexOf("cell temperature") !== -1)
									|| (dischargeLimitationItem.valid && dischargeLimitationItem.value.toLowerCase().indexOf("cell temperature") !== -1)
									? "#BF4845" : Theme.color_font_primary
								font.pixelSize: 22
							}

							Label {
								width: parent.width
								horizontalAlignment: Text.AlignHCenter
								text: "Cell avg"
								color: Theme.color_font_secondary
								font.pixelSize: Theme.font_size_caption
							}
						}
						Column {
							width: temperaturesContentRowOverview.itemWidth

							QuantityLabel {
								width: parent.width
								value: temperature1Item.value ?? NaN
								unit: Global.systemSettings.temperatureUnit
								precision: 1
								valueColor: Theme.color_font_primary
								font.pixelSize: 22
							}

							Label {
								width: parent.width
								horizontalAlignment: Text.AlignHCenter
								//% "Temp 1"
								text: temperature1NameItem.value ?? qsTrId("dbus_serialbattery_general_temp1")
								color: Theme.color_font_secondary
								font.pixelSize: Theme.font_size_caption
							}
						}
						Column {
							width: temperaturesContentRowOverview.itemWidth

							QuantityLabel {
								width: parent.width
								value: temperature2Item.value ?? NaN
								unit: Global.systemSettings.temperatureUnit
								precision: 1
								valueColor: Theme.color_font_primary
								font.pixelSize: 22
							}

							Label {
								width: parent.width
								horizontalAlignment: Text.AlignHCenter
								//% "Temp 2"
								text: temperature2NameItem.value ?? qsTrId("dbus_serialbattery_general_temp2")
								color: Theme.color_font_secondary
								font.pixelSize: Theme.font_size_caption
							}
						}
						Column {
							width: temperaturesContentRowOverview.itemWidth

							QuantityLabel {
								width: parent.width
								value: temperature3Item.value ?? NaN
								unit: Global.systemSettings.temperatureUnit
								precision: 1
								valueColor: Theme.color_font_primary
								font.pixelSize: 22
							}

							Label {
								width: parent.width
								horizontalAlignment: Text.AlignHCenter
								//% "Temp 3"
								text: temperature3NameItem.value ?? qsTrId("dbus_serialbattery_general_temp3")
								color: Theme.color_font_secondary
								font.pixelSize: Theme.font_size_caption
							}
						}
						Column {
							width: temperaturesContentRowOverview.itemWidth

							QuantityLabel {
								width: parent.width
								value: temperature4Item.value ?? NaN
								unit: Global.systemSettings.temperatureUnit
								precision: 1
								valueColor: Theme.color_font_primary
								font.pixelSize: 22
							}

							Label {
								width: parent.width
								horizontalAlignment: Text.AlignHCenter
								//% "Temp 4"
								text: temperature4NameItem.value ?? qsTrId("dbus_serialbattery_general_temp4")
								color: Theme.color_font_secondary
								font.pixelSize: Theme.font_size_caption
							}
						}
					}
				]
			}

			ListQuantityGroup {
				text: CommonWords.battery
				preferredVisible: !root.isBatteryBank
				model: QuantityObjectModel {
					QuantityObject { object: batteryVoltage; unit: VenusOS.Units_Volt_DC }
					QuantityObject { object: batteryCurrent; unit: VenusOS.Units_Amp }
					QuantityObject { object: batteryPower; unit: VenusOS.Units_Watt }
				}

				VeQuickItem {
					id: batteryVoltage
					uid: root.bindPrefix + "/Dc/0/Voltage"
				}

				VeQuickItem {
					id: batteryCurrent
					uid: root.bindPrefix + "/Dc/0/Current"
				}

				VeQuickItem {
					id: batteryPower
					uid: root.bindPrefix + "/Dc/0/Power"
				}
			}

			ListQuantity {
				//% "Total Capacity"
				text: qsTrId("devicelist_battery_total_capacity")
				dataItem.uid: root.bindPrefix + "/Capacity"
				preferredVisible: root.isParallelBms
				unit: VenusOS.Units_AmpHour
			}

			ListQuantity {
				readonly property VeQuickItem _n2kDeviceInstance: VeQuickItem {
					uid: root.bindPrefix + "/N2kDeviceInstance"
				}

				//% "System voltage"
				text: qsTrId("devicelist_battery_system_voltage")
				dataItem.uid: BackendConnection.serviceUidFromName("com.victronenergy.battery.lynxparallel" + _n2kDeviceInstance.value, _n2kDeviceInstance.value) + "/Dc/0/Voltage"
				preferredVisible: !root.isParallelBms && batteryState.value === VenusOS.Battery_State_Pending
				unit: VenusOS.Units_Volt_DC

				VeQuickItem {
					id: batteryState
					uid: root.bindPrefix + "/State"
				}
			}

			ListText {
				id: nrOfBmses
				//% "Number of BMSes"
				text: qsTrId("devicelist_battery_number_of_bmses")
				dataItem.uid: root.bindPrefix + "/NumberOfBmses"
				preferredVisible: root.isParallelBms
			}

			ListQuantity {
				// For the battery bank this duplicates the Overview row's SoC tile.
				text: CommonWords.state_of_charge
				dataItem.uid: root.bindPrefix + "/Soc"
				preferredVisible: !root.isBatteryBank
				unit: VenusOS.Units_Percentage
			}

			ListQuantity {
				//% "State of health"
				text: qsTrId("battery_state_of_health")
				dataItem.uid: root.bindPrefix + "/Soh"
				preferredVisible: dataItem.valid
				unit: VenusOS.Units_Percentage
			}

			ListTemperature {
				// For the battery bank this duplicates the Temperatures row's "Battery" tile.
				text: CommonWords.battery_temperature
				dataItem.uid: root.bindPrefix + "/Dc/0/Temperature"
				preferredVisible: !root.isBatteryBank && dataItem.valid
				unit: Global.systemSettings.temperatureUnit
			}

			ListTemperature {
				// For the battery bank this duplicates the Temperatures row's Ambient tile.
				//% "Air temperature"
				text: qsTrId("battery_air_temp")
				dataItem.uid: root.bindPrefix + "/AirTemperature"
				preferredVisible: !root.isBatteryBank && dataItem.valid
			}

			ListQuantity {
				//% "Bus voltage"
				text: qsTrId("battery_bus_voltage")
				dataItem.uid: root.bindPrefix + "/BusVoltage"
				preferredVisible: dataItem.valid
				unit: VenusOS.Units_Volt_DC
			}

			ListQuantity {
				//% "Top section voltage"
				text: qsTrId("battery_top_section_voltage")
				preferredVisible: midVoltage.valid
				value: midVoltage.valid && batteryVoltage.valid ? batteryVoltage.value - midVoltage.value : NaN
				unit: VenusOS.Units_Volt_DC
			}

			ListQuantity {
				//% "Bottom section voltage"
				text: qsTrId("battery_bottom_section_voltage")
				value: midVoltage.value === undefined ? NaN : midVoltage.value
				preferredVisible: midVoltage.valid
				unit: VenusOS.Units_Volt_DC
			}

			ListQuantity {
				// For the battery bank this duplicates the Overview row's Consumed Ah tile.
				//% "Consumed AmpHours"
				text: qsTrId("battery_consumed_amphours")
				dataItem.uid: root.bindPrefix + "/ConsumedAmphours"
				preferredVisible: !root.isBatteryBank && dataItem.valid
				unit: VenusOS.Units_AmpHour
			}

			ListQuantity {
				//% "Bus voltage"
				text: qsTrId("battery_buss_voltage")
				dataItem.uid: root.bindPrefix + "/BussVoltage"
				preferredVisible: dataItem.valid
				unit: VenusOS.Units_Volt_DC
			}

			ListRelayState {
				dataItem.uid: root.bindPrefix + "/Relay/0/State"
			}

			ListAlarmState {
				dataItem.uid: root.bindPrefix + "/Alarms/Alarm"
			}

			ListText {
				text: CommonWords.state
				dataItem.uid: root.bindPrefix + "/State"
				preferredVisible: dataItem.valid
				secondaryText: {
					if (!dataItem.valid) {
						return ""
					}
					if (dataItem.value >= 0 && dataItem.value <= 8) {
						//% "Initializing"
						return qsTrId("devicelist_battery_initializing")
					}
					switch (dataItem.value) {
					case VenusOS.Battery_State_Running:
						return CommonWords.running_status
					case VenusOS.Battery_State_Error:
						return CommonWords.error
					// case Battery_State_Unknown is omitted
					case VenusOS.Battery_State_Shutdown:
						//: Status is 'Shutdown'
						//% "Shutdown"
						return qsTrId("devicelist_battery_shutdown")
					case VenusOS.Battery_State_Updating:
						//: Status is 'Updating'
						//% "Updating"
						return qsTrId("devicelist_battery_updating")
					case VenusOS.Battery_State_Standby:
						return CommonWords.standby
					case VenusOS.Battery_State_GoingToRun:
						//: Status is 'Going to run'
						//% "Going to run"
						return qsTrId("devicelist_battery_going_to_run")
					case VenusOS.Battery_State_Precharging:
						//: Status is 'Pre-Charging'
						//% "Pre-Charging"
						return qsTrId("devicelist_battery_pre_charging")
					case VenusOS.Battery_State_ContactorCheck:
						//: Status is 'Contactor check'
						//% "Contactor check"
						return qsTrId("devicelist_battery_contactor_check")
					case VenusOS.Battery_State_Pending:
						return CommonWords.pending
					default:
						return ""
					}
				}
			}

			ListText {
				//% "Charge Mode"
				text: qsTrId("dbus_serialbattery_general_charge_mode")
				secondaryText: chargeModeItem.valid ? chargeModeItem.value : "--"
				preferredVisible: root.isBatteryBank && chargeModeItem.valid
			}

			ListQuantityGroup {
				// "Charge Voltage Limit (CVL)"
				text: qsTrId("batteryparameters_charge_voltage_limit_cvl")
				preferredVisible: root.isBatteryBank && maxChargeVoltageItem.valid
				model: QuantityObjectModel {
					QuantityObject { object: maxChargeVoltageItem; unit: VenusOS.Units_Volt_DC }
				}

				VeQuickItem {
					id: maxChargeVoltageItem
					uid: root.bindPrefix + "/Info/MaxChargeVoltage"
				}
			}

			ListQuantityGroup {
				// "Charge Current Limit (CCL)"
				text: qsTrId("batteryparameters_charge_current_limit_ccl")
				preferredVisible: root.isBatteryBank && maxChargeCurrentItem.valid
				model: QuantityObjectModel {
					QuantityObject { object: chargeLimitationItem; defaultValue: "--" }
					QuantityObject { object: maxChargeCurrentItem; unit: VenusOS.Units_Amp }
				}

				VeQuickItem {
					id: chargeLimitationItem
					uid: root.bindPrefix + "/Info/ChargeLimitation"
				}

				VeQuickItem {
					id: maxChargeCurrentItem
					uid: root.bindPrefix + "/Info/MaxChargeCurrent"
				}
			}

			ListQuantityGroup {
				// "Discharge Current Limit (DCL)"
				text: qsTrId("batteryparameters_discharge_current_limit_dcl")
				preferredVisible: root.isBatteryBank && maxDischargeCurrentItem.valid
				model: QuantityObjectModel {
					QuantityObject { object: dischargeLimitationItem; defaultValue: "--" }
					QuantityObject { object: maxDischargeCurrentItem; unit: VenusOS.Units_Amp }
				}

				VeQuickItem {
					id: dischargeLimitationItem
					uid: root.bindPrefix + "/Info/DischargeLimitation"
				}

				VeQuickItem {
					id: maxDischargeCurrentItem
					uid: root.bindPrefix + "/Info/MaxDischargeCurrent"
				}
			}

			ListQuantityGroup {
				//% "Installed / Available capacity"
				text: qsTrId("batterydetails_installed_available_capacity")
				preferredVisible: root.isBatteryBank && (installedCapacityItem.valid || availableCapacityItem.valid)
				model: QuantityObjectModel {
					QuantityObject { object: installedCapacityItem; unit: VenusOS.Units_AmpHour }
					QuantityObject { object: availableCapacityItem; unit: VenusOS.Units_AmpHour }
				}

				VeQuickItem {
					id: installedCapacityItem
					uid: root.bindPrefix + "/InstalledCapacity"
				}

				VeQuickItem {
					id: availableCapacityItem
					uid: root.bindPrefix + "/Capacity"
				}
			}

			ListText {
				//% "Time-to-go"
				text: qsTrId("battery_time_to_go")
				dataItem.uid: root.bindPrefix + "/TimeToGo"
				preferredVisible: dataItem.seen
				secondaryText: Utils.secondsToString(dataItem.value)
			}

			ListItem {
				id: allowToOverviewItem
				//% "Allow to"
				text: qsTrId("dbus_serialbattery_general_allow_to")
				preferredVisible: root.isBatteryBank && (allowToChargeItem.valid || allowToDischargeItem.valid)
				content.children: [
					Row {
						id: allowToContentRowOverview

						readonly property real itemWidth: (width - spacing) / 2

						width: allowToOverviewItem.maximumContentWidth
						spacing: Theme.geometry_listItem_content_spacing

						Column {
							width: allowToContentRowOverview.itemWidth

							QuantityLabel {
								width: parent.width
								valueText: allowToChargeItem.valid ? CommonWords.yesOrNo(allowToChargeItem.value) : "--"
								valueColor: allowToChargeItem.value === 0 ? "#BF4845" : Theme.color_font_primary
								font.pixelSize: 22
							}

							Label {
								width: parent.width
								horizontalAlignment: Text.AlignHCenter
								//: Allow to ...
								//% "Charge"
								text: qsTrId("dbus_serialbattery_general_charge")
								color: Theme.color_font_secondary
								font.pixelSize: Theme.font_size_caption
							}
						}
						Column {
							width: allowToContentRowOverview.itemWidth

							QuantityLabel {
								width: parent.width
								valueText: allowToDischargeItem.valid ? CommonWords.yesOrNo(allowToDischargeItem.value) : "--"
								valueColor: allowToDischargeItem.value === 0 ? "#BF4845" : Theme.color_font_primary
								font.pixelSize: 22
							}

							Label {
								width: parent.width
								horizontalAlignment: Text.AlignHCenter
								//: Allow to ...
								//% "Discharge"
								text: qsTrId("dbus_serialbattery_general_discharge")
								color: Theme.color_font_secondary
								font.pixelSize: Theme.font_size_caption
							}
						}
					}
				]
			}

			ListQuantity {
				// The dbus-battery-bank aggregate repurposes this path for VRM logging of the
				// PTC overheat-detection chain voltage (times 10 for resolution).
				//% "Starter voltage"
				text: productId.value === 0xBA44 ? "PTC voltage ×10" : qsTrId("battery_starter_voltage")
				dataItem.uid: root.bindPrefix + "/Dc/1/Voltage"
				preferredVisible: dataItem.valid
				unit: VenusOS.Units_Volt_DC
			}

			ListQuantity {
				// The dbus-battery-bank aggregate repurposes this path for VRM logging of the
				// PTC voltage deviation from the temperature-based expectation.
				//% "Mid-point deviation"
				text: productId.value === 0xBA44 ? "PTC deviation" : qsTrId("battery_mid_point_deviation")
				dataItem.uid: root.bindPrefix + "/Dc/0/MidVoltageDeviation"
				preferredVisible: dataItem.valid
				unit: VenusOS.Units_Percentage
			}

			ListText {
				// "Alarms"
				text: CommonWords.alarms
				secondaryText: getActiveAlarmsText()
				secondaryLabel.color: Theme.color_red
				preferredVisible: root.isBatteryBank && secondaryLabel.text !== ""
			}

			ListNavigation {
				text: "Cell Voltages"
				preferredVisible: cell3Voltage.valid
				onClicked: {
					Global.pageManager.pushPage("/pages/settings/devicelist/battery/PageBatteryCellVoltages.qml",
							{ "title": text, "bindPrefix": root.bindPrefix })
				}

				VeQuickItem {
					id: cell3Voltage
					uid: root.bindPrefix + "/Voltages/Cell3"
				}
			}

			ListNavigation {
				text: "Debug"
				preferredVisible: chargeModeDebugItem.valid && chargeModeDebugItem.value !== ""
				onClicked: {
					Global.pageManager.pushPage("/pages/settings/devicelist/battery/PageBatteryBankDebug.qml",
							{ "title": text, "bindPrefix": root.bindPrefix })
				}

				VeQuickItem {
					id: chargeModeDebugItem
					uid: root.bindPrefix + "/Info/ChargeModeDebug"
				}
			}

			ListNavigation {
				text: "Time to SoC"
				preferredVisible: timeToSoc0.seen ||
						timeToSoc5.seen ||
						timeToSoc10.seen ||
						timeToSoc15.seen ||
						timeToSoc20.seen ||
						timeToSoc80.seen ||
						timeToSoc85.seen ||
						timeToSoc90.seen ||
						timeToSoc95.seen ||
						timeToSoc100.seen
				onClicked: {
					Global.pageManager.pushPage("/pages/settings/devicelist/battery/PageBatteryTimeToSoc.qml",
							{ "title": text, "bindPrefix": root.bindPrefix })
				}

				VeQuickItem {
					id: timeToSoc0
					uid: root.bindPrefix + "/TimeToSoC/0"
				}
				VeQuickItem {
					id: timeToSoc5
					uid: root.bindPrefix + "/TimeToSoC/5"
				}
				VeQuickItem {
					id: timeToSoc10
					uid: root.bindPrefix + "/TimeToSoC/10"
				}
				VeQuickItem {
					id: timeToSoc15
					uid: root.bindPrefix + "/TimeToSoC/15"
				}
				VeQuickItem {
					id: timeToSoc20
					uid: root.bindPrefix + "/TimeToSoC/20"
				}
				VeQuickItem {
					id: timeToSoc80
					uid: root.bindPrefix + "/TimeToSoC/80"
				}
				VeQuickItem {
					id: timeToSoc85
					uid: root.bindPrefix + "/TimeToSoC/85"
				}
				VeQuickItem {
					id: timeToSoc90
					uid: root.bindPrefix + "/TimeToSoC/90"
				}
				VeQuickItem {
					id: timeToSoc95
					uid: root.bindPrefix + "/TimeToSoC/95"
				}
				VeQuickItem {
					id: timeToSoc100
					uid: root.bindPrefix + "/TimeToSoC/100"
				}
			}

			ListNavigation {
				//% "Details"
				text: qsTrId("battery_details")
				// For a pack this only duplicates the main page and cell pages.
				preferredVisible: batteryDetails.hasAllowedItem && !root.isBatteryBankPack
				onClicked: {
					Global.pageManager.pushPage("/pages/settings/devicelist/battery/PageBatteryDetails.qml",
							{ "title": text, "bindPrefix": root.bindPrefix, "details": batteryDetails })
				}

				BatteryDetails {
					id: batteryDetails
					bindPrefix: root.bindPrefix
				}
			}

			ListNavigation {
				text: CommonWords.alarms
				preferredVisible: !root.isParallelBms
				onClicked: {
					Global.pageManager.pushPage("/pages/settings/devicelist/battery/PageBatteryAlarms.qml",
							{ "title": text, "bindPrefix": root.bindPrefix })
				}
			}

			ListNavigation {
				//% "Module level alarms"
				text: qsTrId("battery_module_level_alarms")
				preferredVisible: moduleAlarmModel.rowCount > 0
				onClicked: {
					Global.pageManager.pushPage("/pages/settings/devicelist/battery/PageBatteryModuleAlarms.qml",
							{ "title": text, "bindPrefix": root.bindPrefix, alarmModel: moduleAlarmModel })
				}
			}

			ListNavigation {
				text: CommonWords.history
				preferredVisible: !isFiamm48TL && batteryHistory.hasAllowedItem
				onClicked: {
					Global.pageManager.pushPage("/pages/settings/devicelist/battery/PageBatteryHistory.qml",
							{ "title": text, "bindPrefix": root.bindPrefix, "history": batteryHistory })
				}

				BatteryHistory {
					id: batteryHistory
					bindPrefix: root.bindPrefix
				}
			}

			ListNavigation {
				text: CommonWords.settings
				preferredVisible: hasSettings.value === 1
				onClicked: {
					Global.pageManager.pushPage("/pages/settings/devicelist/battery/PageBatterySettings.qml",
							{ "title": text, "bindPrefix": root.bindPrefix })
				}
			}

			ListNavigation {
				id: lynxIonDiagnostics

				//% "Diagnostics"
				text: qsTrId("battery_settings_diagnostics")
				preferredVisible: lastError.valid
				onClicked: {
					Global.pageManager.pushPage("/pages/settings/devicelist/battery/PageLynxIonDiagnostics.qml",
							{ "title": text, "bindPrefix": root.bindPrefix })
				}

				VeQuickItem {
					id: lastError
					uid: root.bindPrefix + "/Diagnostics/LastErrors/1/Error"
				}
			}

			ListNavigation {
				text: lynxIonDiagnostics.text
				preferredVisible: isFiamm48TL

				onClicked: {
					Global.pageManager.pushPage("/pages/settings/devicelist/battery/Page48TlDiagnostics.qml",
							{ "title": text, "bindPrefix": root.bindPrefix })
				}
			}

			ListNavigation {
				//% "Fuses"
				text: qsTrId("battery_settings_fuses")
				preferredVisible: nrOfDistributors.valid && nrOfDistributors.value > 0

				onClicked: {
					Global.pageManager.pushPage("/pages/settings/devicelist/battery/PageLynxDistributorList.qml",
							{ "title": text, "bindPrefix": root.bindPrefix })
				}

				VeQuickItem {
					id: nrOfDistributors
					uid: root.bindPrefix + "/NrOfDistributors"
				}
			}

			ListNavigation {
				//% "IO"
				text: qsTrId("battery_settings_io")
				// For the battery bank this only duplicates the "Allow to" row.
				preferredVisible: allowToCharge.valid && !root.isBatteryBank
				onClicked: {
					Global.pageManager.pushPage("/pages/settings/devicelist/battery/PageLynxIonIo.qml",
							{ "title": text, "bindPrefix": root.bindPrefix })
				}

				VeQuickItem {
					id: allowToCharge
					uid: root.bindPrefix + "/Io/AllowToCharge"
				}
			}

			ListNavigation {
				//% "System"
				text: qsTrId("battery_settings_system")
				preferredVisible: nrOfBatteries.valid
				onClicked: {
					Global.pageManager.pushPage("/pages/settings/devicelist/battery/PageLynxIonSystem.qml",
							{ "title": text, "bindPrefix": root.bindPrefix })
				}

				VeQuickItem {
					id: nrOfBatteries
					uid: root.bindPrefix +"/System/NrOfBatteries"
				}
			}

			ListNavigation {
				text: CommonWords.device_info_title
				onClicked: {
					Global.pageManager.pushPage("/pages/settings/PageDeviceInfo.qml",
							{ "title": text, "bindPrefix": root.bindPrefix })
				}
			}

			ListNavigation {
				//% "Parameters"
				text: qsTrId("battery_settings_parameters")
				// For the battery bank this only duplicates the CVL/CCL/DCL rows.
				preferredVisible: (cvl.valid || ccl.valid || dcl.valid) && !root.isBatteryBank
				onClicked: {
					Global.pageManager.pushPage("/pages/settings/devicelist/battery/PageBatteryParameters.qml",
							{ "title": text, "bindPrefix": root.bindPrefix })
				}

				VeQuickItem {
					id: cvl
					uid: root.bindPrefix + "/Info/MaxChargeVoltage"
				}

				VeQuickItem {
					id: ccl
					uid: root.bindPrefix + "/Info/MaxChargeCurrent"
				}

				VeQuickItem {
					id: dcl
					uid: root.bindPrefix + "/Info/MaxDischargeCurrent"
				}
			}

			ListButton {
				//% "Redetect Battery"
				text: qsTrId("battery_redetect_battery")
				//% "Press to redetect"
				secondaryText: qsTrId("battery_press_to_redetect")
				interactive: redetect.value === 0
				preferredVisible: redetect.valid
				writeAccessLevel: VenusOS.User_AccessType_User
				onClicked: {
					redetect.setValue(1)
					//% "Redetecting the battery may take up time 60 seconds. Meanwhile the name of the battery may be incorrect."
					Global.showToastNotification(VenusOS.Notification_Info, qsTrId("battery_redetecting_the_battery_note"), 10000)
				}

				VeQuickItem {
					id: redetect
					uid: root.bindPrefix + "/Redetect"
				}
			}
		}
	}

	VeQuickItem {
		id: midVoltage
		uid: root.bindPrefix + "/Dc/0/MidVoltage"
	}

	VeQuickItem {
		id: productId
		uid: root.bindPrefix + "/ProductId"
	}

	VeQuickItem {
		id: hasSettings
		uid: root.bindPrefix + "/Settings/HasSettings"
	}

	VeQuickItem {
		id: cellSumItem
		uid: root.bindPrefix + "/Voltages/Sum"
	}
	VeQuickItem {
		id: cellMinItem
		uid: root.bindPrefix + "/System/MinCellVoltage"
	}
	VeQuickItem {
		id: cellMaxItem
		uid: root.bindPrefix + "/System/MaxCellVoltage"
	}
	VeQuickItem {
		id: socItem
		uid: root.bindPrefix + "/Soc"
	}
	VeQuickItem {
		id: consumedAhItem
		uid: root.bindPrefix + "/ConsumedAmphours"
	}
	VeQuickItem {
		id: chargeModeItem
		uid: root.bindPrefix + "/Info/ChargeMode"
	}
	VeQuickItem {
		id: airTemperatureItem
		uid: root.bindPrefix + "/AirTemperature"
		sourceUnit: Units.unitToVeUnit(VenusOS.Units_Temperature_Celsius)
		displayUnit: Units.unitToVeUnit(Global.systemSettings.temperatureUnit)
	}

	VeQuickItem {
		id: temperatureItem
		uid: root.bindPrefix + "/Dc/0/Temperature"
		sourceUnit: Units.unitToVeUnit(VenusOS.Units_Temperature_Celsius)
		displayUnit: Units.unitToVeUnit(Global.systemSettings.temperatureUnit)
	}
	VeQuickItem {
		id: temperatureMosItem
		uid: root.bindPrefix + "/System/MOSTemperature"
		sourceUnit: Units.unitToVeUnit(VenusOS.Units_Temperature_Celsius)
		displayUnit: Units.unitToVeUnit(Global.systemSettings.temperatureUnit)
	}
	VeQuickItem {
		id: temperature1Item
		uid: root.bindPrefix + "/System/Temperature1"
		sourceUnit: Units.unitToVeUnit(VenusOS.Units_Temperature_Celsius)
		displayUnit: Units.unitToVeUnit(Global.systemSettings.temperatureUnit)
	}
	VeQuickItem {
		id: temperature1NameItem
		uid: root.bindPrefix + "/System/Temperature1Name"
	}
	VeQuickItem {
		id: temperature2Item
		uid: root.bindPrefix + "/System/Temperature2"
		sourceUnit: Units.unitToVeUnit(VenusOS.Units_Temperature_Celsius)
		displayUnit: Units.unitToVeUnit(Global.systemSettings.temperatureUnit)
	}
	VeQuickItem {
		id: temperature2NameItem
		uid: root.bindPrefix + "/System/Temperature2Name"
	}
	VeQuickItem {
		id: temperature3Item
		uid: root.bindPrefix + "/System/Temperature3"
		sourceUnit: Units.unitToVeUnit(VenusOS.Units_Temperature_Celsius)
		displayUnit: Units.unitToVeUnit(Global.systemSettings.temperatureUnit)
	}
	VeQuickItem {
		id: temperature3NameItem
		uid: root.bindPrefix + "/System/Temperature3Name"
	}
	VeQuickItem {
		id: temperature4Item
		uid: root.bindPrefix + "/System/Temperature4"
		sourceUnit: Units.unitToVeUnit(VenusOS.Units_Temperature_Celsius)
		displayUnit: Units.unitToVeUnit(Global.systemSettings.temperatureUnit)
	}
	VeQuickItem {
		id: temperature4NameItem
		uid: root.bindPrefix + "/System/Temperature4Name"
	}

	VeQuickItem {
		id: allowToChargeItem
		uid: root.bindPrefix + "/Io/AllowToCharge"
	}
	VeQuickItem {
		id: allowToDischargeItem
		uid: root.bindPrefix + "/Io/AllowToDischarge"
	}

	VeQuickItem {
		id: alarmLowBatteryVoltage
		uid: root.bindPrefix + "/Alarms/LowVoltage"
	}
	VeQuickItem {
		id: alarmHighBatteryVoltage
		uid: root.bindPrefix + "/Alarms/HighVoltage"
	}
	VeQuickItem {
		id: alarmHighCellVoltage
		uid: root.bindPrefix + "/Alarms/HighCellVoltage"
	}
	VeQuickItem {
		id: alarmHighChargeCurrent
		uid: root.bindPrefix + "/Alarms/HighChargeCurrent"
	}
	VeQuickItem {
		id: alarmHighCurrent
		uid: root.bindPrefix + "/Alarms/HighCurrent"
	}
	VeQuickItem {
		id: alarmHighDischargeCurrent
		uid: root.bindPrefix + "/Alarms/HighDischargeCurrent"
	}
	VeQuickItem {
		id: alarmLowSoc
		uid: root.bindPrefix + "/Alarms/LowSoc"
	}
	VeQuickItem {
		id: alarmStateOfHealth
		uid: root.bindPrefix + "/Alarms/StateOfHealth"
	}
	VeQuickItem {
		id: alarmLowStarterVoltage
		uid: root.bindPrefix + "/Alarms/LowStarterVoltage"
	}
	VeQuickItem {
		id: alarmHighStarterVoltage
		uid: root.bindPrefix + "/Alarms/HighStarterVoltage"
	}
	VeQuickItem {
		id: alarmLowTemperature
		uid: root.bindPrefix + "/Alarms/LowTemperature"
	}
	VeQuickItem {
		id: alarmHighTemperature
		uid: root.bindPrefix + "/Alarms/HighTemperature"
	}
	VeQuickItem {
		id: alarmBatteryTemperatureSensor
		uid: root.bindPrefix + "/Alarms/BatteryTemperatureSensor"
	}
	VeQuickItem {
		id: alarmMidPointVoltage
		uid: root.bindPrefix + "/Alarms/MidVoltage"
	}
	VeQuickItem {
		id: alarmFuseBlown
		uid: root.bindPrefix + "/Alarms/FuseBlown"
	}
	VeQuickItem {
		id: alarmHighInternalTemperature
		uid: root.bindPrefix + "/Alarms/HighInternalTemperature"
	}
	VeQuickItem {
		id: alarmLowChargeTemperature
		uid: root.bindPrefix + "/Alarms/LowChargeTemperature"
	}
	VeQuickItem {
		id: alarmHighChargeTemperature
		uid: root.bindPrefix + "/Alarms/HighChargeTemperature"
	}
	VeQuickItem {
		id: alarmInternalFailure
		uid: root.bindPrefix + "/Alarms/InternalFailure"
	}
	VeQuickItem {
		id: alarmCellImbalance
		uid: root.bindPrefix + "/Alarms/CellImbalance"
	}
	VeQuickItem {
		id: alarmLowCellVoltage
		uid: root.bindPrefix + "/Alarms/LowCellVoltage"
	}
	VeQuickItem {
		id: alarmBmsCable
		uid: root.bindPrefix + "/Alarms/BmsCable"
	}
	VeQuickItem {
		id: alarmContactor
		uid: root.bindPrefix + "/Alarms/Contactor"
	}

	VeQItemSortTableModel {
		id: moduleAlarmModel

		filterRegExp: "\/Module[0-9]\/Id$"
		filterFlags: VeQItemSortTableModel.FilterInvalid
		model: VeQItemTableModel {
			uids: [root.bindPrefix + "/Diagnostics"]
		}
	}
}
