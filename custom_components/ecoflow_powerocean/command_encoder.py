"""
Encoder fuer PowerOcean-MQTT-Schreib- und GET-Befehle.

Zweck:
    Dieses Modul baut die wenigen bekannten PowerOcean-Protobuf-Kommandos, die
    wir fuer optionale Steuerfunktionen brauchen. Aktuell ist nur der von einem
    Nutzer reproduzierbar getestete System-AN/AUS-Befehl implementiert.

Input:
    - Seriennummer des PowerOcean
    - Sequenznummer
    - Gewuenschter Zielzustand

Output:
    - Rohe MQTT-Payload-Bytes fuer `thing/property/set` oder `thing/property/get`

Wichtige Invarianten:
    - Keine Home-Assistant-Imports, damit die Logik direkt testbar bleibt.
    - Keine echten Seriennummern oder Mitschnitt-Geheimnisse im Modul.
    - Der Schreibbefehl wird byte-genau aus anonymisierten App-Mitschnitten
      reproduziert. Nicht belegte Steuerbefehle werden hier nicht geraten.

Debug-Hinweis:
    - `tests/test_system_power.py` vergleicht die erzeugten Bytes mit
      anonymisierten App-Mitschnitten.
"""

from __future__ import annotations

from datetime import datetime
from threading import Lock

_CMD_FUNC_EMS = 96
_CMD_ID_SYSTEM_POWER = 105
_SRC = b"android"
_PDATA_OFF = bytes([0x08, 0x01])
_PDATA_ON = b""


def _encode_varint(value: int) -> bytes:
    """Kodiert eine nicht-negative Ganzzahl im Protobuf-Varint-Format."""
    if value < 0:
        raise ValueError("varint darf nicht negativ sein")

    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _tag(field: int, wire_type: int) -> bytes:
    return _encode_varint((field << 3) | wire_type)


def _field_varint(field: int, value: int) -> bytes:
    return _tag(field, 0) + _encode_varint(value)


def _field_bytes(field: int, data: bytes) -> bytes:
    return _tag(field, 2) + _encode_varint(len(data)) + data


def _build_power_header(serial_number: str, seq: int, turn_on: bool) -> bytes:
    """
    Baut den inneren PowerOcean-Header fuer System AN/AUS.

    Warum diese feste Struktur:
        Der Befehl ist aus App-Verkehr reproduziert. Nur Feld 14 (`seq`) aendert
        sich pro Nachricht. Der AUS-Befehl traegt `pdata=08 01`; der AN-Befehl
        laesst `pdata` leer, was dem App-Mitschnitt entspricht.
    """
    pdata = _PDATA_ON if turn_on else _PDATA_OFF
    header = bytearray()

    if pdata:
        header += _field_bytes(1, pdata)

    header += _field_varint(2, 32)
    header += _field_varint(3, 96)
    header += _field_varint(4, 1)
    header += _field_varint(5, 1)
    header += _field_varint(8, _CMD_FUNC_EMS)
    header += _field_varint(9, _CMD_ID_SYSTEM_POWER)

    if pdata:
        header += _field_varint(10, len(pdata))

    header += _field_varint(11, 1)
    header += _field_varint(14, seq)
    header += _field_varint(15, 1)
    header += _field_varint(16, 19)
    header += _field_varint(17, 1)
    header += _field_bytes(23, _SRC)
    header += _field_bytes(25, serial_number.encode("ascii"))
    return bytes(header)


def build_power_command(serial_number: str, seq: int, turn_on: bool) -> bytes:
    """
    Baut den vollstaendigen MQTT-Payload fuer den System-AN/AUS-Befehl.

    Args:
        serial_number: PowerOcean-Seriennummer.
        seq: Sequenznummer nach App-Schema.
        turn_on: `True` fuer System einschalten, `False` fuer ausschalten.

    Returns:
        Rohe Bytes fuer `/app/{user_id}/{sn}/thing/property/set`.
    """
    header = _build_power_header(serial_number, seq, turn_on)
    return _field_bytes(1, header)


def build_property_get_request(seq: int) -> bytes:
    """
    Baut die Protobuf-GET-Anfrage fuer `thing/property/get`.

    Warum:
        Der bisherige JSON-GET-Payload ist bei PowerOcean nicht ausreichend, um
        den vollstaendigen Status inklusive `JTS1_EMS_CHANGE_REPORT` zu erhalten.
        Diese App-nahe GET-Anfrage loest eine `get_reply`-Antwort aus.
    """
    header = (
        _field_varint(2, 32)
        + _field_varint(3, 32)
        + _field_varint(14, seq)
        + _field_bytes(23, b"Android")
        + _field_bytes(26, b"")
        + _field_bytes(27, b"")
    )
    return _field_bytes(1, header)


class SeqGenerator:
    """
    Erzeugt Sequenznummern im Stil der EcoFlow-App.

    Das Format wurde aus dem App-Verhalten abgeleitet:
    `"1" + Sekunde + Millisekunde + Zaehler`, auf die letzten 9 Stellen gekuerzt.
    Ein Lock verhindert doppelte Sequenzen bei parallelen HA-Service-Aufrufen.
    """

    def __init__(self) -> None:
        self._counter = 1000
        self._lock = Lock()

    def next(self, now: datetime | None = None) -> int:
        """Gibt die naechste Sequenznummer zurueck."""
        with self._lock:
            counter = self._counter
            self._counter += 1
            if self._counter > 9999:
                self._counter = 1000

        current = now or datetime.now()
        millis = current.microsecond // 1000
        combined = f"1{current.second}{millis:03d}{counter}"
        return int(combined[-9:])
