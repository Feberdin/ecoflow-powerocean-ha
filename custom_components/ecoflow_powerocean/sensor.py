"""
Sensor-Plattform für die EcoFlow PowerOcean Plus Integration.

Sensorgruppen:

    Batterie-Pack-Sensoren (konfigurierbar, Standard: 2 Packs × 9 Sensoren):
        SOC, SOH, Leistung, Spannung*, Strom*, Energie, Temp, MOSFET-Temp*, Zyklen

    Systemweite Leistungs-Sensoren (primär aus ENERGY_STREAM, Fallback EMS_HEARTBEAT):
        Solar, Netz, Last, Batterie-Gesamt, Gesamt-SOC, Gesamtenergie,
        DC-Bus*, Aktive Module,
        Phase L1/L2/L3: Spannung, Strom, Wirk-, Blind-*, Scheinleistung*,
        Netzfrequenz, MPPT 1–4: Leistung, Spannung*, Strom*

    Energie-Akkumulatoren (kWh, für Energie-Dashboard):
        Solar, Netz-Bezug, Netz-Einspeisung, Batterie-Entnahme, Batterie-Ladung

    Verbindungsstatus: connected / disconnected

    (* = standardmäßig deaktiviert)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfReactivePower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_NUM_BATTERY_PACKS,
    CONF_SERIAL_NUMBER,
    DATA_BATTERIES,
    DATA_ENERGY_STREAM,
    DATA_EMS_HEARTBEAT,
    DEFAULT_NUM_BATTERY_PACKS,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .coordinator import EcoFlowCoordinator
from .proto_decoder import BatteryPackData

_LOGGER = logging.getLogger(__name__)
_LAST_SIGN_CORRECTION_SIGNATURE: tuple[Any, ...] | None = None

# Kleine Netzleistungen liegen oft im Messrauschen und sollten nicht
# per Vorzeichenkorrektur "umkippen".
GRID_SIGN_DEADBAND_W = 20.0
# Ein Flip wird nur akzeptiert, wenn die Bilanz erkennbar besser wird.
MIN_SIGN_FLIP_IMPROVEMENT_W = 20.0


# ── Sensor-Beschreibungen ─────────────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class EcoFlowBatterySensorDescription(SensorEntityDescription):
    """Sensor-Beschreibung für Batterie-Pack-Sensoren."""
    value_fn: Callable[[BatteryPackData], Any] = lambda _: None


@dataclass(frozen=True, kw_only=True)
class EcoFlowSystemSensorDescription(SensorEntityDescription):
    """Sensor-Beschreibung für systemweite Momentwert-Sensoren."""
    data_key: str = ""
    value_fn: Callable[[Any], Any] = lambda _: None
    uses_coordinator_data: bool = False


@dataclass(frozen=True, kw_only=True)
class EcoFlowEnergyAccumulatorDescription(SensorEntityDescription):
    """Sensor-Beschreibung für Energie-Akkumulatoren (kWh)."""
    power_fn: Callable[[dict], float] = lambda _: 0.0


def _ems_grid_power_w(data: dict[str, Any]) -> float:
    """Fallback: Netzleistung aus den drei Phasen (EMS_HEARTBEAT)."""
    ems = data.get(DATA_EMS_HEARTBEAT)
    if ems is None:
        return 0.0
    return float(ems.phase_a.act_pwr + ems.phase_b.act_pwr + ems.phase_c.act_pwr)


def _ems_solar_power_w(data: dict[str, Any]) -> float:
    """Fallback: Solarleistung als Summe aller MPPT-Strings."""
    ems = data.get(DATA_EMS_HEARTBEAT)
    if ems is None:
        return 0.0
    return float(sum(s.power_w for s in ems.mppt_strings))


def _ems_battery_power_w(data: dict[str, Any]) -> float:
    """Fallback: Batterieleistung aus EMS_HEARTBEAT."""
    ems = data.get(DATA_EMS_HEARTBEAT)
    if ems is None:
        return 0.0
    return float(ems.battery_power_w)


def _normalized_power_components(data: dict[str, Any]) -> tuple[float, float, float, float]:
    """
    Liefert normalisierte Leistungswerte als (solar, grid, load, battery).

    Warum:
    Einige Geräte/Firmwarestände liefern unterschiedliche Vorzeichenkonventionen.
    ENERGY_STREAM liefert alle vier Kernwerte (Solar, Netz, Last, Batterie), daher
    können wir Vorzeichen über die physikalische Bilanz robust auflösen:
        load ~= solar + battery + grid
    """
    stream = data.get(DATA_ENERGY_STREAM)
    if stream is None:
        solar = _ems_solar_power_w(data)
        grid = _ems_grid_power_w(data)
        battery = _ems_battery_power_w(data)
        load = solar + battery + grid
        return solar, grid, load, battery

    solar = float(stream.solar_w)
    load = float(stream.load_w)
    grid_raw = float(stream.grid_w)
    battery_raw = float(stream.battery_w)

    # 1) Netz-Vorzeichen so wählen, dass die Leistungsbilanz bestmöglich passt.
    #    Bei sehr kleinen Netzleistungen bleibt das Roh-Vorzeichen unverändert.
    if abs(grid_raw) <= GRID_SIGN_DEADBAND_W:
        grid = grid_raw
        grid_flipped = False
    else:
        err_grid_keep = abs((solar + battery_raw + grid_raw) - load)
        err_grid_flip = abs((solar + battery_raw - grid_raw) - load)
        grid_improvement = err_grid_keep - err_grid_flip
        grid_flipped = (
            err_grid_flip < err_grid_keep
            and grid_improvement >= MIN_SIGN_FLIP_IMPROVEMENT_W
        )
        grid = -grid_raw if grid_flipped else grid_raw

    # 2) Batterie-Vorzeichen so wählen, dass die Bilanz bei gewähltem Netz passt.
    battery_expected = load - solar - grid
    err_batt_keep = abs(battery_raw - battery_expected)
    err_batt_flip = abs((-battery_raw) - battery_expected)
    batt_improvement = err_batt_keep - err_batt_flip
    battery_flipped = (
        err_batt_flip < err_batt_keep
        and batt_improvement >= MIN_SIGN_FLIP_IMPROVEMENT_W
    )
    battery = -battery_raw if battery_flipped else battery_raw

    # Debug-Hilfe: zeigt nur Fälle, in denen ein Vorzeichen aktiv korrigiert wurde.
    if grid_flipped or battery_flipped:
        # Verhindert Log-Spam: nur loggen, wenn sich das Rohwert-Muster geändert hat.
        signature = (
            grid_flipped,
            battery_flipped,
            round(solar, 1),
            round(grid_raw, 1),
            round(battery_raw, 1),
            round(load, 1),
        )
        global _LAST_SIGN_CORRECTION_SIGNATURE
        if signature != _LAST_SIGN_CORRECTION_SIGNATURE:
            _LAST_SIGN_CORRECTION_SIGNATURE = signature
            _LOGGER.debug(
                "ENERGY_STREAM Vorzeichenkorrektur angewendet (grid_flip=%s, battery_flip=%s, "
                "solar=%.1fW, grid_raw=%.1fW, battery_raw=%.1fW, load=%.1fW)",
                grid_flipped,
                battery_flipped,
                solar,
                grid_raw,
                battery_raw,
                load,
            )

    return solar, grid, load, battery


def _solar_power_w(data: dict[str, Any]) -> float:
    """Solarleistung in W (normalisiert)."""
    return _normalized_power_components(data)[0]


def _grid_power_w(data: dict[str, Any]) -> float:
    """Netzleistung in W (normalisiert; +Bezug, -Einspeisung)."""
    return _normalized_power_components(data)[1]


def _load_power_w(data: dict[str, Any]) -> float:
    """Hausverbrauch in W (normalisiert)."""
    return _normalized_power_components(data)[2]


def _battery_power_w(data: dict[str, Any]) -> float:
    """Batterieleistung in W (normalisiert; +Entladen, -Laden)."""
    return _normalized_power_components(data)[3]


def _total_soc(data: dict[str, Any]) -> int | None:
    """
    Gesamt-SOC bevorzugt aus ENERGY_STREAM, fallback auf Batterie-Mittelwert.
    """
    stream = data.get(DATA_ENERGY_STREAM)
    if stream is not None:
        stream_soc = int(getattr(stream, "soc", 0))
        if 0 <= stream_soc <= 100:
            return stream_soc

    batteries = data.get(DATA_BATTERIES, {})
    if not batteries:
        return None
    return int(sum(p.soc for p in batteries.values()) / len(batteries))


# ── Batterie-Pack-Sensoren ────────────────────────────────────────────────────

BATTERY_SENSOR_TYPES: tuple[EcoFlowBatterySensorDescription, ...] = (

    EcoFlowBatterySensorDescription(
        key="soc",
        translation_key="battery_soc",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda p: p.soc,
    ),
    EcoFlowBatterySensorDescription(
        key="soh",
        translation_key="battery_soh",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-heart",
        value_fn=lambda p: p.soh,
    ),
    EcoFlowBatterySensorDescription(
        key="power",
        translation_key="battery_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda p: round(p.power_w, 1),
    ),
    EcoFlowBatterySensorDescription(
        key="voltage",
        translation_key="battery_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda p: round(p.voltage_v, 2),
    ),
    EcoFlowBatterySensorDescription(
        key="current",
        translation_key="battery_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda p: round(p.current_a, 3),
    ),
    EcoFlowBatterySensorDescription(
        key="remaining_energy",
        translation_key="battery_remaining_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda p: round(p.remaining_wh, 0),
    ),
    EcoFlowBatterySensorDescription(
        key="temperature",
        translation_key="battery_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda p: round(p.temperature_env_c, 1),
    ),
    EcoFlowBatterySensorDescription(
        key="temperature_mos",
        translation_key="battery_temperature_mos",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        icon="mdi:thermometer-alert",
        value_fn=lambda p: round(p.temperature_mos_c, 1),
    ),
    EcoFlowBatterySensorDescription(
        key="cycles",
        translation_key="battery_cycles",
        icon="mdi:battery-sync",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda p: p.cycles,
    ),
)


# ── Systemweite Leistungs-Sensoren ────────────────────────────────────────────

ENERGY_STREAM_SENSOR_TYPES: tuple[EcoFlowSystemSensorDescription, ...] = (

    EcoFlowSystemSensorDescription(
        key="solar_power",
        translation_key="solar_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
        uses_coordinator_data=True,
        value_fn=lambda d: round(_solar_power_w(d), 1),
    ),
    EcoFlowSystemSensorDescription(
        key="grid_power",
        translation_key="grid_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower",
        uses_coordinator_data=True,
        value_fn=lambda d: round(_grid_power_w(d), 1),
    ),
    EcoFlowSystemSensorDescription(
        key="load_power",
        translation_key="load_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
        uses_coordinator_data=True,
        value_fn=lambda d: round(_load_power_w(d), 1),
    ),
    EcoFlowSystemSensorDescription(
        key="battery_total_power",
        translation_key="battery_total_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-charging",
        uses_coordinator_data=True,
        value_fn=lambda d: round(_battery_power_w(d), 1),
    ),
    EcoFlowSystemSensorDescription(
        key="total_soc",
        translation_key="total_soc",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        uses_coordinator_data=True,
        value_fn=lambda d: _total_soc(d),
    ),
    EcoFlowSystemSensorDescription(
        key="bp_remain_wh",
        translation_key="bp_remain_wh",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.bp_remain_wh, 0) if d.bp_remain_wh > 0 else None,
    ),
    EcoFlowSystemSensorDescription(
        key="bp_alive_count",
        translation_key="bp_alive_count",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-check",
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: d.bp_alive_num if d.bp_alive_num > 0 else None,
    ),
    EcoFlowSystemSensorDescription(
        key="bus_voltage",
        translation_key="bus_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.bus_volt, 1) if d.bus_volt > 0 else None,
    ),
)


EMS_HEARTBEAT_SENSOR_TYPES: tuple[EcoFlowSystemSensorDescription, ...] = (

    # ── Phase L1 ──────────────────────────────────────────────────────────────
    EcoFlowSystemSensorDescription(
        key="phase_a_voltage",
        translation_key="phase_a_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.phase_a.volt, 1),
    ),
    EcoFlowSystemSensorDescription(
        key="phase_a_current",
        translation_key="phase_a_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.phase_a.amp, 2),
    ),
    EcoFlowSystemSensorDescription(
        key="phase_a_power",
        translation_key="phase_a_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.phase_a.act_pwr, 1),
    ),
    EcoFlowSystemSensorDescription(
        key="phase_a_reactive_power",
        translation_key="phase_a_reactive_power",
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.phase_a.react_pwr, 1),
    ),
    EcoFlowSystemSensorDescription(
        key="phase_a_apparent_power",
        translation_key="phase_a_apparent_power",
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.phase_a.apparent_pwr, 1),
    ),

    # ── Phase L2 ──────────────────────────────────────────────────────────────
    EcoFlowSystemSensorDescription(
        key="phase_b_voltage",
        translation_key="phase_b_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.phase_b.volt, 1),
    ),
    EcoFlowSystemSensorDescription(
        key="phase_b_current",
        translation_key="phase_b_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.phase_b.amp, 2),
    ),
    EcoFlowSystemSensorDescription(
        key="phase_b_power",
        translation_key="phase_b_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.phase_b.act_pwr, 1),
    ),
    EcoFlowSystemSensorDescription(
        key="phase_b_reactive_power",
        translation_key="phase_b_reactive_power",
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.phase_b.react_pwr, 1),
    ),
    EcoFlowSystemSensorDescription(
        key="phase_b_apparent_power",
        translation_key="phase_b_apparent_power",
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.phase_b.apparent_pwr, 1),
    ),

    # ── Phase L3 ──────────────────────────────────────────────────────────────
    EcoFlowSystemSensorDescription(
        key="phase_c_voltage",
        translation_key="phase_c_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.phase_c.volt, 1),
    ),
    EcoFlowSystemSensorDescription(
        key="phase_c_current",
        translation_key="phase_c_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.phase_c.amp, 2),
    ),
    EcoFlowSystemSensorDescription(
        key="phase_c_power",
        translation_key="phase_c_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.phase_c.act_pwr, 1),
    ),
    EcoFlowSystemSensorDescription(
        key="phase_c_reactive_power",
        translation_key="phase_c_reactive_power",
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.phase_c.react_pwr, 1),
    ),
    EcoFlowSystemSensorDescription(
        key="phase_c_apparent_power",
        translation_key="phase_c_apparent_power",
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.phase_c.apparent_pwr, 1),
    ),

    # ── Netz ──────────────────────────────────────────────────────────────────
    EcoFlowSystemSensorDescription(
        key="grid_frequency",
        translation_key="grid_frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.frequency_hz, 2) if d.frequency_hz >= 0.1 else None,
    ),

    # ── MPPT-Strings ──────────────────────────────────────────────────────────
    EcoFlowSystemSensorDescription(
        key="mppt_1_power",
        translation_key="mppt_1_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-panel",
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.mppt_strings[0].power_w, 1) if len(d.mppt_strings) >= 1 else None,
    ),
    EcoFlowSystemSensorDescription(
        key="mppt_1_voltage",
        translation_key="mppt_1_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.mppt_strings[0].volt, 1) if len(d.mppt_strings) >= 1 else None,
    ),
    EcoFlowSystemSensorDescription(
        key="mppt_1_current",
        translation_key="mppt_1_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.mppt_strings[0].amp, 2) if len(d.mppt_strings) >= 1 else None,
    ),
    EcoFlowSystemSensorDescription(
        key="mppt_2_power",
        translation_key="mppt_2_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-panel",
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.mppt_strings[1].power_w, 1) if len(d.mppt_strings) >= 2 else None,
    ),
    EcoFlowSystemSensorDescription(
        key="mppt_2_voltage",
        translation_key="mppt_2_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.mppt_strings[1].volt, 1) if len(d.mppt_strings) >= 2 else None,
    ),
    EcoFlowSystemSensorDescription(
        key="mppt_2_current",
        translation_key="mppt_2_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.mppt_strings[1].amp, 2) if len(d.mppt_strings) >= 2 else None,
    ),
    EcoFlowSystemSensorDescription(
        key="mppt_3_power",
        translation_key="mppt_3_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-panel",
        entity_registry_enabled_default=False,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.mppt_strings[2].power_w, 1) if len(d.mppt_strings) >= 3 else None,
    ),
    EcoFlowSystemSensorDescription(
        key="mppt_3_voltage",
        translation_key="mppt_3_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.mppt_strings[2].volt, 1) if len(d.mppt_strings) >= 3 else None,
    ),
    EcoFlowSystemSensorDescription(
        key="mppt_3_current",
        translation_key="mppt_3_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.mppt_strings[2].amp, 2) if len(d.mppt_strings) >= 3 else None,
    ),
    EcoFlowSystemSensorDescription(
        key="mppt_4_power",
        translation_key="mppt_4_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-panel",
        entity_registry_enabled_default=False,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.mppt_strings[3].power_w, 1) if len(d.mppt_strings) >= 4 else None,
    ),
    EcoFlowSystemSensorDescription(
        key="mppt_4_voltage",
        translation_key="mppt_4_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.mppt_strings[3].volt, 1) if len(d.mppt_strings) >= 4 else None,
    ),
    EcoFlowSystemSensorDescription(
        key="mppt_4_current",
        translation_key="mppt_4_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.mppt_strings[3].amp, 2) if len(d.mppt_strings) >= 4 else None,
    ),
)


# ── Energie-Akkumulatoren (kWh) ───────────────────────────────────────────────

ENERGY_ACCUMULATOR_TYPES: tuple[EcoFlowEnergyAccumulatorDescription, ...] = (

    EcoFlowEnergyAccumulatorDescription(
        key="solar_energy",
        translation_key="solar_energy",
        icon="mdi:solar-power",
        power_fn=lambda d: max(
            _solar_power_w(d), 0.0,
        ),
    ),
    EcoFlowEnergyAccumulatorDescription(
        key="grid_import_energy",
        translation_key="grid_import_energy",
        icon="mdi:transmission-tower-import",
        power_fn=lambda d: max(
            _grid_power_w(d), 0.0,
        ),
    ),
    EcoFlowEnergyAccumulatorDescription(
        key="grid_export_energy",
        translation_key="grid_export_energy",
        icon="mdi:transmission-tower-export",
        power_fn=lambda d: max(
            -_grid_power_w(d), 0.0,
        ),
    ),
    EcoFlowEnergyAccumulatorDescription(
        key="battery_discharge_energy",
        translation_key="battery_discharge_energy",
        icon="mdi:battery-arrow-up",
        power_fn=lambda d: max(
            _battery_power_w(d), 0.0,
        ),
    ),
    EcoFlowEnergyAccumulatorDescription(
        key="battery_charge_energy",
        translation_key="battery_charge_energy",
        icon="mdi:battery-arrow-down",
        power_fn=lambda d: max(
            -_battery_power_w(d), 0.0,
        ),
    ),
)


# ── Plattform-Setup ───────────────────────────────────────────────────────────

async def async_setup_platform(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    discovery_info: Any = None,
) -> None:
    """Legacy-Plattform-Setup (nicht verwendet)."""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Initialisiert alle Sensor-Entitäten für einen Config Entry."""
    coordinator: EcoFlowCoordinator = hass.data[DOMAIN][entry.entry_id]
    serial = entry.data[CONF_SERIAL_NUMBER]
    # Options haben Vorrang vor initialen Konfigurationsdaten
    num_packs: int = entry.options.get(
        CONF_NUM_BATTERY_PACKS,
        entry.data.get(CONF_NUM_BATTERY_PACKS, DEFAULT_NUM_BATTERY_PACKS),
    )

    device_info = DeviceInfo(
        identifiers={(DOMAIN, serial)},
        name=f"{MANUFACTURER} {MODEL}",
        manufacturer=MANUFACTURER,
        model=MODEL,
        serial_number=serial,
        configuration_url="https://www.ecoflow.com",
    )

    entities: list[SensorEntity] = []

    # Batterie-Pack-Sensoren
    for pack_index in range(1, num_packs + 1):
        entities.extend(
            _create_battery_sensors(coordinator, device_info, serial, pack_index)
        )

    # Systemweite Leistungs-Sensoren
    for desc in (*ENERGY_STREAM_SENSOR_TYPES, *EMS_HEARTBEAT_SENSOR_TYPES):
        entities.append(EcoFlowSystemSensor(
            coordinator=coordinator, description=desc,
            device_info=device_info, serial=serial,
        ))

    # Energie-Akkumulatoren (kWh)
    for desc in ENERGY_ACCUMULATOR_TYPES:
        entities.append(EcoFlowEnergyAccumulatorSensor(
            coordinator=coordinator, description=desc,
            device_info=device_info, serial=serial,
        ))

    # Verbindungsstatus
    entities.append(EcoFlowConnectionSensor(
        coordinator=coordinator, device_info=device_info, serial=serial,
    ))

    _LOGGER.debug(
        "Erstelle %d Sensor-Entitäten für %d Batterie-Pack(s) (SN: %s)",
        len(entities), num_packs, serial,
    )
    async_add_entities(entities)


def _create_battery_sensors(
    coordinator: EcoFlowCoordinator,
    device_info: DeviceInfo,
    serial: str,
    pack_index: int,
) -> list[EcoFlowBatterySensor]:
    """Erstellt alle Sensor-Entitäten für einen Batterie-Pack."""
    return [
        EcoFlowBatterySensor(
            coordinator=coordinator, description=desc,
            device_info=device_info, serial=serial, pack_index=pack_index,
        )
        for desc in BATTERY_SENSOR_TYPES
    ]


# ── Sensor-Klassen ────────────────────────────────────────────────────────────

class EcoFlowBatterySensor(CoordinatorEntity[EcoFlowCoordinator], SensorEntity):
    """Sensor für einen Messwert eines einzelnen Batterie-Packs."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, description, device_info, serial, pack_index):
        super().__init__(coordinator)
        self.entity_description = description
        self._pack_index = pack_index
        self._serial = serial
        self._attr_unique_id = f"{serial}_battery_{pack_index}_{description.key}"
        self._attr_device_info = device_info

    @property
    def name(self) -> str:
        base = (self.entity_description.translation_key or self.entity_description.key).replace("_", " ").title()
        return f"Battery {self._pack_index} {base.replace('Battery ', '')}"

    @property
    def native_value(self) -> Any:
        pack: BatteryPackData | None = coordinator_batteries(self.coordinator).get(self._pack_index)
        if pack is None:
            return None
        try:
            return self.entity_description.value_fn(pack)
        except Exception:
            return None

    @property
    def available(self) -> bool:
        return super().available and self._pack_index in coordinator_batteries(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"pack_index": self._pack_index}
        pack = coordinator_batteries(self.coordinator).get(self._pack_index)
        if pack:
            attrs["pack_serial_number"] = pack.serial_number
            attrs["is_charging"] = pack.is_charging
            attrs["real_soc"] = pack.real_soc
        return attrs


class EcoFlowSystemSensor(CoordinatorEntity[EcoFlowCoordinator], SensorEntity):
    """Sensor für systemweite Momentwerte (W, %, V, A, Hz, VA, var)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, description, device_info, serial):
        super().__init__(coordinator)
        self.entity_description = description
        self._serial = serial
        self._last_valid_value: Any = None
        self._attr_unique_id = f"{serial}_{description.key}"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None

        if self.entity_description.uses_coordinator_data:
            data: Any = self.coordinator.data
        else:
            data = self.coordinator.data.get(self.entity_description.data_key)
            if data is None:
                return None

        try:
            value = self.entity_description.value_fn(data)
        except Exception:
            return None

        # Speziell fuer Netzfrequenz: letzten gueltigen Wert halten statt "unknown",
        # falls einzelne Telegramme den Wert nicht enthalten.
        if self.entity_description.key == "grid_frequency":
            if value is not None:
                self._last_valid_value = value
                return value
            return self._last_valid_value

        return value

    @property
    def available(self) -> bool:
        if self.entity_description.uses_coordinator_data:
            return super().available and self.coordinator.data is not None
        return (
            super().available
            and self.coordinator.data is not None
            and self.coordinator.data.get(self.entity_description.data_key) is not None
        )


class EcoFlowEnergyAccumulatorSensor(CoordinatorEntity[EcoFlowCoordinator], RestoreSensor):
    """
    Energie-Akkumulator (kWh) — integriert Leistung (W) per Links-Riemann-Summe.

    device_class: energy  |  state_class: total_increasing  |  unit: kWh
    → Erscheint direkt in allen Energie-Dashboard-Dropdowns.
    Wert bleibt über HA-Neustarts erhalten (RestoreSensor).
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, description, device_info, serial):
        super().__init__(coordinator)
        self.entity_description = description
        self._serial = serial
        self._attr_unique_id = f"{serial}_{description.key}"
        self._attr_device_info = device_info
        self._accumulated_kwh: float = 0.0
        self._last_update: datetime | None = None
        self._last_power_w: float = 0.0

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_data = await self.async_get_last_sensor_data()
        if last_data is not None and last_data.native_value is not None:
            try:
                self._accumulated_kwh = float(last_data.native_value)
                _LOGGER.debug("Energiezähler %s: %.4f kWh wiederhergestellt",
                              self.entity_description.key, self._accumulated_kwh)
            except (TypeError, ValueError):
                pass

    @callback
    def _handle_coordinator_update(self) -> None:
        now = dt_util.utcnow()
        power_w = self._get_power_w()
        if self._last_update is not None:
            dt_hours = (now - self._last_update).total_seconds() / 3600.0
            self._accumulated_kwh += (self._last_power_w / 1000.0) * dt_hours
        self._last_update = now
        self._last_power_w = power_w
        super()._handle_coordinator_update()

    def _get_power_w(self) -> float:
        if not self.coordinator.data:
            return 0.0
        try:
            return self.entity_description.power_fn(self.coordinator.data)
        except Exception:
            return 0.0

    @property
    def native_value(self) -> float:
        return round(self._accumulated_kwh, 4)

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and (
                self.coordinator.data.get(DATA_ENERGY_STREAM) is not None
                or self.coordinator.data.get(DATA_EMS_HEARTBEAT) is not None
            )
        )


class EcoFlowConnectionSensor(CoordinatorEntity[EcoFlowCoordinator], SensorEntity):
    """
    Verbindungsstatus der MQTT-Verbindung zum EcoFlow Cloud-Broker.

    Nützlich für Automationen ("benachrichtige mich wenn Verbindung weg").
    """

    _attr_has_entity_name = True
    _attr_translation_key = "connection_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["connected", "disconnected"]

    def __init__(self, coordinator, device_info, serial):
        super().__init__(coordinator)
        self._attr_unique_id = f"{serial}_connection_status"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> str:
        return "connected" if self.coordinator._mqtt_connected else "disconnected"

    @property
    def icon(self) -> str:
        return "mdi:cloud-check" if self.coordinator._mqtt_connected else "mdi:cloud-off"

    @property
    def available(self) -> bool:
        return True  # Immer verfügbar — zeigt connected/disconnected


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def coordinator_batteries(coordinator: EcoFlowCoordinator) -> dict[int, BatteryPackData]:
    """Gibt das Batterie-Pack-Dictionary aus dem Coordinator zurück."""
    return coordinator.data.get(DATA_BATTERIES, {}) if coordinator.data else {}
