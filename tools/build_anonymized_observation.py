#!/usr/bin/env python3
"""
Anonymisierten EcoFlow-PowerOcean-Messdaten-Snapshot bauen.

Zweck:
    Erstellt aus Home-Assistant-State-Daten ein reiches, aber teilbares JSON für
    Analyse-Issues und externe Repos.

Input:
    - Entweder eine Datei mit `/api/states`-JSON (`--states`)
    - oder ein Home-Assistant-URL mit Token aus `HOMEASSISTANT_TOKEN`
    - optional monatliche Recorder-/Langzeitstatistiken

Output:
    - JSON auf stdout im Format `powerocean-observation.schema.json`

Wichtige Invarianten:
    - Keine Seriennummern, Tokens, E-Mail-Adressen oder vollständigen Entity-IDs
      aus Home Assistant werden ausgegeben.
    - Entity-Quellen werden nur als Domain plus generischer Suffix dokumentiert.
    - EcoFlow-/PowerOcean-Rohzustände werden breit, aber ohne vollständige
      Entity-IDs und ohne `friendly_name` exportiert.
    - Das Script verwendet ausschließlich die Python-Standardbibliothek.

Debug-Hinweis:
    - Lokale Datei prüfen:
      `python3 tools/build_anonymized_observation.py --states states.json --source-id sample-a`
    - Direkt aus HA lesen:
      `HOMEASSISTANT_TOKEN=... python3 tools/build_anonymized_observation.py --ha-url http://homeassistant.local:8123 --source-id sample-a`
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime
import hashlib
import json
import os
import socket
import re
import ssl
import struct
import sys
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse
import urllib.request


SCHEMA_VERSION = 3

SERIAL_RE = re.compile(r"\bR[0-9A-Z]{8,}\b", re.IGNORECASE)
TOKEN_LIKE_RE = re.compile(r"\b[A-Za-z0-9_-]{32,}\b")
EMAIL_RE = re.compile(r"\b[^@\s]+@[^@\s]+\.[^@\s]+\b")
PACK_SENSOR_RE = re.compile(
    r"(?:^|_)battery_(?P<index>[1-9])_(?P<kind>soc|soh|power|remaining_energy|temperature|cycles)$"
)

ALLOWED_DOMAINS = {"sensor", "binary_sensor", "button", "switch", "update"}
POWEROCEAN_MARKERS = (
    "ecoflow_powerocean_plus_",
    "ecoflow_powerocean_",
    "powerocean_plus_",
    "powerocean_",
)
SAFE_ATTRIBUTE_KEYS = {
    "device_class",
    "entity_category",
    "icon",
    "state_class",
    "unit_of_measurement",
}
SENSITIVE_KEY_PARTS = (
    "address",
    "auth",
    "cookie",
    "email",
    "gps",
    "key",
    "latitude",
    "location",
    "longitude",
    "mail",
    "mqtt",
    "password",
    "secret",
    "serial",
    "seriennummer",
    "token",
)
PURPOSE = {
    "consent": "voluntary_user_supplied",
    "allowed_use": "improve_ecoflow_powerocean_integration_and_analysis_only",
    "not_allowed": [
        "identify_user",
        "publish_raw_home_assistant_dump",
        "use_for_advertising_or_tracking",
    ],
}

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
    statistics: Mapping[str, Any] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Baut ein anonymisiertes Beobachtungspaket aus HA-State-Daten.

    Beispiel:
        Input: `sensor...netz_leistung = -761.6`
        Output: `measurements.power.grid_power_w = -761.6`
    """
    state_list = list(states)
    state_index = _index_states(state_list)
    values, sources = _extract_field_values(state_index)
    battery = _extract_battery_values(state_index, sources)
    status = _extract_status_values(state_index, sources)
    anonymized_entities = _extract_anonymized_entities(state_list)
    sanitized_statistics = _sanitize_recorder_statistics(statistics or {})
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
        "purpose": PURPOSE,
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
        "anonymized_entities": anonymized_entities,
        "statistics": sanitized_statistics,
        "privacy": {
            "redaction_level": "entity_id_suffix_only",
            "state_redaction": "sensitive_values_redacted",
            "removed": [
                "serial_numbers",
                "tokens",
                "email_addresses",
                "full_entity_ids",
                "locations",
                "display_names",
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


def _extract_anonymized_entities(
    states: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """
    Exportiert alle erkennbaren EcoFlow-/PowerOcean-States anonymisiert.

    Warum:
        Für neue Sensoren oder Fremdrepos kennen wir die Felder oft noch nicht.
        Dieser Block behält deshalb die technische Form der Daten, entfernt aber
        nutzerspezifische Namen und vollständige Entity-IDs.
    """
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for state in states:
        entity_id = str(state.get("entity_id", ""))
        suffix = _safe_observation_suffix(state)
        if not suffix:
            continue
        domain = entity_id.split(".", 1)[0] if "." in entity_id else "unknown"
        key = (domain, suffix)
        if key in seen:
            continue
        seen.add(key)
        raw_state = state.get("state")
        item: dict[str, Any] = {
            "domain": domain,
            "suffix": suffix,
            "state": _sanitize_state_for_export(raw_state, suffix),
        }
        attributes = _sanitize_attributes(state.get("attributes", {}))
        if attributes:
            item["attributes"] = attributes
        result.append(item)
    return sorted(result, key=lambda item: (item["domain"], item["suffix"]))


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
    if domain not in ALLOWED_DOMAINS:
        return ""

    normalized = _normalize_suffix(object_id)
    marker_suffix = _suffix_after_powerocean_marker(normalized)
    if marker_suffix:
        return marker_suffix
    return normalized


def _safe_observation_suffix(state: Mapping[str, Any]) -> str | None:
    """
    Ermittelt einen teilbaren Entity-Suffix für den breiten Rohzustandsblock.

    Ohne EcoFlow-/PowerOcean-Marker wird nur dann exportiert, wenn das Objekt auf
    einen bekannten PowerOcean-Suffix endet. Dadurch landet z. B. keine
    Küchen-Temperatur im Datensatz.
    """
    entity_id = str(state.get("entity_id", ""))
    if "." not in entity_id:
        return None
    domain, object_id = entity_id.split(".", 1)
    if domain not in ALLOWED_DOMAINS:
        return None

    normalized = _normalize_suffix(object_id)
    marker_suffix = _suffix_after_powerocean_marker(normalized)
    if marker_suffix:
        return marker_suffix

    known_suffix = _known_suffix_match(normalized)
    if known_suffix:
        return known_suffix
    return None


def _suffix_after_powerocean_marker(normalized_object_id: str) -> str | None:
    """Schneidet nutzerspezifische Prefixe vor dem PowerOcean-Marker ab."""
    for marker in POWEROCEAN_MARKERS:
        if marker in normalized_object_id:
            suffix = normalized_object_id.split(marker, 1)[1]
            return suffix or None
    return None


def _known_suffix_match(normalized_object_id: str) -> str | None:
    """Findet bekannte, nicht-personalisierte PowerOcean-Suffixe."""
    candidates: set[str] = set()
    for suffixes in FIELD_SPECS.values():
        candidates.update(_normalize_suffix(suffix) for suffix in suffixes)
    for suffixes in STATUS_SPECS.values():
        candidates.update(_normalize_suffix(suffix) for suffix in suffixes)
    for suffixes in BINARY_STATUS_SPECS.values():
        candidates.update(_normalize_suffix(suffix) for suffix in suffixes)
    candidates.update({"gesamt_ladestand", "total_soc", "batterie_gesamtleistung"})
    for candidate in sorted(candidates, key=len, reverse=True):
        if normalized_object_id == candidate or normalized_object_id.endswith(f"_{candidate}"):
            return candidate
    pack_match = PACK_SENSOR_RE.search(normalized_object_id)
    if pack_match:
        return pack_match.group(0).strip("_")
    return None


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
    value = EMAIL_RE.sub("<redacted-email>", value)
    value = SERIAL_RE.sub("<redacted-serial>", value)
    value = TOKEN_LIKE_RE.sub("<redacted-token>", value)
    return value


def _sanitize_state_for_export(value: Any, key_hint: str) -> Any:
    """Sanitisiert einen State-Wert für den anonymisierten Rohzustandsblock."""
    if value is None:
        return None
    text = str(value)
    if _is_sensitive_key(key_hint) or _contains_sensitive_text(text):
        return "<redacted>"
    number = _parse_float(text)
    if number is not None:
        return number
    return _sanitize_text(text)


def _sanitize_attributes(raw: Any) -> dict[str, Any]:
    """Übernimmt nur technische, nicht-personalisierte Attribute."""
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key in sorted(SAFE_ATTRIBUTE_KEYS):
        if key not in raw:
            continue
        value = raw[key]
        if value is None:
            continue
        clean_key = _normalize_suffix(str(key))
        if isinstance(value, (int, float, bool)):
            result[clean_key] = value
            continue
        text = str(value)
        result[clean_key] = "<redacted>" if _contains_sensitive_text(text) else _sanitize_text(text)
    return result


def _sanitize_recorder_statistics(
    statistics: Mapping[str, Any],
) -> dict[str, Any]:
    """Bereitet optionale HA-Langzeitstatistiken anonymisiert auf."""
    monthly: dict[str, Any] = {}
    for statistic_id, payload in statistics.items():
        suffix = _safe_suffix_from_statistic_id(str(statistic_id))
        if not suffix:
            continue
        rows, unit = _statistics_rows_and_unit(payload)
        values: list[dict[str, Any]] = []
        previous_total: float | None = None
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            period = _statistics_period(row.get("start") or row.get("start_time"))
            if not period:
                continue
            item: dict[str, Any] = {"period": period}
            total = _first_float(row, ("sum", "state"))
            if total is not None:
                item["sum"] = _round_float(total)
                if previous_total is not None:
                    item["delta"] = _round_float(total - previous_total)
                previous_total = total
            for source_key, output_key in (
                ("mean", "mean"),
                ("min", "min"),
                ("max", "max"),
            ):
                number = _first_float(row, (source_key,))
                if number is not None:
                    item[output_key] = _round_float(number)
            if len(item) > 1:
                values.append(item)
        if values:
            entry: dict[str, Any] = {"values": values}
            if unit:
                entry["unit_of_measurement"] = _sanitize_text(unit)
            monthly[suffix] = entry
    return {"monthly": monthly} if monthly else {}


def _safe_suffix_from_statistic_id(statistic_id: str) -> str | None:
    """Reduziert Statistic-IDs auf denselben anonymisierten Suffix wie States."""
    fake_state = {"entity_id": statistic_id}
    return _safe_observation_suffix(fake_state)


def _statistics_rows_and_unit(payload: Any) -> tuple[list[Any], str | None]:
    """Akzeptiert mehrere einfache Exportformen für Statistikdaten."""
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, Mapping):
        rows = payload.get("rows") or payload.get("values") or []
        if isinstance(rows, list):
            unit = payload.get("unit_of_measurement") or payload.get("unit")
            return rows, str(unit) if unit else None
    return [], None


def _statistics_period(value: Any) -> str | None:
    """Normalisiert HA-Statistikzeitpunkte auf `YYYY-MM`."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000).astimezone().strftime("%Y-%m")
    text = str(value)
    if len(text) >= 7 and re.match(r"^\d{4}-\d{2}", text):
        return text[:7]
    return None


def _first_float(row: Mapping[str, Any], keys: tuple[str, ...]) -> float | None:
    """Liest den ersten numerischen Wert aus mehreren möglichen Keys."""
    for key in keys:
        number = _parse_float(row.get(key))
        if number is not None:
            return number
    return None


def _parse_float(value: Any) -> float | None:
    """Parst Zahlen robust, ohne Exceptions nach außen zu geben."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def _round_float(value: float) -> float:
    """Rundet Statistikwerte stabil für kleine JSON-Diffs."""
    return round(value, 4)


def _is_sensitive_key(key: str) -> bool:
    """Erkennt sensible Schlüssel oder Suffixe."""
    normalized = _normalize_suffix(key)
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _contains_sensitive_text(value: str) -> bool:
    """Erkennt sensible Freitextmuster."""
    return bool(
        EMAIL_RE.search(value)
        or SERIAL_RE.search(value)
        or TOKEN_LIKE_RE.search(value)
    )


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


def _load_monthly_statistics_from_home_assistant(
    ha_url: str,
    *,
    months: int,
) -> dict[str, Any]:
    """
    Lädt EcoFlow-/PowerOcean-Langzeitstatistiken über die HA-WebSocket-API.

    Warum WebSocket:
        Home Assistant stellt Recorder-Langzeitstatistiken über seine interne
        WebSocket-API bereit. Damit bekommen wir Monatswerte, ohne rohe
        hochfrequente Verlaufsdaten zu exportieren.
    """
    token = os.environ.get("HOMEASSISTANT_TOKEN")
    if not token:
        raise ValueError(
            "HOMEASSISTANT_TOKEN fehlt. Token als Umgebungsvariable setzen, "
            "nicht im Befehl oder in Dateien speichern."
        )
    with _SimpleWebSocket(_home_assistant_ws_url(ha_url), timeout=30) as websocket:
        auth_required = websocket.recv_json()
        if auth_required.get("type") != "auth_required":
            raise ValueError("Home Assistant WebSocket hat keine Auth-Anforderung geliefert")
        websocket.send_json({"type": "auth", "access_token": token})
        auth_response = websocket.recv_json()
        if auth_response.get("type") != "auth_ok":
            raise ValueError("Home Assistant WebSocket-Authentifizierung fehlgeschlagen")

        statistic_ids_response = _ha_ws_call(
            websocket,
            1,
            "recorder/list_statistic_ids",
        )
        statistic_ids = _filter_powerocean_statistics(statistic_ids_response.get("result", []))
        if not statistic_ids:
            return {}

        now = datetime.now().astimezone()
        start = _month_start_months_back(now, months)
        stats_response = _ha_ws_call(
            websocket,
            2,
            "recorder/statistics_during_period",
            start_time=start.isoformat(),
            end_time=now.isoformat(),
            statistic_ids=[item["statistic_id"] for item in statistic_ids],
            period="month",
            types=["sum", "state", "mean", "min", "max"],
        )

    unit_by_id = {
        item["statistic_id"]: item.get("unit_of_measurement")
        for item in statistic_ids
    }
    result: dict[str, Any] = {}
    for statistic_id, rows in stats_response.get("result", {}).items():
        if not isinstance(rows, list):
            continue
        result[statistic_id] = {
            "unit_of_measurement": unit_by_id.get(statistic_id),
            "rows": rows,
        }
    return result


def _filter_powerocean_statistics(raw_items: Any) -> list[dict[str, Any]]:
    """Filtert HA-Statistik-IDs auf anonymisierbare PowerOcean-Werte."""
    if not isinstance(raw_items, list):
        return []
    result: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        statistic_id = str(item.get("statistic_id", ""))
        if not _safe_suffix_from_statistic_id(statistic_id):
            continue
        result.append(
            {
                "statistic_id": statistic_id,
                "unit_of_measurement": item.get("unit_of_measurement"),
            }
        )
    return result


def _ha_ws_call(
    websocket: "_SimpleWebSocket",
    message_id: int,
    command_type: str,
    **payload: Any,
) -> Mapping[str, Any]:
    """Sendet einen HA-WebSocket-Befehl und wartet auf die passende Antwort."""
    websocket.send_json({"id": message_id, "type": command_type, **payload})
    while True:
        response = websocket.recv_json()
        if response.get("id") != message_id:
            continue
        if not response.get("success", False):
            raise ValueError(f"Home Assistant WebSocket-Befehl fehlgeschlagen: {command_type}")
        return response


def _home_assistant_ws_url(ha_url: str) -> str:
    """Baut aus der HA-HTTP-URL die WebSocket-URL."""
    if ha_url.startswith("https://"):
        return "wss://" + ha_url[len("https://") :].rstrip("/") + "/api/websocket"
    if ha_url.startswith("http://"):
        return "ws://" + ha_url[len("http://") :].rstrip("/") + "/api/websocket"
    raise ValueError("--ha-url muss mit http:// oder https:// beginnen")


def _month_start_months_back(now: datetime, months: int) -> datetime:
    """Berechnet den Monatsanfang N Monate zurück ohne externe Bibliothek."""
    safe_months = max(1, min(months, 60))
    month_index = now.year * 12 + now.month - 1 - safe_months
    year = month_index // 12
    month = month_index % 12 + 1
    return now.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


class _SimpleWebSocket:
    """Minimaler WebSocket-Client für HA-Textnachrichten ohne Zusatzabhängigkeit."""

    def __init__(self, url: str, *, timeout: int) -> None:
        self._url = url
        self._timeout = timeout
        self._socket: socket.socket | ssl.SSLSocket | None = None

    def __enter__(self) -> "_SimpleWebSocket":
        self._connect()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        if self._socket is not None:
            self._socket.close()

    def send_json(self, payload: Mapping[str, Any]) -> None:
        """Sendet eine JSON-Nachricht als maskierten WebSocket-Textframe."""
        self._send_frame(json.dumps(payload).encode("utf-8"), opcode=0x1)

    def recv_json(self) -> Mapping[str, Any]:
        """Liest eine JSON-Nachricht aus einem WebSocket-Textframe."""
        text = self._recv_text()
        data = json.loads(text)
        if not isinstance(data, Mapping):
            raise ValueError("Home Assistant WebSocket hat kein JSON-Objekt geliefert")
        return data

    def _connect(self) -> None:
        parsed = urlparse(self._url)
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
            raise ValueError(f"Ungültige WebSocket-URL: {self._url}")
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        raw_socket = socket.create_connection((parsed.hostname, port), timeout=self._timeout)
        if parsed.scheme == "wss":
            context = ssl.create_default_context()
            self._socket = context.wrap_socket(raw_socket, server_hostname=parsed.hostname)
        else:
            self._socket = raw_socket
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        host = parsed.hostname if parsed.port is None else f"{parsed.hostname}:{parsed.port}"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._socket.sendall(request.encode("ascii"))
        response = self._read_http_response()
        expected_accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        if " 101 " not in response.split("\r\n", 1)[0]:
            raise ValueError("Home Assistant WebSocket-Handshake fehlgeschlagen")
        if f"sec-websocket-accept: {expected_accept.lower()}" not in response.lower():
            raise ValueError("Home Assistant WebSocket-Handshake konnte nicht validiert werden")

    def _read_http_response(self) -> str:
        chunks: list[bytes] = []
        while b"\r\n\r\n" not in b"".join(chunks):
            chunks.append(self._recv_exact(1))
        return b"".join(chunks).decode("iso-8859-1")

    def _send_frame(self, payload: bytes, *, opcode: int) -> None:
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.extend(struct.pack("!BH", 0x80 | 126, length))
        else:
            header.extend(struct.pack("!BQ", 0x80 | 127, length))
        mask = os.urandom(4)
        masked_payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._send_all(bytes(header) + mask + masked_payload)

    def _recv_text(self) -> str:
        message = bytearray()
        while True:
            fin, opcode, payload = self._recv_frame()
            if opcode == 0x8:
                raise ValueError("Home Assistant WebSocket wurde geschlossen")
            if opcode == 0x9:
                self._send_frame(payload, opcode=0xA)
                continue
            if opcode in {0x1, 0x0}:
                message.extend(payload)
                if fin:
                    return message.decode("utf-8")

    def _recv_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._recv_exact(2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return fin, opcode, payload

    def _recv_exact(self, length: int) -> bytes:
        if self._socket is None:
            raise ValueError("WebSocket ist nicht verbunden")
        chunks = bytearray()
        while len(chunks) < length:
            chunk = self._socket.recv(length - len(chunks))
            if not chunk:
                raise ValueError("WebSocket-Verbindung unerwartet beendet")
            chunks.extend(chunk)
        return bytes(chunks)

    def _send_all(self, payload: bytes) -> None:
        if self._socket is None:
            raise ValueError("WebSocket ist nicht verbunden")
        self._socket.sendall(payload)


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
    parser.add_argument(
        "--include-statistics",
        action="store_true",
        help="Monatliche HA-Langzeitstatistiken über die WebSocket-API mit exportieren",
    )
    parser.add_argument(
        "--statistics-months",
        type=int,
        default=6,
        help="Anzahl Monate für --include-statistics, begrenzt auf 1 bis 60",
    )
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
        statistics = {}
        if args.include_statistics:
            if not args.ha_url:
                raise ValueError("--include-statistics benötigt --ha-url")
            try:
                statistics = _load_monthly_statistics_from_home_assistant(
                    args.ha_url,
                    months=args.statistics_months,
                )
            except Exception as exc:
                print(
                    f"Warnung: Langzeitstatistiken konnten nicht geladen werden: {exc}",
                    file=sys.stderr,
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
            statistics=statistics,
            notes=args.notes,
        )
    except Exception as exc:
        print(f"Fehler: anonymisierter Export fehlgeschlagen: {exc}", file=sys.stderr)
        return 1

    print(json_dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
