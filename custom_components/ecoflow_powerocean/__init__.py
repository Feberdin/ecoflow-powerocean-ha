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

from .const import DOMAIN, PLATFORMS
from .coordinator import EcoFlowCoordinator

_LOGGER = logging.getLogger(__name__)


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

    # Sensor-Plattform initialisieren
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info(
        "EcoFlow PowerOcean Plus Integration gestartet (SN: %s)",
        coordinator.serial_number,
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

    # MQTT-Verbindung trennen
    if coordinator:
        await coordinator.async_shutdown()

    # Daten aus HA-State entfernen
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
