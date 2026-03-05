"""
Diagnose-Export für die EcoFlow PowerOcean Plus Integration.

Zweck:
    Ermöglicht Nutzerinnen und Nutzern, über Home Assistant eine redigierte
    Diagnosedatei herunterzuladen und mit Maintainers zu teilen.

Input:
    - Home Assistant Instanz
    - Config Entry dieser Integration

Output:
    - JSON-kompatibles Dict mit redigierten Config-Daten und aktuellem
      Integrationsstatus (Coordinator-Daten, Verbindungszustand)

Wichtige Invarianten:
    - Keine sensiblen Daten im Klartext (Passwort, Token, User-ID, Seriennummer)
    - Ausgabe muss JSON-serialisierbar sein

Debug-Hinweis:
    - In HA: Geräte & Dienste -> EcoFlow PowerOcean -> Drei Punkte ->
      "Diagnose herunterladen"
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import CONF_SERIAL_NUMBER, DOMAIN
from .coordinator import EcoFlowCoordinator

TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SERIAL_NUMBER,
    "token",
    "_token",
    "userId",
    "_user_id",
    "certificateAccount",
    "_mqtt_user",
    "certificatePassword",
    "_mqtt_password",
    "serial_number",
}


def _to_jsonable(value: Any) -> Any:
    """Konvertiert beliebige Integrationsdaten in JSON-kompatible Strukturen."""
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Liefert redigierte Diagnosedaten für einen Config Entry."""
    coordinator: EcoFlowCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    coordinator_snapshot: dict[str, Any] = {
        "mqtt_connected": getattr(coordinator, "_mqtt_connected", None),
        "has_token": bool(getattr(coordinator, "_token", None)),
        "has_mqtt_credentials": bool(
            getattr(coordinator, "_mqtt_user", None)
            and getattr(coordinator, "_mqtt_password", None)
        ),
        "data": _to_jsonable(getattr(coordinator, "data", None)),
    }

    diagnostics = {
        "entry": entry.as_dict(),
        "coordinator": coordinator_snapshot,
    }

    return async_redact_data(diagnostics, TO_REDACT)
