"""
Button-Plattform fuer EcoFlow PowerOcean.

Zweck:
    Stellt manuelle Test-/Wiederholbuttons fuer optionale Nachrichten bereit.
    Nutzer koennen den gespeicherten Tagesbericht von gestern erneut senden und
    die Stromausfall-Benachrichtigung ohne echten Ausfall pruefen.

Input:
    - Bereits eingerichteter DailySunsetReportManager aus `hass.data`
    - Bereits eingerichteter BackupOutageNotificationManager aus `hass.data`
    - Config-Entry-Optionen fuer Aktivierung und Notify-Ziel

Output:
    - Eine Button-Entitaet `daily_report_test`, wenn der Tagesbericht aktiviert ist
    - Eine Button-Entitaet `backup_outage_notification_test`, wenn die
      Stromausfall-Benachrichtigung aktiviert ist
    - Bei Tastendruck eine Nachricht mit dem gespeicherten Bericht von gestern

Wichtige Invarianten:
    - Keine eigene Tagesbericht-Fachlogik in dieser Datei
    - Kein Veraendern von `hass.data[DOMAIN][entry.entry_id]`; dort bleibt der Coordinator
    - Ein Tastendruck markiert den echten Tagesbericht nicht als gesendet

Debug-Hinweis:
    - Wenn ein Button fehlt, ist die jeweilige Option deaktiviert oder der
      zugehoerige Manager konnte beim Setup nicht gestartet werden.
      Dann zuerst Integrationslogs mit
      `custom_components.ecoflow_powerocean: debug` pruefen.
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
    CONF_BACKUP_OUTAGE_NOTIFY_TARGET,
    CONF_DAILY_REPORT_NOTIFY_TARGET,
    CONF_ENABLE_BACKUP_OUTAGE_NOTIFICATION,
    CONF_ENABLE_DAILY_SUNSET_REPORT,
    CONF_SERIAL_NUMBER,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .backup_notification import (
    BACKUP_NOTIFICATION_DATA_KEY,
    BackupOutageNotificationManager,
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
    """Initialisiert optionale Benachrichtigungs-Buttons."""
    serial = str(entry.data.get(CONF_SERIAL_NUMBER, entry.entry_id))
    device_info = DeviceInfo(
        identifiers={(DOMAIN, serial)},
        name=f"{MANUFACTURER} {MODEL}",
        manufacturer=MANUFACTURER,
        model=MODEL,
        serial_number=serial,
        configuration_url="https://www.ecoflow.com",
    )

    entities: list[ButtonEntity] = []

    if bool(entry.options.get(CONF_ENABLE_DAILY_SUNSET_REPORT, False)):
        manager = hass.data.get(DAILY_REPORT_DATA_KEY, {}).get(entry.entry_id)
        if manager is None:
            _LOGGER.warning(
                "Tagesbericht-Testbutton wird nicht angelegt: Manager ist nicht verfuegbar"
            )
        else:
            entities.append(
                EcoFlowDailyReportTestButton(
                    manager=manager,
                    device_info=device_info,
                    serial=serial,
                )
            )

    if bool(entry.options.get(CONF_ENABLE_BACKUP_OUTAGE_NOTIFICATION, False)):
        backup_manager = hass.data.get(BACKUP_NOTIFICATION_DATA_KEY, {}).get(
            entry.entry_id
        )
        if backup_manager is None:
            _LOGGER.warning(
                "Stromausfall-Testbutton wird nicht angelegt: Manager ist nicht verfuegbar"
            )
        else:
            entities.append(
                EcoFlowBackupOutageNotificationTestButton(
                    manager=backup_manager,
                    device_info=device_info,
                    serial=serial,
                )
            )

    if entities:
        async_add_entities(entities)


class EcoFlowDailyReportTestButton(ButtonEntity):
    """Button, der den gespeicherten Tagesbericht von gestern versendet."""

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
        """Sendet den gestrigen Tagesbericht ueber den Daily-Report-Manager."""
        sent = await self._manager.async_send_test_report()
        if not sent:
            raise HomeAssistantError(
                "EcoFlow Tagesbericht von gestern konnte nicht gesendet werden. "
                "Bitte Benachrichtigungsziel, gespeicherten Vortag und "
                "Integrationslogs pruefen."
            )


class EcoFlowBackupOutageNotificationTestButton(ButtonEntity):
    """Button, der die Stromausfall-Benachrichtigung testweise versendet."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:bell-alert-outline"
    _attr_translation_key = "backup_outage_notification_test"

    def __init__(
        self,
        *,
        manager: BackupOutageNotificationManager,
        device_info: DeviceInfo,
        serial: str,
    ) -> None:
        self._manager = manager
        self._attr_unique_id = f"{serial}_backup_outage_notification_test"
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        """Aktiv nur bei eingeschalteter Benachrichtigung und Notify-Ziel."""
        return (
            bool(
                self._manager.options.get(
                    CONF_ENABLE_BACKUP_OUTAGE_NOTIFICATION,
                    False,
                )
            )
            and has_notification_target(
                self._manager.options.get(CONF_BACKUP_OUTAGE_NOTIFY_TARGET)
            )
        )

    async def async_press(self) -> None:
        """Sendet eine Testnachricht ueber den Backup-Notification-Manager."""
        sent = await self._manager.async_send_test_notification()
        if not sent:
            raise HomeAssistantError(
                "EcoFlow Stromausfall-Testbenachrichtigung konnte nicht gesendet "
                "werden. Bitte Benachrichtigungsziel, Backup Helpers und "
                "Integrationslogs pruefen."
            )
