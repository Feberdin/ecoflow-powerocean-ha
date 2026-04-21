"""
Binary-Sensor-Plattform für optionale Backup-/Stromausfall-Helfer.

Zweck:
    Diese Plattform stellt ausschließlich abgeleitete Hilfszustände bereit,
    damit Nutzer in Home Assistant eigene Automationen bauen können.

Input:
    - Bereits vorhandene Coordinator-Daten
    - Die in `backup_helpers.py` berechnete `BackupEvaluation`

Output:
    - `power_outage`
    - `backup_reserve_critical`
    - `backup_active`

Wichtige Invarianten:
    - Keine eigenen Netzwerkzugriffe
    - Keine Fachlogik-Duplikate zur Coordinator-/Helper-Ebene
    - Feature bleibt komplett optional und ist standardmäßig deaktiviert

Debug-Hinweis:
    - Zusätzliche Details wie `outage_reason` oder die geschätzte Restlaufzeit
      stehen als Entity-Attribute bereit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ENABLE_BACKUP_HELPERS, CONF_SERIAL_NUMBER, DOMAIN, MANUFACTURER, MODEL
from .coordinator import EcoFlowCoordinator


@dataclass(frozen=True, kw_only=True)
class EcoFlowBackupBinarySensorDescription(BinarySensorEntityDescription):
    """Beschreibung für einen Backup-Helper-Binary-Sensor."""

    is_on_fn: Callable[[Any], bool] = lambda _: False


BACKUP_BINARY_SENSOR_TYPES: tuple[EcoFlowBackupBinarySensorDescription, ...] = (
    EcoFlowBackupBinarySensorDescription(
        key="power_outage",
        translation_key="power_outage",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:transmission-tower-off",
        is_on_fn=lambda evaluation: evaluation.power_outage,
    ),
    EcoFlowBackupBinarySensorDescription(
        key="backup_reserve_critical",
        translation_key="backup_reserve_critical",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:battery-alert",
        is_on_fn=lambda evaluation: evaluation.backup_reserve_critical,
    ),
    EcoFlowBackupBinarySensorDescription(
        key="backup_active",
        translation_key="backup_active",
        device_class=BinarySensorDeviceClass.POWER,
        icon="mdi:home-battery-outline",
        is_on_fn=lambda evaluation: evaluation.backup_active,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Initialisiert die optionalen Backup-Helper-Binary-Sensoren."""
    if not bool(entry.options.get(CONF_ENABLE_BACKUP_HELPERS, False)):
        return

    coordinator: EcoFlowCoordinator = hass.data[DOMAIN][entry.entry_id]
    serial = entry.data[CONF_SERIAL_NUMBER]
    device_info = DeviceInfo(
        identifiers={(DOMAIN, serial)},
        name=f"{MANUFACTURER} {MODEL}",
        manufacturer=MANUFACTURER,
        model=MODEL,
        serial_number=serial,
        configuration_url="https://www.ecoflow.com",
    )

    async_add_entities(
        [
            EcoFlowBackupBinarySensor(
                coordinator=coordinator,
                description=description,
                device_info=device_info,
                serial=serial,
            )
            for description in BACKUP_BINARY_SENSOR_TYPES
        ]
    )


class EcoFlowBackupBinarySensor(
    CoordinatorEntity[EcoFlowCoordinator], BinarySensorEntity
):
    """Binary Sensor, der nur den bereits ausgewerteten Backup-Zustand spiegelt."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, description, device_info, serial):
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key}"
        self._attr_device_info = device_info

    @property
    def is_on(self) -> bool:
        try:
            return bool(self.entity_description.is_on_fn(self.coordinator.backup_evaluation))
        except Exception:
            return False

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.backup_helpers_enabled
            and self.coordinator.backup_evaluation.observed_at is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        evaluation = self.coordinator.backup_evaluation
        attrs: dict[str, Any] = {
            "backup_helpers_enabled": self.coordinator.backup_helpers_enabled,
            "outage_reason": evaluation.outage_reason,
            "has_seen_valid_grid_frequency": evaluation.has_seen_valid_grid_frequency,
        }
        if evaluation.observed_at is not None:
            attrs["evaluated_at"] = evaluation.observed_at.isoformat()
        if evaluation.runtime_estimate_minutes is not None:
            attrs["runtime_estimate_minutes"] = evaluation.runtime_estimate_minutes
        if evaluation.smoothed_load_power_w is not None:
            attrs["smoothed_load_power_w"] = evaluation.smoothed_load_power_w
        if evaluation.usable_energy_wh is not None:
            attrs["usable_energy_wh"] = evaluation.usable_energy_wh
        return attrs
