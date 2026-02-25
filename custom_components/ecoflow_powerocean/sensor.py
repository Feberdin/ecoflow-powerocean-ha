"""
Sensor-Plattform für die EcoFlow PowerOcean Plus Integration.

Implementierte Sensorgruppen:

    Pro Batterie-Pack (konfigurierbar, Standard: 2):
    ┌──────────────────────────────────────────────────────────┐
    │ • Ladestand (SOC)          %    — Primärsensor           │
    │ • Gesundheitszustand (SOH) %    — Langzeitüberwachung    │
    │ • Aktuelle Leistung        W    — Laden/Entladen         │
    │ • Spannung                 V    — Betriebspunkt          │
    │ • Strom                    A    — Betriebspunkt          │
    │ • Verbleibende Energie     Wh   — Energiemanagement      │
    │ • Umgebungstemperatur      °C   — Thermoüberwachung      │
    │ • Ladezyklen               —    — Alterungsindikator     │
    └──────────────────────────────────────────────────────────┘

    Systemweite Leistungs-Sensoren (JTS1_EMS_HEARTBEAT):
    ┌──────────────────────────────────────────────────────────┐
    │ • Solar-Leistung           W    — PV-Ertrag gesamt       │
    │ • Netz-Leistung            W    — Bezug/Einspeisung      │
    │ • Hausverbrauch            W    — Aktuelle Last          │
    │ • Batterie-Gesamtleistung  W    — Laden/Entladen         │
    │ • Gesamt-Ladestand         %    — Kombinierter SOC       │
    │ • Phase L1/L2/L3 Spannung  V    — 3-Phasen-Daten        │
    │ • Phase L1/L2/L3 Strom     A    — 3-Phasen-Daten        │
    │ • Phase L1/L2/L3 Leistung  W    — 3-Phasen-Daten        │
    │ • Netzfrequenz             Hz   — Netzqualität           │
    │ • MPPT 1–4 Leistung        W    — PV-Strings            │
    └──────────────────────────────────────────────────────────┘

    Energie-Akkumulatoren für das HA Energie-Dashboard (kWh):
    ┌──────────────────────────────────────────────────────────┐
    │ • Solar-Energie            kWh  — Energie-Dashboard      │
    │ • Netz-Bezug               kWh  — Energie-Dashboard      │
    │ • Netz-Einspeisung         kWh  — Energie-Dashboard      │
    │ • Batterie-Entnahme        kWh  — Energie-Dashboard      │
    │ • Batterie-Ladung          kWh  — Energie-Dashboard      │
    └──────────────────────────────────────────────────────────┘

    Die Akkumulatoren integrieren Momentleistung (W) per Riemann-Summe
    zu kumulierten Energiemengen (kWh). Werte bleiben über HA-Neustarts
    erhalten (RestoreSensor). Kein YAML oder Helfer nötig.
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
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
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
    DATA_EMS_HEARTBEAT,
    DEFAULT_NUM_BATTERY_PACKS,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .coordinator import EcoFlowCoordinator
from .proto_decoder import BatteryPackData

_LOGGER = logging.getLogger(__name__)


# ── Sensor-Beschreibungen ─────────────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class EcoFlowBatterySensorDescription(SensorEntityDescription):
    """Sensor-Beschreibung für Batterie-Pack-Sensoren."""
    value_fn: Callable[[BatteryPackData], Any] = lambda _: None


@dataclass(frozen=True, kw_only=True)
class EcoFlowSystemSensorDescription(SensorEntityDescription):
    """
    Sensor-Beschreibung für systemweite Momentleistungs-Sensoren (W, %, V, A, Hz).

    `data_key` gibt an, welcher Schlüssel in coordinator.data ausgelesen wird.
    `value_fn` extrahiert den Wert aus dem jeweiligen Datenobjekt.
    """
    data_key: str = ""
    value_fn: Callable[[Any], Any] = lambda _: None


@dataclass(frozen=True, kw_only=True)
class EcoFlowEnergyAccumulatorDescription(SensorEntityDescription):
    """
    Sensor-Beschreibung für Energie-Akkumulatoren (kWh, für das Energie-Dashboard).

    `power_fn` wird mit coordinator.data aufgerufen und gibt die zu integrierende
    Leistung in Watt zurück (immer >= 0). Die Sensoren akkumulieren die Energie
    per Links-Riemann-Summe und behalten den Wert über HA-Neustarts (RestoreSensor).
    """
    power_fn: Callable[[dict], float] = lambda _: 0.0


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
        key="cycles",
        translation_key="battery_cycles",
        icon="mdi:battery-sync",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda p: p.cycles,
    ),
)


# ── Systemweite Leistungs-Sensoren ────────────────────────────────────────────

# Aus EMS_HEARTBEAT abgeleitet (Gerät sendet cmdId=33 nicht zuverlässig):
#   solar_power   = Summe MPPT-String-Leistungen
#   grid_power    = Summe Phasen-actPwr (negativ = Einspeisung)
#   load_power    = Energiebilanz: solar + battery + grid
#   battery_total = emsBpPower (Feld 59)
#   total_soc     = Durchschnitt Pack-SOCs aus DATA_BATTERIES

ENERGY_STREAM_SENSOR_TYPES: tuple[EcoFlowSystemSensorDescription, ...] = (

    EcoFlowSystemSensorDescription(
        key="solar_power",
        translation_key="solar_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(sum(s.power_w for s in d.mppt_strings), 1),
    ),

    EcoFlowSystemSensorDescription(
        key="grid_power",
        translation_key="grid_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower",
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.phase_a.act_pwr + d.phase_b.act_pwr + d.phase_c.act_pwr, 1),
    ),

    EcoFlowSystemSensorDescription(
        key="load_power",
        translation_key="load_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(
            sum(s.power_w for s in d.mppt_strings)
            + d.battery_power_w
            + (d.phase_a.act_pwr + d.phase_b.act_pwr + d.phase_c.act_pwr),
            1,
        ),
    ),

    EcoFlowSystemSensorDescription(
        key="battery_total_power",
        translation_key="battery_total_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-charging",
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.battery_power_w, 1),
    ),

    EcoFlowSystemSensorDescription(
        key="total_soc",
        translation_key="total_soc",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        data_key=DATA_BATTERIES,
        value_fn=lambda d: int(sum(p.soc for p in d.values()) / len(d)) if d else None,
    ),
)


EMS_HEARTBEAT_SENSOR_TYPES: tuple[EcoFlowSystemSensorDescription, ...] = (

    # Phase L1
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

    # Phase L2
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

    # Phase L3
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

    # Netz
    EcoFlowSystemSensorDescription(
        key="grid_frequency",
        translation_key="grid_frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        data_key=DATA_EMS_HEARTBEAT,
        value_fn=lambda d: round(d.frequency_hz, 2),
    ),

    # MPPT-Strings
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
)


# ── Energie-Akkumulatoren (kWh, für das Energie-Dashboard) ───────────────────
#
# Jeder Akkumulator integriert eine Leistung (W) über die Zeit (Links-Riemann-Summe)
# und liefert eine kumulierte Energiemenge (kWh).
#
# Bidirektionale Quellen (Netz, Batterie) werden in Bezug und Rückspeisung
# aufgeteilt — power_fn gibt immer einen nicht-negativen Wert zurück.

ENERGY_ACCUMULATOR_TYPES: tuple[EcoFlowEnergyAccumulatorDescription, ...] = (

    EcoFlowEnergyAccumulatorDescription(
        key="solar_energy",
        translation_key="solar_energy",
        icon="mdi:solar-power",
        # Summe aller MPPT-String-Leistungen
        power_fn=lambda d: max(
            sum(s.power_w for s in d[DATA_EMS_HEARTBEAT].mppt_strings)
            if d.get(DATA_EMS_HEARTBEAT) is not None else 0.0,
            0.0,
        ),
    ),

    EcoFlowEnergyAccumulatorDescription(
        key="grid_import_energy",
        translation_key="grid_import_energy",
        icon="mdi:transmission-tower-import",
        # Phasen-Summe positiv = Netzbezug
        power_fn=lambda d: max(
            d[DATA_EMS_HEARTBEAT].phase_a.act_pwr
            + d[DATA_EMS_HEARTBEAT].phase_b.act_pwr
            + d[DATA_EMS_HEARTBEAT].phase_c.act_pwr
            if d.get(DATA_EMS_HEARTBEAT) is not None else 0.0,
            0.0,
        ),
    ),

    EcoFlowEnergyAccumulatorDescription(
        key="grid_export_energy",
        translation_key="grid_export_energy",
        icon="mdi:transmission-tower-export",
        # Phasen-Summe negativ = Einspeisung (Betrag)
        power_fn=lambda d: max(
            -(d[DATA_EMS_HEARTBEAT].phase_a.act_pwr
              + d[DATA_EMS_HEARTBEAT].phase_b.act_pwr
              + d[DATA_EMS_HEARTBEAT].phase_c.act_pwr)
            if d.get(DATA_EMS_HEARTBEAT) is not None else 0.0,
            0.0,
        ),
    ),

    EcoFlowEnergyAccumulatorDescription(
        key="battery_discharge_energy",
        translation_key="battery_discharge_energy",
        icon="mdi:battery-arrow-up",
        # emsBpPower positiv = Batterie entlädt
        power_fn=lambda d: max(
            d[DATA_EMS_HEARTBEAT].battery_power_w
            if d.get(DATA_EMS_HEARTBEAT) is not None else 0.0,
            0.0,
        ),
    ),

    EcoFlowEnergyAccumulatorDescription(
        key="battery_charge_energy",
        translation_key="battery_charge_energy",
        icon="mdi:battery-arrow-down",
        # emsBpPower negativ = Batterie lädt (Betrag)
        power_fn=lambda d: max(
            -d[DATA_EMS_HEARTBEAT].battery_power_w
            if d.get(DATA_EMS_HEARTBEAT) is not None else 0.0,
            0.0,
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
    """
    Initialisiert alle Sensor-Entitäten für einen Config Entry.

    Erstellt beim Start:
    - Batterie-Pack-Sensoren (8 × num_packs)
    - Systemweite Leistungs-Sensoren (Momentwerte in W/%, V, A, Hz)
    - Energie-Akkumulatoren (5 kWh-Sensoren für das Energie-Dashboard)
    """
    coordinator: EcoFlowCoordinator = hass.data[DOMAIN][entry.entry_id]
    serial = entry.data[CONF_SERIAL_NUMBER]
    num_packs: int = entry.data.get(CONF_NUM_BATTERY_PACKS, DEFAULT_NUM_BATTERY_PACKS)

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
            coordinator=coordinator,
            description=desc,
            device_info=device_info,
            serial=serial,
        ))

    # Energie-Akkumulatoren (kWh) für das Energie-Dashboard
    for desc in ENERGY_ACCUMULATOR_TYPES:
        entities.append(EcoFlowEnergyAccumulatorSensor(
            coordinator=coordinator,
            description=desc,
            device_info=device_info,
            serial=serial,
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
            coordinator=coordinator,
            description=desc,
            device_info=device_info,
            serial=serial,
            pack_index=pack_index,
        )
        for desc in BATTERY_SENSOR_TYPES
    ]


# ── Sensor-Entitäten ──────────────────────────────────────────────────────────

class EcoFlowBatterySensor(CoordinatorEntity[EcoFlowCoordinator], SensorEntity):
    """Sensor für einen Messwert eines einzelnen Batterie-Packs."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EcoFlowCoordinator,
        description: EcoFlowBatterySensorDescription,
        device_info: DeviceInfo,
        serial: str,
        pack_index: int,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._pack_index = pack_index
        self._serial = serial
        self._attr_unique_id = f"{serial}_battery_{pack_index}_{description.key}"
        self._attr_device_info = device_info

    @property
    def name(self) -> str:
        base_name = (self.entity_description.translation_key or self.entity_description.key).replace("_", " ").title()
        return f"Battery {self._pack_index} {base_name.replace('Battery ', '')}"

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
        return (
            super().available
            and self._pack_index in coordinator_batteries(self.coordinator)
        )

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
    """Sensor für systemweite Momentwerte (W, %, V, A, Hz)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EcoFlowCoordinator,
        description: EcoFlowSystemSensorDescription,
        device_info: DeviceInfo,
        serial: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._serial = serial
        self._attr_unique_id = f"{serial}_{description.key}"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> Any:
        data = (
            self.coordinator.data.get(self.entity_description.data_key)
            if self.coordinator.data
            else None
        )
        if data is None:
            return None
        try:
            return self.entity_description.value_fn(data)
        except Exception:
            return None

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self.coordinator.data.get(self.entity_description.data_key) is not None
        )


class EcoFlowEnergyAccumulatorSensor(CoordinatorEntity[EcoFlowCoordinator], RestoreSensor):
    """
    Energie-Akkumulator-Sensor (kWh) für das HA Energie-Dashboard.

    Integriert Momentleistung (W) über die Zeit per Links-Riemann-Summe zu
    einer kumulierten Energiemenge (kWh). Der Wert bleibt über HA-Neustarts
    erhalten. Kein externer Helfer oder YAML nötig.

    Eigenschaften die das Energie-Dashboard erkennt:
        device_class:  energy
        state_class:   total_increasing
        unit:          kWh
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self,
        coordinator: EcoFlowCoordinator,
        description: EcoFlowEnergyAccumulatorDescription,
        device_info: DeviceInfo,
        serial: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._serial = serial
        self._attr_unique_id = f"{serial}_{description.key}"
        self._attr_device_info = device_info

        self._accumulated_kwh: float = 0.0
        self._last_update: datetime | None = None
        self._last_power_w: float = 0.0

    async def async_added_to_hass(self) -> None:
        """Letzten gespeicherten Energiewert nach HA-Neustart wiederherstellen."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_sensor_data()
        if last_data is not None and last_data.native_value is not None:
            try:
                self._accumulated_kwh = float(last_data.native_value)
                _LOGGER.debug(
                    "Energiezähler %s wiederhergestellt: %.4f kWh",
                    self.entity_description.key,
                    self._accumulated_kwh,
                )
            except (TypeError, ValueError):
                pass

    @callback
    def _handle_coordinator_update(self) -> None:
        """Wird bei jedem MQTT-Update aufgerufen — integriert die Leistung."""
        now = dt_util.utcnow()
        power_w = self._get_power_w()

        if self._last_update is not None:
            # Links-Riemann-Summe: vorherige Leistung × Zeitdelta
            dt_hours = (now - self._last_update).total_seconds() / 3600.0
            self._accumulated_kwh += (self._last_power_w / 1000.0) * dt_hours

        self._last_update = now
        self._last_power_w = power_w
        super()._handle_coordinator_update()

    def _get_power_w(self) -> float:
        """Gibt die zu integrierende Leistung in Watt zurück (immer >= 0)."""
        if not self.coordinator.data:
            return 0.0
        try:
            return self.entity_description.power_fn(self.coordinator.data)
        except Exception:
            return 0.0

    @property
    def native_value(self) -> float:
        """Akkumulierte Energie in kWh."""
        return round(self._accumulated_kwh, 4)

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self.coordinator.data.get(DATA_EMS_HEARTBEAT) is not None
        )


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def coordinator_batteries(
    coordinator: EcoFlowCoordinator,
) -> dict[int, BatteryPackData]:
    """Gibt das Batterie-Pack-Dictionary aus dem Coordinator-Datensatz zurück."""
    return coordinator.data.get(DATA_BATTERIES, {}) if coordinator.data else {}
