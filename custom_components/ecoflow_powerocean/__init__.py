"""
EcoFlow PowerOcean Plus — Home Assistant Integration.

Diese Integration ermöglicht die Überwachung einer EcoFlow PowerOcean Plus
Photovoltaikanlage in Home Assistant. Sie kommuniziert über die EcoFlow
Private Cloud API (MQTT) und verarbeitet die Protobuf-kodierten Gerätedaten.

Unterstützte Geräte:
    - EcoFlow PowerOcean Plus 15 kW (Seriennummer beginnt mit R37)
    - Bis zu 9 EcoFlow Batterie-Packs

Kommunikationsweg:
    HA ──HTTPS──► api.ecoflow.com  (Login + MQTT-Credentials)
    HA ──MQTTS──► mqtt-e.ecoflow.com:8883  (Echtzeit-Gerätedaten)
    Gerät ──────► mqtt-e.ecoflow.com  (Gerät sendet von sich aus)

Einschränkungen:
    - Benötigt aktive Internetverbindung (Cloud-abhängig)
    - Die EcoFlow Developer API (Open API) unterstützt den PowerOcean Plus
      derzeit nicht vollständig (Error 1006 bei /device/quota/all)
    - Lokaler Zugriff nur über Modbus TCP Port 502 möglich (noch nicht implementiert)
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_DEBUG_MODE,
    CONF_ENABLE_BACKUP_OUTAGE_NOTIFICATION,
    CONF_ENABLE_DAILY_SUNSET_REPORT,
    DEFAULT_ENABLE_BACKUP_OUTAGE_NOTIFICATION,
    DEFAULT_DEBUG_MODE,
    DEFAULT_ENABLE_DAILY_SUNSET_REPORT,
    DOMAIN,
    PLATFORMS,
)
from .backup_notification import (
    BACKUP_NOTIFICATION_DATA_KEY,
    BackupOutageNotificationManager,
)
from .coordinator import EcoFlowCoordinator
from .daily_report import DAILY_REPORT_DATA_KEY, DailySunsetReportManager

_LOGGER = logging.getLogger(__name__)


def _apply_debug_logging(entry: ConfigEntry) -> None:
    """
    Aktiviert/deaktiviert Debug-Logging für diese Integration.

    Hinweis:
    Der Schalter wirkt auf den Integrations-Logger-Namespace
    `custom_components.ecoflow_powerocean`.
    """
    debug_mode = bool(entry.options.get(CONF_DEBUG_MODE, DEFAULT_DEBUG_MODE))
    integration_logger = logging.getLogger("custom_components.ecoflow_powerocean")
    integration_logger.setLevel(logging.DEBUG if debug_mode else logging.NOTSET)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Lädt die Integration neu wenn Options geändert wurden."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Richtet einen Config Entry ein.

    Wird aufgerufen wenn die Integration geladen wird (HA-Start oder manuelle
    Aktivierung). Erstellt den Coordinator, baut die Verbindung auf und
    registriert alle Sensor-Plattformen.

    Args:
        hass:  Home Assistant Instanz.
        entry: Konfigurationseintrag mit Zugangsdaten.

    Returns:
        True bei Erfolg.

    Raises:
        ConfigEntryNotReady: Wenn die Verbindung nicht hergestellt werden kann.
    """
    hass.data.setdefault(DOMAIN, {})
    _apply_debug_logging(entry)

    coordinator = EcoFlowCoordinator(hass, entry)

    # Verbindung aufbauen (Login + MQTT)
    try:
        await coordinator.async_setup()
    except Exception as exc:
        raise ConfigEntryNotReady(
            f"Verbindung zu EcoFlow PowerOcean Plus fehlgeschlagen: {exc}"
        ) from exc

    # Ersten Datenabzug durchführen (wartet auf MQTT-Daten mit Timeout)
    await coordinator.async_config_entry_first_refresh()

    # Coordinator im globalen HA-State speichern
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Optionaler Tagesbericht:
    # Der Coordinator bleibt unverändert unter hass.data[DOMAIN][entry.entry_id],
    # weil sensor.py und binary_sensor.py diese Struktur direkt nutzen.
    if bool(
        entry.options.get(
            CONF_ENABLE_DAILY_SUNSET_REPORT,
            DEFAULT_ENABLE_DAILY_SUNSET_REPORT,
        )
    ):
        manager = DailySunsetReportManager(hass, entry, coordinator)
        try:
            await manager.async_setup()
        except Exception as exc:
            _LOGGER.warning(
                "Täglicher Sonnenuntergangsbericht konnte nicht gestartet werden: %s",
                exc,
            )
        else:
            hass.data.setdefault(DAILY_REPORT_DATA_KEY, {})[entry.entry_id] = manager

    if bool(
        entry.options.get(
            CONF_ENABLE_BACKUP_OUTAGE_NOTIFICATION,
            DEFAULT_ENABLE_BACKUP_OUTAGE_NOTIFICATION,
        )
    ):
        backup_notification_manager = BackupOutageNotificationManager(
            hass,
            entry,
            coordinator,
        )
        try:
            await backup_notification_manager.async_setup()
        except Exception as exc:
            _LOGGER.warning(
                "Stromausfall-Benachrichtigung konnte nicht gestartet werden: %s",
                exc,
            )
        else:
            hass.data.setdefault(BACKUP_NOTIFICATION_DATA_KEY, {})[
                entry.entry_id
            ] = backup_notification_manager

    # Sensor-Plattform initialisieren
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Bei Options-Änderungen Integration neu laden
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _LOGGER.info(
        "EcoFlow PowerOcean Plus Integration gestartet (SN: %s, debug_mode=%s)",
        coordinator.serial_number,
        bool(entry.options.get(CONF_DEBUG_MODE, DEFAULT_DEBUG_MODE)),
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Entlädt einen Config Entry.

    Wird aufgerufen wenn die Integration deaktiviert, neu geladen oder
    entfernt wird. Trennt die MQTT-Verbindung sauber und gibt Ressourcen frei.

    Args:
        hass:  Home Assistant Instanz.
        entry: Zu entladender Konfigurationseintrag.

    Returns:
        True wenn alle Plattformen erfolgreich entladen wurden.
    """
    coordinator: EcoFlowCoordinator = hass.data[DOMAIN].get(entry.entry_id)

    # Plattformen entladen
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    daily_report_manager = hass.data.get(DAILY_REPORT_DATA_KEY, {}).pop(
        entry.entry_id,
        None,
    )
    if daily_report_manager is not None:
        await daily_report_manager.async_shutdown()

    backup_notification_manager = hass.data.get(
        BACKUP_NOTIFICATION_DATA_KEY,
        {},
    ).pop(
        entry.entry_id,
        None,
    )
    if backup_notification_manager is not None:
        await backup_notification_manager.async_shutdown()

    # MQTT-Verbindung trennen
    if coordinator:
        await coordinator.async_shutdown()

    # Daten aus HA-State entfernen
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
