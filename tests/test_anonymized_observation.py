"""
Unit-Tests für den anonymisierten Messdaten-Export.

Zweck:
    Prüft, dass aus Home-Assistant-State-Daten ein deutlich informativeres,
    aber weiterhin anonymisiertes Beobachtungspaket entsteht.

Input:
    - Künstliche Home-Assistant-State-Objekte
    - Öffentlich teilbare Metadaten wie Integrationsversion und Modell

Output:
    - JSON-kompatibles Dict mit Live-Leistung, Energiezählern, Batterie-,
      Backup-, Tagesbericht- und Systemstatusdaten

Wichtige Invarianten:
    - Keine Seriennummern oder vollständigen nutzerspezifischen Entity-IDs
      werden ausgegeben.
    - Entity-Quellen werden nur als Domain plus Suffix dokumentiert.

Debug-Hinweis:
    - Ausführen mit:
      `python3 -m unittest discover -s tests -p 'test_anonymized_observation.py'`
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "build_anonymized_observation.py"


def _load_module():
    """Lädt das Script direkt von Dateipfad."""
    spec = importlib.util.spec_from_file_location(
        "build_anonymized_observation",
        MODULE_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Kann Modul nicht laden: {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_anonymized_observation"] = module
    spec.loader.exec_module(module)
    return module


observation = _load_module()


class AnonymizedObservationTestCase(unittest.TestCase):
    """Prüft das reichere Austauschformat."""

    def _states(self):
        def state(entity_id, value, **attrs):
            return {
                "entity_id": entity_id,
                "state": value,
                "attributes": {
                    "friendly_name": attrs.get("friendly_name", entity_id),
                    **{k: v for k, v in attrs.items() if k != "friendly_name"},
                },
            }

        return [
            state("sensor.garage_ecoflow_powerocean_plus_gesamt_ladestand", "77"),
            state("sensor.garage_ecoflow_powerocean_plus_solar_leistung", "0.4"),
            state("sensor.garage_ecoflow_powerocean_plus_netz_leistung", "-761.6"),
            state("sensor.garage_ecoflow_powerocean_plus_hausverbrauch", "113"),
            state(
                "sensor.garage_ecoflow_powerocean_plus_batterie_gesamtleistung",
                "874.1",
            ),
            state("sensor.garage_ecoflow_powerocean_plus_solar_energie", "123.45"),
            state("sensor.garage_ecoflow_powerocean_plus_netz_einspeisung", "3528.1125"),
            state("sensor.garage_ecoflow_powerocean_plus_netzbezug", "25.5"),
            state("sensor.garage_ecoflow_powerocean_plus_batterie_entnahme", "42.0"),
            state("sensor.garage_ecoflow_powerocean_plus_batterie_ladung", "40.0"),
            state("sensor.garage_ecoflow_powerocean_plus_battery_1_soc", "77"),
            state("sensor.garage_ecoflow_powerocean_plus_battery_1_soh", "99"),
            state("sensor.garage_ecoflow_powerocean_plus_battery_1_power", "291.4"),
            state(
                "sensor.garage_ecoflow_powerocean_plus_battery_1_remaining_energy",
                "3942",
            ),
            state("sensor.garage_ecoflow_powerocean_plus_battery_1_temperature", "30"),
            state("sensor.garage_ecoflow_powerocean_plus_battery_1_cycles", "178"),
            state(
                "sensor.garage_ecoflow_powerocean_plus_geschatzte_backup_laufzeit_minuten",
                "847.6",
            ),
            state(
                "sensor.garage_ecoflow_powerocean_plus_nutzbare_backup_energie",
                "11160",
            ),
            state(
                "sensor.garage_ecoflow_powerocean_plus_empfohlene_backup_aktion",
                "normal",
            ),
            state(
                "sensor.garage_ecoflow_powerocean_plus_tagesbericht_gesamt_einspeisung",
                "1713.3881",
            ),
            state(
                "sensor.garage_ecoflow_powerocean_plus_tagesbericht_gesamt_vergutung",
                "131.93",
            ),
            state(
                "sensor.garage_ecoflow_powerocean_plus_tagesbericht_akku_100_gesamtzeit",
                "388.372",
            ),
            state("sensor.garage_ecoflow_powerocean_plus_verbindungsstatus", "connected"),
            state("sensor.garage_ecoflow_powerocean_plus_system_power_status", "on"),
            state("sensor.garage_ecoflow_powerocean_plus_system_arbeitsmodus", "self_use"),
            state("sensor.garage_ecoflow_powerocean_plus_system_arbeitszustand", "running"),
            state("binary_sensor.garage_ecoflow_powerocean_plus_backup_aktiv", "off"),
            state("binary_sensor.garage_ecoflow_powerocean_plus_stromausfall_erkannt", "off"),
            state(
                "binary_sensor.garage_ecoflow_powerocean_plus_backup_reserve_kritisch",
                "off",
            ),
            state(
                "sensor.garage_ecoflow_powerocean_plus_private_seriennummer",
                "R37SECRET123456",
            ),
        ]

    def test_builds_rich_redacted_observation(self) -> None:
        result = observation.build_anonymized_observation(
            self._states(),
            source_id="sample-a",
            observed_at="2026-09-14T23:39:10+02:00",
            repo="Feberdin/ecoflow-powerocean-ha",
            integration_version="v0.4.21",
            home_assistant_version="2026.7.4",
            device_model="PowerOcean Plus",
            battery_packs=3,
            settings={
                "backup_reserved_soc_percent": 10,
                "system_power_reserve_guard_enabled": True,
                "system_power_reserve_restart_margin_percent": 2,
            },
        )

        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["measurements"]["power"]["grid_power_w"], -761.6)
        self.assertEqual(result["measurements"]["energy"]["grid_export_energy_kwh"], 3528.1125)
        self.assertEqual(result["measurements"]["battery"]["total_soc_percent"], 77.0)
        self.assertEqual(result["measurements"]["battery"]["packs"][0]["soc_percent"], 77.0)
        self.assertEqual(result["measurements"]["backup"]["runtime_estimate_minutes"], 847.6)
        self.assertEqual(result["measurements"]["daily_report"]["total_value_eur"], 131.93)
        self.assertEqual(result["status"]["system_power_state"], "on")
        self.assertFalse(result["status"]["power_outage"])
        self.assertEqual(result["privacy"]["redaction_level"], "entity_id_suffix_only")

        dumped = observation.json_dumps(result)
        self.assertNotIn("garage", dumped)
        self.assertNotIn("R37SECRET", dumped)
        self.assertIn("matched_suffix", dumped)


if __name__ == "__main__":
    unittest.main()
