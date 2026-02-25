"""
Protobuf-Decoder für EcoFlow PowerOcean Plus MQTT-Nachrichten.

EcoFlow verwendet ein proprietäres Protokoll auf Basis von Protocol Buffers (Protobuf).
Alle MQTT-Nachrichten folgen diesem Schema:

    HeaderMessage
    └── repeated Header
        ├── pdata      (bytes)   — innere Nutzdaten (weiteres Protobuf-Objekt)
        ├── cmd_func   (int32)   — Nachrichtenklasse (z. B. 96 = EMS)
        ├── cmd_id     (int32)   — Nachrichtentyp innerhalb der Klasse
        ├── enc_type   (int32)   — 1 = XOR-verschlüsselt, 0 = unverschlüsselt
        └── seq        (int32)   — XOR-Schlüssel (nur niedrigstes Byte = seq & 0xFF)

Wichtige Nachrichtentypen (cmdFunc=96):
    cmdId=7  → JTS1_BP_STA_REPORT       — Batterie-Pack-Status (SOC, SOH, Temp., …)
    cmdId=33 → JTS1_ENERGY_STREAM_REPORT — Energiefluss (Grid, Solar, Last, Gesamt-SOC)
    cmdId=1  → JTS1_EMS_HEARTBEAT        — Wechselrichter / 3-Phasen-Daten

Dieses Modul enthält einen vollständig in reinem Python geschriebenen Decoder,
der ohne externe Protobuf-Bibliotheken auskommt. Das macht die Installation
einfacher und reduziert Abhängigkeiten auf ein Minimum.

Quellen und Referenzen:
    - Protobuf-Wire-Format: https://protobuf.dev/programming-guides/encoding/
    - Reverse-Engineering: foxthefox/ioBroker.ecoflow-mqtt (MIT Lizenz)
    - Feldnamen: tolwi/hassio-ecoflow-cloud (MIT Lizenz)
"""

from __future__ import annotations

import base64
import logging
import struct
from dataclasses import dataclass, field
from typing import Any

_LOGGER = logging.getLogger(__name__)


# ── Datenklassen für dekodierte Nachrichten ───────────────────────────────────

@dataclass
class BatteryPackData:
    """
    Zustandsdaten eines einzelnen Batterie-Packs (JTS1_BP_STA_REPORT).

    Zwei Packs bilden zusammen die 10 kWh Gesamtkapazität der Anlage.
    Der Index `pack_index` entspricht dem `bpDsrc`-Feld in der Protobuf-Nachricht
    und identifiziert den physischen Steckplatz im Gerät.
    """
    pack_index: int = 0
    """Batterie-Pack-Index (1 = erste Batterie, 2 = zweite Batterie, …)."""

    serial_number: str = ""
    """Seriennummer des Batterie-Packs."""

    soc: int = 0
    """State of Charge — Ladestand in Prozent (0–100)."""

    real_soc: float = 0.0
    """Präziser Ladestand mit Nachkommastellen (genauer als ganzzahliger SOC)."""

    soh: int = 0
    """State of Health — Gesundheitszustand in Prozent (0–100). 100 % = neuwertig."""

    power_w: float = 0.0
    """Aktuelle Leistung in Watt. Positiv = Entladen, Negativ = Laden."""

    voltage_v: float = 0.0
    """Klemmenspannung des Packs in Volt."""

    current_a: float = 0.0
    """Ladestrom (+) bzw. Entladestrom (-) in Ampere."""

    remaining_wh: float = 0.0
    """Verbleibende nutzbare Energie in Wattstunden."""

    cycles: int = 0
    """Anzahl abgeschlossener Ladezyklen."""

    temperature_env_c: float = 0.0
    """Umgebungstemperatur des Packs in Grad Celsius."""

    temperature_mos_c: float = 0.0
    """MOSFET-Temperatur (Hochspannungsseite) in Grad Celsius."""

    is_charging: bool = False
    """True wenn das Pack gerade geladen wird, False wenn entladen."""


@dataclass
class EnergyStreamData:
    """
    Systemweiter Energiefluss (JTS1_ENERGY_STREAM_REPORT).

    Diese Nachricht liefert eine Zusammenfassung aller Leistungsflüsse
    im System und den kombinierten Batterie-SOC.
    """
    load_w: float = 0.0
    """Aktuelle Hausverbrauchsleistung in Watt."""

    grid_w: float = 0.0
    """
    Netzleistung in Watt.
    Positiv = Netzbezug (Strom wird vom Netz gekauft).
    Negativ = Netzeinspeisung (Strom wird ins Netz verkauft).
    """

    solar_w: float = 0.0
    """Aktuelle Solarertrag in Watt (Summe aller MPPT-Strings)."""

    battery_w: float = 0.0
    """
    Batteriegesamtleistung in Watt.
    Positiv = Entladen, Negativ = Laden.
    """

    soc: int = 0
    """Kombinierter Batterie-Ladestand aller Packs in Prozent."""


# ── Protobuf Wire-Format Decoder ──────────────────────────────────────────────

def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """
    Liest eine variable-length Integer (Varint) aus dem Byte-Array.

    Varints sind das Standard-Encoding für int32/int64/bool in Protobuf.
    Jedes Byte trägt 7 Datenbits; das höchste Bit zeigt an, ob weitere Bytes folgen.

    Args:
        data: Rohe Byte-Daten.
        pos:  Startposition im Array.

    Returns:
        Tuple aus (dekodierter Wert, neue Position nach dem Varint).
    """
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return result, pos


def _decode_fields(data: bytes) -> dict[int, list[Any]]:
    """
    Dekodiert rohe Protobuf-Bytes in ein Dictionary {Feldnummer: [Werte]}.

    Jedes Feld kann mehrfach vorkommen (repeated fields → Liste).
    Wire-Typen:
        0 = Varint (int32, int64, bool, enum)
        1 = 64-bit (double, fixed64)
        2 = Length-delimited (string, bytes, embedded message, packed repeated)
        5 = 32-bit (float, fixed32)

    Args:
        data: Rohe Protobuf-Bytes eines Nachrichtenobjekts.

    Returns:
        Dictionary mit Feldnummern als Schlüssel und Listen von Werten.
    """
    fields: dict[int, list[Any]] = {}
    pos = 0

    while pos < len(data):
        try:
            tag, pos = _read_varint(data, pos)
        except IndexError:
            break

        field_num = tag >> 3
        wire_type = tag & 0x07

        try:
            if wire_type == 0:  # Varint
                value, pos = _read_varint(data, pos)
            elif wire_type == 1:  # 64-bit
                if pos + 8 > len(data):
                    break
                value = struct.unpack_from("<q", data, pos)[0]
                pos += 8
            elif wire_type == 2:  # Length-delimited
                length, pos = _read_varint(data, pos)
                if pos + length > len(data):
                    break
                value = data[pos: pos + length]
                pos += length
            elif wire_type == 5:  # 32-bit (float)
                if pos + 4 > len(data):
                    break
                value = struct.unpack_from("<f", data, pos)[0]
                pos += 4
            else:
                _LOGGER.debug("Unbekannter Wire-Typ %d bei Feld %d", wire_type, field_num)
                break
        except (struct.error, IndexError) as exc:
            _LOGGER.debug("Fehler beim Dekodieren von Feld %d: %s", field_num, exc)
            break

        fields.setdefault(field_num, []).append(value)

    return fields


def _get_float(fields: dict, field_num: int, default: float = 0.0) -> float:
    """Gibt den ersten Float-Wert eines Feldes zurück, oder den Default-Wert."""
    vals = fields.get(field_num)
    if vals:
        try:
            return float(vals[0])
        except (TypeError, ValueError):
            pass
    return default


def _get_int(fields: dict, field_num: int, default: int = 0) -> int:
    """Gibt den ersten Integer-Wert eines Feldes zurück, oder den Default-Wert."""
    vals = fields.get(field_num)
    if vals:
        try:
            return int(vals[0])
        except (TypeError, ValueError):
            pass
    return default


def _get_bytes(fields: dict, field_num: int) -> bytes:
    """Gibt die ersten Bytes eines Feldes zurück, oder leere Bytes."""
    vals = fields.get(field_num)
    return bytes(vals[0]) if vals else b""


def _get_string(fields: dict, field_num: int, default: str = "") -> str:
    """Gibt den ersten String-Wert eines Feldes zurück, oder den Default-Wert."""
    vals = fields.get(field_num)
    if vals and isinstance(vals[0], (bytes, bytearray)):
        try:
            return vals[0].decode("utf-8")
        except UnicodeDecodeError:
            pass
    return default


# ── XOR-Entschlüsselung ───────────────────────────────────────────────────────

def _xor_decrypt(pdata: bytes, seq: int) -> bytes:
    """
    Entschlüsselt XOR-verschlüsselte Protobuf-Nutzdaten.

    EcoFlow verwendet eine einfache XOR-Verschlüsselung, wenn enc_type == 1.
    Jedes Byte der Nutzdaten wird mit dem niedrigsten Byte des `seq`-Wertes
    XOR-verknüpft. Das Verfahren ist symmetrisch (Entschlüsseln = erneut XOR).

    Args:
        pdata:  Verschlüsselte Byte-Daten.
        seq:    Sequenznummer aus dem Header; das niedrigste Byte ist der XOR-Schlüssel.

    Returns:
        Entschlüsselte Byte-Daten.
    """
    key = seq & 0xFF
    return bytes(b ^ key for b in pdata)


# ── Spezifische Nachrichts-Decoder ────────────────────────────────────────────

def _decode_bp_sta_report(pdata: bytes) -> list[BatteryPackData]:
    """
    Dekodiert eine JTS1_BP_STA_REPORT-Nachricht (cmdFunc=96, cmdId=7).

    Diese Nachricht enthält den Zustand aller Batterie-Packs. Für jeden
    Pack gibt es ein `bpSta`-Feld (Feldnummer 1), das seinerseits ein
    eingebettetes Protobuf-Objekt mit den Pack-Daten enthält.

    Feldnummern in bpStaReport:
        1=bpPwr, 2=bpSoc, 3=bpSoh, 9=bpVol, 10=bpAmp, 15=bpDsrc,
        16=bpSn, 17=bpCycles, 19=bpHvMosTemp, 25=bpEnvTemp,
        38=bpRealSoc, 50=bmsChgDsgSta, 54=bpRemainWatth

    Args:
        pdata: Entschlüsselte innere Nutzdaten des Headers.

    Returns:
        Liste von BatteryPackData-Objekten (ein Eintrag pro erkanntem Pack).
    """
    outer = _decode_fields(pdata)
    packs: list[BatteryPackData] = []

    for raw_pack in outer.get(1, []):  # Feld 1 = repeated bpSta
        if not isinstance(raw_pack, (bytes, bytearray)):
            continue
        f = _decode_fields(bytes(raw_pack))

        # Seriennummer: base64-kodiert im Feld 16
        sn_raw = _get_string(f, 16)
        try:
            serial_number = base64.b64decode(sn_raw).decode("utf-8")
        except Exception:
            serial_number = sn_raw

        pack = BatteryPackData(
            pack_index=_get_int(f, 15),          # bpDsrc  → Pack-Nummer
            serial_number=serial_number,
            soc=_get_int(f, 2),                  # bpSoc   → %
            real_soc=_get_float(f, 38),           # bpRealSoc → % (präzise)
            soh=_get_int(f, 3),                  # bpSoh   → %
            power_w=_get_float(f, 1),            # bpPwr   → W
            voltage_v=_get_float(f, 9),          # bpVol   → V
            current_a=_get_float(f, 10),         # bpAmp   → A
            remaining_wh=_get_float(f, 54),      # bpRemainWatth → Wh
            cycles=_get_int(f, 17),              # bpCycles
            temperature_env_c=_get_float(f, 25), # bpEnvTemp → °C
            temperature_mos_c=_get_float(f, 19), # bpHvMosTemp → °C
            is_charging=(_get_int(f, 50) == 1),  # bmsChgDsgSta: 1=Laden, 0=Entladen
        )
        if pack.pack_index > 0:
            packs.append(pack)

    return packs


def _decode_energy_stream(pdata: bytes) -> EnergyStreamData | None:
    """
    Dekodiert eine JTS1_ENERGY_STREAM_REPORT-Nachricht (cmdFunc=96, cmdId=33).

    Diese Nachricht liefert eine kompakte Zusammenfassung aller Energieflüsse
    im System. Sie wird ca. alle 5–30 Sekunden vom Gerät gesendet.

    Feldnummern:
        1=sysLoadPwr (W), 2=sysGridPwr (W), 3=mpptPwr (W),
        4=bpPwr (W), 5=bpSoc (%)

    Args:
        pdata: Entschlüsselte innere Nutzdaten des Headers.

    Returns:
        EnergyStreamData-Objekt oder None bei Dekodierungsfehler.
    """
    try:
        f = _decode_fields(pdata)
        return EnergyStreamData(
            load_w=_get_float(f, 1),    # sysLoadPwr
            grid_w=_get_float(f, 2),    # sysGridPwr
            solar_w=_get_float(f, 3),   # mpptPwr
            battery_w=_get_float(f, 4), # bpPwr
            soc=_get_int(f, 5),         # bpSoc
        )
    except Exception as exc:
        _LOGGER.warning("Fehler beim Dekodieren von ENERGY_STREAM_REPORT: %s", exc)
        return None


# ── Haupt-Einstiegspunkt ──────────────────────────────────────────────────────

def decode_mqtt_payload(
    raw: bytes,
) -> tuple[list[BatteryPackData], EnergyStreamData | None]:
    """
    Dekodiert eine rohe MQTT-Payload vom Topic /app/device/property/{SN}.

    Dies ist der zentrale Einstiegspunkt des Decoders. Er:
    1. Parst das äußere HeaderMessage-Envelope
    2. Iteriert über alle enthaltenen Header
    3. Entschlüsselt bei Bedarf die inneren Nutzdaten (XOR bei enc_type==1)
    4. Dispatcht die Dekodierung anhand von cmdFunc und cmdId
    5. Gibt strukturierte Datenklassen zurück

    Args:
        raw: Rohe Bytes der MQTT-Nachricht.

    Returns:
        Tuple aus:
        - Liste erkannter BatteryPackData-Objekte (kann mehrere Packs enthalten)
        - EnergyStreamData oder None (falls nicht in dieser Nachricht enthalten)
    """
    battery_packs: list[BatteryPackData] = []
    energy_stream: EnergyStreamData | None = None

    try:
        # Äußeres Envelope: HeaderMessage = { repeated Header header = 1; }
        outer = _decode_fields(raw)
        headers_raw = outer.get(1, [])
    except Exception as exc:
        _LOGGER.error("Fehler beim Parsen des MQTT-Envelopes: %s", exc)
        return [], None

    for raw_header in headers_raw:
        if not isinstance(raw_header, (bytes, bytearray)):
            continue
        try:
            h = _decode_fields(bytes(raw_header))

            cmd_func = _get_int(h, 8)   # cmd_func
            cmd_id   = _get_int(h, 9)   # cmd_id
            enc_type = _get_int(h, 6)   # enc_type
            seq      = _get_int(h, 14)  # seq (XOR-Schlüssel)
            pdata    = _get_bytes(h, 1)  # pdata (innere Nutzdaten)

            if not pdata:
                continue

            # XOR-Entschlüsselung wenn nötig
            if enc_type == 1:
                pdata = _xor_decrypt(pdata, seq)

            # Dispatch nach Nachrichtentyp
            if cmd_func == 96 and cmd_id == 7:
                # JTS1_BP_STA_REPORT — Batterie-Pack-Status
                packs = _decode_bp_sta_report(pdata)
                battery_packs.extend(packs)

            elif cmd_func == 96 and cmd_id == 33:
                # JTS1_ENERGY_STREAM_REPORT — Energiefluss
                energy_stream = _decode_energy_stream(pdata)

            # Weitere Typen (EMS_HEARTBEAT etc.) können hier ergänzt werden

        except Exception as exc:
            _LOGGER.debug("Fehler beim Verarbeiten eines Headers: %s", exc)
            continue

    return battery_packs, energy_stream
