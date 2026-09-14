#!/usr/bin/env python3
"""
Anonymisierten EcoFlow-PowerOcean-Messdaten-Snapshot bauen.

Zweck:
    Erstellt aus Home-Assistant-State-Daten ein reiches, aber teilbares JSON für
    Analyse-Issues und externe Repos.

Input:
    - Entweder eine Datei mit `/api/states`-JSON (`--states`)
    - oder ein Home-Assistant-URL mit Token aus `HOMEASSISTANT_TOKEN`

Output:
    - JSON auf stdout im Format `powerocean-observation.schema.json`

Wichtige Invarianten:
    - Keine Seriennummern, Tokens, E-Mail-Adressen oder vollständigen Entity-IDs
      aus Home Assistant werden ausgegeben.
    - Entity-Quellen werden nur als Domain plus generischer Suffix dokumentiert.
    - Das Script verwendet ausschließlich die Python-Standardbibliothek.

Debug-Hinweis:
    - Lokale Datei prüfen:
      `python3 tools/build_anonymized_observation.py --states states.json --source-id sample-a`
    - Direkt aus HA lesen:
      `HOMEASSISTANT_TOKEN=... python3 tools/build_anonymized_observation.py --ha-url http://homeassistant.local:8123 --source-id sample-a`
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
import re
import sys
from typing import Any, Iterable, Mapping
import urllib.request


SCHEMA_VERSION = 2

SERIAL_RE = re.compile(r"\bR[0-9A-Z]{8,}\b", re.IGNORECASE)
TOKEN_LIKE_RE = re.compile(r"\b[A-Za-z0-9_-]{32,}\b")
PACK_SENSOR_RE = re.compile(
    r"(?:^|_)battery_(?P<index>[1-9])_(?P<kind>soc|soh|power|remaining_energy|temperature|cycles)$"
)


FIELD_SPECS: dict[tuple[str, str], tuple[str, ...]] = {
    ("power", "solar_power_w"): ("solar_leistung", "solar_power"),
    ("power", "grid_power_w"): ("netz_leistung", "grid_power"),
    ("power", "load_power_w"): ("hausverbrauch", "load_power"),
    ("power", "battery_power_w"): (
        "batterie_gesamtleistung",
        "battery_total_power",
        "gesamt_batterieleistung",
    ),
    ("energy", "solar_energy_kwh"): ("solar_energie", "solar_energy"),
    ("energy", "grid_import_energy_kwh"): (
        "netzbezug",
        "netz_bezug",
        "grid_import_energy",
    ),
    ("energy", "grid_export_energy_kwh"): (
        "netz_einspeisung",
        "netzeinspeisung",
        "grid_export_energy",
    ),
    ("energy", "battery_discharge_energy_kwh"): (
        "batterie_entnahme",
        "battery_discharge_energy",
    ),
    ("energy", "battery_charge_energy_kwh"): (
        "batterie_ladung",
        "battery_charge_energy",
    ),
    ("backup", "runtime_estimate_minutes"): (
        "geschatzte_backup_laufzeit_minuten",
        "backup_runtime_estimate_minutes",
    ),
    ("backup", "runtime_estimate_hours"): (
        "geschatzte_backup_laufzeit_stunden",
        "backup_runtime_estimate_hours",
    ),
    ("backup", "usable_energy_wh"): (
        "nutzbare_backup_energie",
        "backup_usable_energy_wh",
    ),
    ("daily_report", "total_export_energy_kwh"): (
        "tagesbericht_gesamt_einspeisung",
        "daily_report_total_export_energy",
    ),
    ("daily_report", "total_value_eur"): (
        "tagesbericht_gesamt_vergutung",
        "daily_report_total_value",
    ),
    ("daily_report", "total_battery_full_hours"): (
        "tagesbericht_akku_100_gesamtzeit",
        "daily_report_total_battery_full_hours",
    ),
    ("system_limits", "battery_charge_limit_percent"): (
        "batterie_ladegrenze",
        "system_battery_charge_limit",
    ),
    ("system_limits", "battery_discharge_limit_percent"): (
        "batterie_entladegrenze",
        "system_battery_discharge_limit",
    ),
    ("system_limits", "backup_reserve_percent"): (
        "backup_reserve",
        "system_backup_ratio",
    ),
    ("system_limits", "feed_in_power_w"): (
        "einspeise_leistung",
        "system_feed_power",
    ),
}

STATUS_SPECS: dict[str, tuple[str, ...]] = {
    "connection_status": ("verbindungsstatus", "connection_status"),
    "system_power_state": ("system_power_status", "system_power_state"),
    "work_mode": ("system_arbeitsmodus", "system_work_mode"),
    "work_state": ("system_arbeitszustand", "system_work_state"),
    "grid_status": ("system_netzstatus", "system_grid_status"),
    "recommended_action": (
        "empfohlene_backup_aktion",
        "backup_recommended_action",
    ),
}

BINARY_STATUS_SPECS: dict[str, tuple[str, ...]] = {
    "backup_active": ("backup_aktiv", "backup_active"),
    "power_outage": ("stromausfall_erkannt", "power_outage"),
    "backup_reserve_critical": (
        "backup_reserve_kritisch",
        "backup_reserve_critical",
    ),
}

PACK_KIND_MAP = {
    "soc": "soc_percent",
    "soh": "soh_percent",
    "power": "power_w",
    "remaining_energy": "remaining_energy_wh",
    "temperature": "temperature_c",
    "cycles": "cycles",
}


def build_anonymized_observation(
    states: Iterable[Mapping[str, Any]],
    *,
    source_id: str,
    observed_at: str | None = None,
    repo: str | None = None,
    integration_version: str | None = None,
    home_assistant_version: str | None = None,
    device_model: str | None = None,
    battery_packs: int | None = None,
    settings: Mapping[str, Any] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Baut ein anonymisiertes Beobachtungspaket aus HA-State-Daten.

    Beispiel:
        Input: `sensor...netz_leistung = -761.6`
        Output: `measurements.power.grid_power_w = -761.6`
    """
    state_index = _index_states(states)
    values, sources = _extract_field_values(state_index)
    battery = _extract_battery_values(state_index, sources)
    status = _extract_status_values(state_index, sources)
    source: dict[str, Any] = {"source_id": _sanitize_source_id(source_id)}

    for key, value in (
        ("repo", repo),
        ("integration_version", integration_version),
        ("home_assistant_version", home_assistant_version),
        ("device_model", device_model),
    ):
        if value:
            source[key] = _sanitize_text(str(value))
    if battery_packs is not None:
        source["battery_packs"] = int(battery_packs)

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "observed_at": observed_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": source,
        "measurements": _compact_dict(
            {
                "power": values.get("power", {}),
                "energy": values.get("energy", {}),
                "battery": battery,
                "backup": values.get("backup", {}),
                "daily_report": values.get("daily_report", {}),
                "system_limits": values.get("system_limits", {}),
            }
        ),
        "status": status,
        "privacy": {
            "redaction_level": "entity_id_suffix_only",
            "removed": [
                "serial_numbers",
                "tokens",
                "email_addresses",
                "full_entity_ids",
                "locations",
            ],
        },
    }

    if settings:
        result["settings"] = _sanitize_mapping(settings)
    if notes:
        result["notes"] = _sanitize_text(notes)
    if sources:
        result["entity_sources"] = sources

    return _compact_dict(result)


def json_dumps(value: Any) -> str:
    """Serialisiert stabil und lesbar für Tests, Issues und Pull Requests."""
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _index_states(
    states: Iterable[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """Indiziert HA-State-Objekte nach anonymisierten Entity-Suffixen."""
    index: dict[str, Mapping[str, Any]] = {}
    for item in states:
        entity_id = str(item.get("entity_id", ""))
        suffix = _entity_suffix(entity_id)
        if not suffix:
            continue
        index.setdefault(suffix, item)
    return index


def _extract_field_values(
    state_index: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, str]]]:
    """Extrahiert numerische Felder anhand bekannter Suffixe."""
    values: dict[str, dict[str, float]] = {}
    sources: dict[str, dict[str, str]] = {}
    for (section, field), suffixes in FIELD_SPECS.items():
        state, matched_suffix = _find_state_by_suffix(state_index, suffixes)
        number = _state_float(state)
        if state is None or number is None:
            continue
        values.setdefault(section, {})[field] = number
        sources[f"{section}.{field}"] = _source_hint(state, matched_suffix)
    return values, sources


def _extract_battery_values(
    state_index: Mapping[str, Mapping[str, Any]],
    sources: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Sammelt Gesamt-SOC und Batteriepackdaten."""
    battery: dict[str, Any] = {}
    total_soc_state, total_soc_suffix = _find_state_by_suffix(
        state_index,
        ("gesamt_ladestand", "total_soc"),
    )
    total_soc = _state_float(total_soc_state)
    if total_soc is not None:
        battery["total_soc_percent"] = total_soc
        sources["battery.total_soc_percent"] = _source_hint(
            total_soc_state,
            total_soc_suffix,
        )

    total_power_state, total_power_suffix = _find_state_by_suffix(
        state_index,
        ("batterie_gesamtleistung", "battery_total_power"),
    )
    total_power = _state_float(total_power_state)
    if total_power is not None:
        battery["total_power_w"] = total_power
        sources["battery.total_power_w"] = _source_hint(
            total_power_state,
            total_power_suffix,
        )

    packs: dict[int, dict[str, Any]] = {}
    for suffix, state in state_index.items():
        match = PACK_SENSOR_RE.search(suffix)
        if not match:
            continue
        value = _state_float(state)
        if value is None:
            continue
        index = int(match.group("index"))
        output_key = PACK_KIND_MAP[match.group("kind")]
        packs.setdefault(index, {"index": index})[output_key] = value
        sources[f"battery.packs.{index}.{output_key}"] = _source_hint(state, suffix)

    if packs:
        battery["packs"] = [packs[index] for index in sorted(packs)]
    return battery


def _extract_status_values(
    state_index: Mapping[str, Mapping[str, Any]],
    sources: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Extrahiert String- und Binary-Statuswerte."""
    status: dict[str, Any] = {}
    for field, suffixes in STATUS_SPECS.items():
        state, matched_suffix = _find_state_by_suffix(state_index, suffixes)
        raw_state = _state_value(state)
        if raw_state is None:
            continue
        status[field] = _sanitize_text(raw_state)
        sources[f"status.{field}"] = _source_hint(state, matched_suffix)

    for field, suffixes in BINARY_STATUS_SPECS.items():
        state, matched_suffix = _find_state_by_suffix(state_index, suffixes)
        raw_state = _state_value(state)
        if raw_state is None:
            continue
        status[field] = raw_state == "on"
        sources[f"status.{field}"] = _source_hint(state, matched_suffix)
    return status


def _find_state_by_suffix(
    state_index: Mapping[str, Mapping[str, Any]],
    suffixes: Iterable[str],
) -> tuple[Mapping[str, Any] | None, str | None]:
    """Findet den ersten State, dessen anonymisierter Suffix passt."""
    normalized_suffixes = tuple(_normalize_suffix(suffix) for suffix in suffixes)
    for suffix in normalized_suffixes:
        if suffix in state_index:
            return state_index[suffix], suffix
    for existing_suffix, state in state_index.items():
        if any(existing_suffix.endswith(suffix) for suffix in normalized_suffixes):
            return state, existing_suffix
    return None, None


def _source_hint(
    state: Mapping[str, Any] | None,
    matched_suffix: str | None,
) -> dict[str, str]:
    """Gibt eine hilfreiche, aber anonymisierte Quellenbeschreibung zurück."""
    entity_id = str(state.get("entity_id", "")) if state is not None else ""
    domain = entity_id.split(".", 1)[0] if "." in entity_id else "unknown"
    return {
        "domain": domain,
        "matched_suffix": matched_suffix or "unknown",
    }


def _entity_suffix(entity_id: str) -> str:
    """Reduziert Entity-IDs auf nicht-personalisierte EcoFlow-Suffixe."""
    if "." not in entity_id:
        return ""
    domain, object_id = entity_id.split(".", 1)
    if domain not in {"sensor", "binary_sensor", "button", "switch", "update"}:
        return ""

    normalized = _normalize_suffix(object_id)
    marker = "ecoflow_powerocean_plus_"
    if marker in normalized:
        return normalized.split(marker, 1)[1]
    return normalized


def _state_value(state: Mapping[str, Any] | None) -> str | None:
    """Liest den State als String, wenn er sinnvoll verfügbar ist."""
    if state is None:
        return None
    value = state.get("state")
    if value in (None, "", "unknown", "unavailable"):
        return None
    return str(value)


def _state_float(state: Mapping[str, Any] | None) -> float | None:
    """Liest einen numerischen State robust."""
    value = _state_value(state)
    if value is None:
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def _sanitize_mapping(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Sanitisiert flache Settings-Mappings."""
    result: dict[str, Any] = {}
    for key, value in raw.items():
        clean_key = _normalize_suffix(str(key))
        if isinstance(value, (int, float, bool)) or value is None:
            result[clean_key] = value
        else:
            result[clean_key] = _sanitize_text(str(value))
    return result


def _sanitize_source_id(value: str) -> str:
    """Macht eine Quellen-ID teilbar, ohne Personenbezug zu erzwingen."""
    clean = _normalize_suffix(_sanitize_text(value)).strip("_")
    return clean or "anonymous-sample"


def _sanitize_text(value: str) -> str:
    """Entfernt bekannte sensible Muster aus Freitext."""
    value = SERIAL_RE.sub("<redacted-serial>", value)
    value = TOKEN_LIKE_RE.sub("<redacted-token>", value)
    return value


def _normalize_suffix(value: str) -> str:
    """Normalisiert IDs auf stabile ASCII-Suffixe."""
    value = value.lower().replace("ä", "a").replace("ö", "o").replace("ü", "u")
    value = value.replace("ß", "ss")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_")


def _compact_dict(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Entfernt leere Dicts/Listen/None-Werte rekursiv."""
    result: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, Mapping):
            compact = _compact_dict(value)
            if compact:
                result[key] = compact
        elif isinstance(value, list):
            compact_list = [
                _compact_dict(item) if isinstance(item, Mapping) else item
                for item in value
            ]
            compact_list = [item for item in compact_list if item not in ({}, None)]
            if compact_list:
                result[key] = compact_list
        elif value is not None:
            result[key] = value
    return result


def _load_states_from_file(path: str) -> list[Mapping[str, Any]]:
    """Lädt HA-State-JSON aus einer Datei."""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("--states muss eine JSON-Liste aus /api/states enthalten")
    return data


def _load_states_from_home_assistant(ha_url: str) -> list[Mapping[str, Any]]:
    """Lädt `/api/states` aus Home Assistant mit Token aus der Umgebung."""
    token = os.environ.get("HOMEASSISTANT_TOKEN")
    if not token:
        raise ValueError(
            "HOMEASSISTANT_TOKEN fehlt. Token als Umgebungsvariable setzen, "
            "nicht im Befehl oder in Dateien speichern."
        )
    url = ha_url.rstrip("/") + "/api/states"
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    if not isinstance(data, list):
        raise ValueError("Home Assistant hat keine State-Liste geliefert")
    return data


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Definiert die CLI ohne externe Abhängigkeiten."""
    parser = argparse.ArgumentParser(
        description="Anonymisierten EcoFlow-PowerOcean-Messdaten-Snapshot bauen.",
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--states", help="Pfad zu einer /api/states-JSON-Datei")
    source_group.add_argument("--ha-url", help="Home-Assistant-URL, z. B. http://homeassistant.local:8123")
    parser.add_argument("--source-id", required=True, help="Anonyme Quellen-ID")
    parser.add_argument("--repo", default="Feberdin/ecoflow-powerocean-ha")
    parser.add_argument("--integration-version")
    parser.add_argument("--home-assistant-version")
    parser.add_argument("--device-model", default="PowerOcean Plus")
    parser.add_argument("--battery-packs", type=int)
    parser.add_argument("--backup-reserved-soc-percent", type=int)
    parser.add_argument("--reserve-guard-enabled", action="store_true")
    parser.add_argument("--reserve-guard-restart-margin-percent", type=int)
    parser.add_argument("--notes")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI-Einstiegspunkt."""
    args = _parse_args(argv or sys.argv[1:])
    try:
        states = (
            _load_states_from_file(args.states)
            if args.states
            else _load_states_from_home_assistant(args.ha_url)
        )
        settings = {
            key: value
            for key, value in {
                "backup_reserved_soc_percent": args.backup_reserved_soc_percent,
                "system_power_reserve_guard_enabled": args.reserve_guard_enabled,
                "system_power_reserve_restart_margin_percent": (
                    args.reserve_guard_restart_margin_percent
                ),
            }.items()
            if value is not None
        }
        result = build_anonymized_observation(
            states,
            source_id=args.source_id,
            repo=args.repo,
            integration_version=args.integration_version,
            home_assistant_version=args.home_assistant_version,
            device_model=args.device_model,
            battery_packs=args.battery_packs,
            settings=settings,
            notes=args.notes,
        )
    except Exception as exc:
        print(f"Fehler: anonymisierter Export fehlgeschlagen: {exc}", file=sys.stderr)
        return 1

    print(json_dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
