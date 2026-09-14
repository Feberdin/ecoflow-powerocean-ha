"""
Unit-Tests fuer System-Power-MQTT-Befehle und Systemstatus-Dekodierung.

Zweck:
    Diese Tests sichern den ersten optionalen Schreibzugriff der Integration ab:
    den PowerOcean-Systemschalter aus dem Nutzer-Fork. Gleichzeitig pruefen sie
    den read-only Statuspfad ueber `JTS1_EMS_CHANGE_REPORT`.

Input:
    - Reproduzierte, anonymisierte App-Mitschnitte fuer AN/AUS
    - Kuenstlich erzeugte Protobuf-Frames fuer den Systemstatus

Output:
    - Verifikation der Byte-genauen Payloads und der Statusauswertung

Wichtige Invarianten:
    - Keine Home-Assistant-Imports noetig
    - Keine echten Seriennummern oder Zugangsdaten in Testdaten
    - Statuswert 0 bedeutet System an, Statuswert 1 bedeutet System aus

Debug-Hinweis:
    - Ausfuehren mit:
      `python3 -m unittest discover -s tests -p 'test_system_power.py'`
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = REPO_ROOT / "custom_components" / "ecoflow_powerocean"
PACKAGE_NAME = "custom_components.ecoflow_powerocean"


def _load_module(module_name: str, file_path: Path):
    """Laedt ein Modul direkt von Dateipfad, ohne `__init__.py` auszufuehren."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Kann Modul nicht laden: {module_name} -> {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


if "custom_components" not in sys.modules:
    custom_components_pkg = types.ModuleType("custom_components")
    custom_components_pkg.__path__ = [str(REPO_ROOT / "custom_components")]
    sys.modules["custom_components"] = custom_components_pkg

if PACKAGE_NAME not in sys.modules:
    integration_pkg = types.ModuleType(PACKAGE_NAME)
    integration_pkg.__path__ = [str(MODULE_DIR)]
    sys.modules[PACKAGE_NAME] = integration_pkg

_load_module(f"{PACKAGE_NAME}.const", MODULE_DIR / "const.py")
proto_decoder = _load_module(
    f"{PACKAGE_NAME}.proto_decoder",
    MODULE_DIR / "proto_decoder.py",
)
command_encoder = _load_module(
    f"{PACKAGE_NAME}.command_encoder",
    MODULE_DIR / "command_encoder.py",
)


def _encode_varint(value: int) -> bytes:
    """Kodiert eine positive Ganzzahl im Protobuf-Varint-Format."""
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


def _field_varint(field: int, value: int) -> bytes:
    return _encode_varint((field << 3) | 0) + _encode_varint(value)


def _field_bytes(field: int, value: bytes) -> bytes:
    return _encode_varint((field << 3) | 2) + _encode_varint(len(value)) + value


def _header(*, cmd_func: int, cmd_id: int, pdata: bytes) -> bytes:
    return (
        _field_bytes(1, pdata)
        + _field_varint(8, cmd_func)
        + _field_varint(9, cmd_id)
        + _field_varint(14, 123456789)
    )


class SystemPowerProtocolTestCase(unittest.TestCase):
    """Prueft Encoder und Decoder fuer den optionalen System-Power-Schalter."""

    def test_power_command_matches_captured_app_frames(self) -> None:
        serial = "R000EXAMPLE00000"

        self.assertEqual(
            command_encoder.build_power_command(
                serial,
                seq=526431350,
                turn_on=False,
            ).hex(),
            "0a3f0a0208011020186020012801406048695002580170f6e882fb017801800113880101ba0107616e64726f6964ca0110523030304558414d504c453030303030",
        )
        self.assertEqual(
            command_encoder.build_power_command(
                serial,
                seq=196721359,
                turn_on=True,
            ).hex(),
            "0a38102018602001280140604869580170cff5e65d7801800113880101ba0107616e64726f6964ca0110523030304558414d504c453030303030",
        )

    def test_property_get_request_matches_captured_shape(self) -> None:
        self.assertEqual(
            command_encoder.build_property_get_request(seq=123456789).hex(),
            "0a191020182070959aef3aba0107416e64726f6964d20100da0100",
        )

    def test_decoder_extracts_system_power_status_from_ems_change_report(self) -> None:
        pdata = (
            _field_varint(1, 7)
            + _field_varint(3, 8)
            + _field_varint(10, 1)
            + _field_varint(205, 8)
        )
        raw = _field_bytes(1, _header(cmd_func=96, cmd_id=17, pdata=pdata))

        battery_packs, energy_stream, ems_heartbeat, system_status = (
            proto_decoder.decode_mqtt_payload(raw)
        )

        self.assertEqual(battery_packs, [])
        self.assertIsNone(energy_stream)
        self.assertIsNone(ems_heartbeat)
        self.assertIsNotNone(system_status)
        assert system_status is not None
        self.assertFalse(system_status.system_power_on)
        self.assertEqual(system_status.sys_on_off_machine_stat, 1)
        self.assertEqual(system_status.sys_work_sta, 7)
        self.assertEqual(system_status.ems_work_mode, 8)
        self.assertEqual(system_status.ems_work_mode_label, "soc_calib")
        self.assertEqual(system_status.ems_work_state, 8)
        self.assertEqual(system_status.ems_work_state_label, "stop")

    def test_decoder_maps_zero_system_status_to_on(self) -> None:
        pdata = _field_varint(10, 0)
        raw = _field_bytes(1, _header(cmd_func=96, cmd_id=17, pdata=pdata))

        _, _, _, system_status = proto_decoder.decode_mqtt_payload(raw)

        self.assertIsNotNone(system_status)
        assert system_status is not None
        self.assertTrue(system_status.system_power_on)


if __name__ == "__main__":
    unittest.main()
