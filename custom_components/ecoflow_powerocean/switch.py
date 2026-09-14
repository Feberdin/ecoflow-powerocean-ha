"""
Switch-Plattform fuer den optionalen PowerOcean-Systemschalter.

Zweck:
    Stellt einen Home-Assistant-Schalter bereit, der den belegten EcoFlow-App-
    Befehl "Turn on/off system" ueber die bestehende MQTT-Verbindung ausfuehrt.

Input:
    - Aktivierte Option `enable_system_power_switch`
    - Laufender EcoFlowCoordinator mit MQTT-Verbindung

Output:
    - Optional eine `switch`-Entitaet fuer System AN/AUS

Wichtige Invarianten:
    - Standardmaessig wird keine steuernde Entitaet angelegt.
    - Es werden keine unbekannten Schreibbefehle geraten.
    - Fehler beim Schalten werden an HA gemeldet und stoppen nicht die Integration.

Debug-Hinweis:
    - Bei Problemen Debug-Modus aktivieren und nach
      `System-Power-Befehl gesendet` oder MQTT-Publish-Fehlern suchen.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ENABLE_SYSTEM_POWER_SWITCH,
    CONF_SERIAL_NUMBER,
    DEFAULT_ENABLE_SYSTEM_POWER_SWITCH,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .coordinator import EcoFlowCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Legt den System-Power-Schalter nur bei aktivierter Option an."""
    if not bool(
        entry.options.get(
            CONF_ENABLE_SYSTEM_POWER_SWITCH,
            DEFAULT_ENABLE_SYSTEM_POWER_SWITCH,
        )
    ):
        _LOGGER.debug("System-Power-Schalter ist deaktiviert")
        return

    coordinator: EcoFlowCoordinator = hass.data[DOMAIN][entry.entry_id]
    serial = str(entry.data.get(CONF_SERIAL_NUMBER, entry.entry_id))
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
            EcoFlowSystemPowerSwitch(
                coordinator=coordinator,
                device_info=device_info,
                serial=serial,
            )
        ]
    )


class EcoFlowSystemPowerSwitch(CoordinatorEntity[EcoFlowCoordinator], SwitchEntity):
    """Schalter fuer PowerOcean-System AN/AUS."""

    _attr_has_entity_name = True
    _attr_translation_key = "system_power"
    _attr_icon = "mdi:power"

    def __init__(
        self,
        coordinator: EcoFlowCoordinator,
        device_info: DeviceInfo,
        serial: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{serial}_system_power"
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        """Nur schaltbar, wenn HA den MQTT-Broker erreicht."""
        return super().available and self.coordinator.is_mqtt_connected

    @property
    def is_on(self) -> bool | None:
        """Echter Geraetestatus, sobald bekannt; sonst der zuletzt gesendete Zielwert."""
        return self.coordinator.system_power_on

    @property
    def assumed_state(self) -> bool:
        """Markiert den Zustand als angenommen, bis ein echter Status vorliegt."""
        return not self.coordinator.system_power_is_real

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnoseattribute fuer Statusquelle und Rohwerte."""
        status = self.coordinator.system_status
        attrs: dict[str, Any] = {
            "state_source": (
                "device" if self.coordinator.system_power_is_real else "assumed"
            )
        }
        if status is not None:
            attrs.update(
                {
                    "system_power_state": status.system_power_state,
                    "sys_on_off_machine_stat": status.sys_on_off_machine_stat,
                    "sys_work_sta": status.sys_work_sta,
                    "sys_grid_sta": status.sys_grid_sta,
                    "ems_work_mode": status.ems_work_mode_label,
                    "ems_work_state": status.ems_work_state_label,
                }
            )
        return attrs

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Schaltet das PowerOcean-System ein."""
        try:
            await self.coordinator.async_set_system_power(True)
        except Exception as exc:
            raise HomeAssistantError(
                f"EcoFlow: System einschalten fehlgeschlagen: {exc}"
            ) from exc
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Schaltet das PowerOcean-System aus."""
        try:
            await self.coordinator.async_set_system_power(False)
        except Exception as exc:
            raise HomeAssistantError(
                f"EcoFlow: System ausschalten fehlgeschlagen: {exc}"
            ) from exc
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
