"""
Button-Plattform fuer EcoFlow PowerOcean.

Zweck:
    Stellt einen manuellen Test-Button fuer den optionalen Tagesbericht bereit.
    Nutzer koennen damit sofort pruefen, ob Notify-Ziel, Service-Aufruf und
    Nachrichtenformat funktionieren, ohne auf den naechsten Sonnenuntergang zu
    warten.

Input:
    - Bereits eingerichteter DailySunsetReportManager aus `hass.data`
    - Config-Entry-Optionen fuer Aktivierung und Notify-Ziel

Output:
    - Eine Button-Entitaet `daily_report_test`, wenn der Tagesbericht aktiviert ist
    - Bei Tastendruck eine Testnachricht ueber den bestehenden Manager

Wichtige Invarianten:
    - Keine eigene Tagesbericht-Fachlogik in dieser Datei
    - Kein Veraendern von `hass.data[DOMAIN][entry.entry_id]`; dort bleibt der Coordinator
    - Ein Testdruck markiert den echten Tagesbericht nicht als gesendet

Debug-Hinweis:
    - Wenn der Button fehlt, ist der Tagesbericht deaktiviert oder der Manager
      konnte beim Setup nicht gestartet werden. Dann zuerst Integrationslogs
      mit `custom_components.ecoflow_powerocean: debug` pruefen.
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_DAILY_REPORT_NOTIFY_TARGET,
    CONF_ENABLE_DAILY_SUNSET_REPORT,
    CONF_SERIAL_NUMBER,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .daily_report import (
    DAILY_REPORT_DATA_KEY,
    DailySunsetReportManager,
    has_notification_target,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Initialisiert den Test-Button fuer den optionalen Tagesbericht."""
    if not bool(entry.options.get(CONF_ENABLE_DAILY_SUNSET_REPORT, False)):
        return

    manager = hass.data.get(DAILY_REPORT_DATA_KEY, {}).get(entry.entry_id)
    if manager is None:
        _LOGGER.warning(
            "Tagesbericht-Testbutton wird nicht angelegt: Manager ist nicht verfuegbar"
        )
        return

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
            EcoFlowDailyReportTestButton(
                manager=manager,
                device_info=device_info,
                serial=serial,
            )
        ]
    )


class EcoFlowDailyReportTestButton(ButtonEntity):
    """Button, der sofort einen Tagesbericht-Test versendet."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:message-badge-outline"
    _attr_translation_key = "daily_report_test"

    def __init__(
        self,
        *,
        manager: DailySunsetReportManager,
        device_info: DeviceInfo,
        serial: str,
    ) -> None:
        self._manager = manager
        self._attr_unique_id = f"{serial}_daily_report_test"
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        """Aktiv nur bei eingeschaltetem Bericht und vorhandenem Notify-Ziel."""
        return (
            bool(
                self._manager.options.get(
                    CONF_ENABLE_DAILY_SUNSET_REPORT,
                    False,
                )
            )
            and has_notification_target(
                self._manager.options.get(CONF_DAILY_REPORT_NOTIFY_TARGET)
            )
        )

    async def async_press(self) -> None:
        """Sendet eine Testnachricht ueber den Daily-Report-Manager."""
        sent = await self._manager.async_send_test_report()
        if not sent:
            raise HomeAssistantError(
                "EcoFlow Tagesbericht-Test konnte nicht gesendet werden. "
                "Bitte Benachrichtigungsziel und Integrationslogs pruefen."
            )
