"""
Sensor-Plattform für die EcoFlow PowerOcean Plus Integration.

Definiert alle Home Assistant Sensor-Entitäten, die Batterie-Daten
vom EcoFlow PowerOcean Plus anzeigen.

Implementierte Sensorgruppen:

    Pro Batterie-Pack (dynamisch, je nach erkannten Packs):
    ┌─────────────────────────────────────────────────────────┐
    │ • Ladestand (SOC)           %    — Primärsensor         │
    │ • Gesundheitszustand (SOH)  %    — Langzeitüberwachung  │
    │ • Aktuelle Leistung         W    — Laden/Entladen        │
    │ • Spannung                  V    — Betriebspunkt         │
    │ • Strom                     A    — Betriebspunkt         │
    │ • Verbleibende Energie      kWh  — Energiemanagement     │
    │ • Umgebungstemperatur       °C   — Thermoüberwachung     │
    │ • Ladezyklen                —    — Alterungsindikator    │
    └─────────────────────────────────────────────────────────┘

    Systemübergreifend (aus JTS1_ENERGY_STREAM_REPORT):
    ┌─────────────────────────────────────────────────────────┐
    │ • Gesamt-Ladestand          %    — Kombinierter SOC      │
    └─────────────────────────────────────────────────────────┘

Erweiterbarkeit:
    Die Sensorliste ist bewusst modular aufgebaut. Weitere Sensoren
    (Grid-Leistung, Solar-Ertrag, Phasendaten) können einfach durch
    Erweiterung der BATTERY_SENSOR_TYPES und SYSTEM_SENSOR_TYPES
    hinzugefügt werden.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
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
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_SERIAL_NUMBER,
    DATA_BATTERIES,
    DATA_ENERGY_STREAM,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .coordinator import EcoFlowCoordinator
from .proto_decoder import BatteryPackData, EnergyStreamData

_LOGGER = logging.getLogger(__name__)


# ── Sensor-Beschreibungen ─────────────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class EcoFlowBatterySensorDescription(SensorEntityDescription):
    """
    Erweiterte Sensor-Beschreibung für Batterie-Pack-Sensoren.

    Zusätzlich zur Standard-SensorEntityDescription enthält diese Klasse
    einen `value_fn`, der den Wert aus einem BatteryPackData-Objekt extrahiert.
    Dies ermöglicht eine deklarative Sensor-Definition ohne Boilerplate-Code.
    """
    value_fn: Callable[[BatteryPackData], Any] = lambda _: None
    """Funktion zum Extrahieren des Sensorwerts aus den BatteryPackData."""


# Batterie-Pack-Sensoren — werden für jeden erkannten Pack instanziiert
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


# ── Plattform-Setup ───────────────────────────────────────────────────────────

async def async_setup_platform(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    discovery_info: Any = None,
) -> None:
    """Legacy-Plattform-Setup (wird nicht verwendet, async_setup_entry bevorzugt)."""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Initialisiert alle Sensor-Entitäten für einen Config Entry.

    Wird einmalig beim Laden der Integration aufgerufen. Erstellt Sensoren
    für alle bereits bekannten Batterie-Packs und registriert einen Listener,
    der bei neuen Packs automatisch weitere Sensoren anlegt.

    Args:
        hass:              Home Assistant Instanz.
        entry:             Konfigurationseintrag.
        async_add_entities: Callback zum Hinzufügen neuer Entitäten.
    """
    coordinator: EcoFlowCoordinator = hass.data[DOMAIN][entry.entry_id]
    serial = entry.data[CONF_SERIAL_NUMBER]

    # Gemeinsame Geräteinformationen für alle Sensoren dieser Anlage
    device_info = DeviceInfo(
        identifiers={(DOMAIN, serial)},
        name=f"{MANUFACTURER} {MODEL}",
        manufacturer=MANUFACTURER,
        model=MODEL,
        serial_number=serial,
        configuration_url="https://www.ecoflow.com",
    )

    # Bereits bekannte Packs sofort anlegen
    known_pack_indices: set[int] = set()
    entities: list[SensorEntity] = []

    batteries: dict[int, BatteryPackData] = coordinator.data.get(DATA_BATTERIES, {})
    for pack_index, pack_data in batteries.items():
        entities.extend(
            _create_battery_sensors(coordinator, device_info, serial, pack_index)
        )
        known_pack_indices.add(pack_index)

    async_add_entities(entities)

    # Listener für dynamisch erkannte neue Batterie-Packs
    def _handle_coordinator_update() -> None:
        """Prüft ob neue Batterie-Packs erkannt wurden und legt ggf. Sensoren an."""
        new_entities: list[SensorEntity] = []
        current_batteries: dict[int, BatteryPackData] = coordinator.data.get(DATA_BATTERIES, {})

        for pack_index in current_batteries:
            if pack_index not in known_pack_indices:
                _LOGGER.info("Neuer Batterie-Pack erkannt: Index %d", pack_index)
                new_entities.extend(
                    _create_battery_sensors(coordinator, device_info, serial, pack_index)
                )
                known_pack_indices.add(pack_index)

        if new_entities:
            async_add_entities(new_entities)

    coordinator.async_add_listener(_handle_coordinator_update)


def _create_battery_sensors(
    coordinator: EcoFlowCoordinator,
    device_info: DeviceInfo,
    serial: str,
    pack_index: int,
) -> list[EcoFlowBatterySensor]:
    """
    Erstellt alle Sensor-Entitäten für einen Batterie-Pack.

    Args:
        coordinator: Datenvermittler.
        device_info: HA-Geräteinformationen.
        serial:      Seriennummer des PowerOcean Geräts.
        pack_index:  Index des Batterie-Packs (1, 2, …).

    Returns:
        Liste aller Sensor-Entitäten für diesen Pack.
    """
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
    """
    Sensor-Entität für einen Messwert eines einzelnen Batterie-Packs.

    Jeder Sensor repräsentiert genau einen Messwert (z. B. SOC) eines
    bestimmten physischen Batterie-Packs im PowerOcean Plus Gehäuse.

    Die Entitäts-ID folgt dem Schema:
        sensor.ecoflow_powerocean_{serial}_battery_{pack_index}_{key}

    Beispiel für zwei Packs:
        sensor.ecoflow_powerocean_r371zd1azh4u0484_battery_1_soc
        sensor.ecoflow_powerocean_r371zd1azh4u0484_battery_2_soc
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EcoFlowCoordinator,
        description: EcoFlowBatterySensorDescription,
        device_info: DeviceInfo,
        serial: str,
        pack_index: int,
    ) -> None:
        """
        Initialisiert den Sensor.

        Args:
            coordinator: Datenvermittler mit aktuellen Gerätedaten.
            description: Sensor-Beschreibung (Einheit, Geräteklasse, Wertextraktion).
            device_info: HA-Geräteinformationen für die Gerätezuordnung.
            serial:      Seriennummer des PowerOcean Geräts.
            pack_index:  Index des Batterie-Packs (1-basiert).
        """
        super().__init__(coordinator)
        self.entity_description = description
        self._pack_index = pack_index
        self._serial = serial

        # Eindeutige ID: verhindert Duplikate nach HA-Neustart
        self._attr_unique_id = f"{serial}_battery_{pack_index}_{description.key}"

        # Gerät zuordnen
        self._attr_device_info = device_info

        # Zusätzliche Attribute (erscheinen in der HA-Attributliste)
        self._attr_extra_state_attributes: dict[str, Any] = {
            "pack_index": pack_index,
            "serial_number": serial,
        }

    @property
    def name(self) -> str:
        """
        Lesbarer Name des Sensors.

        Kombiniert Pack-Index und Sensor-Schlüssel für eindeutige Bezeichnung.
        Beispiel: "Battery 1 State of Charge"
        """
        base_name = (self.entity_description.translation_key or self.entity_description.key).replace("_", " ").title()
        return f"Battery {self._pack_index} {base_name.replace('Battery ', '')}"

    @property
    def native_value(self) -> Any:
        """
        Aktueller Sensorwert.

        Gibt None zurück wenn der Batterie-Pack noch keine Daten geliefert hat
        (z. B. kurz nach dem Start). HA zeigt dann "Unavailable" an.
        """
        pack: BatteryPackData | None = coordinator_batteries(self.coordinator).get(self._pack_index)
        if pack is None:
            return None
        try:
            return self.entity_description.value_fn(pack)
        except Exception:
            return None

    @property
    def available(self) -> bool:
        """
        Verfügbarkeit des Sensors.

        Ein Sensor ist verfügbar wenn:
        1. Der Coordinator erfolgreich Daten empfangen hat, UND
        2. Der entsprechende Batterie-Pack Daten geliefert hat.
        """
        return (
            super().available
            and self._pack_index in coordinator_batteries(self.coordinator)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Zusätzliche Attribute die im HA-Zustandsobjekt erscheinen."""
        attrs: dict[str, Any] = {
            "pack_index": self._pack_index,
        }
        pack = coordinator_batteries(self.coordinator).get(self._pack_index)
        if pack:
            attrs["pack_serial_number"] = pack.serial_number
            attrs["is_charging"] = pack.is_charging
            attrs["real_soc"] = pack.real_soc
        return attrs


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def coordinator_batteries(
    coordinator: EcoFlowCoordinator,
) -> dict[int, BatteryPackData]:
    """
    Gibt das Batterie-Pack-Dictionary aus dem Coordinator-Datensatz zurück.

    Args:
        coordinator: Aktiver Coordinator mit Gerätedaten.

    Returns:
        Dictionary {pack_index: BatteryPackData} — kann leer sein.
    """
    return coordinator.data.get(DATA_BATTERIES, {}) if coordinator.data else {}
