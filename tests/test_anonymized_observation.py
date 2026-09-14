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
      Backup-, Tagesbericht-, Systemstatusdaten und anonymisierten
      EcoFlow-Rohzuständen

Wichtige Invarianten:
    - Keine Seriennummern oder vollständigen nutzerspezifischen Entity-IDs
      werden ausgegeben.
    - Entity-Quellen werden nur als Domain plus Suffix dokumentiert.
    - Der Export enthält eine klare Zweckbindung für freiwillig geteilte Daten.

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
            state(
                "sensor.garage_ecoflow_powerocean_plus_netz_leistung",
                "-761.6",
                friendly_name="Garage Netz Leistung",
                unit_of_measurement="W",
                device_class="power",
                state_class="measurement",
            ),
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

        self.assertEqual(result["schema_version"], 3)
        self.assertEqual(
            result["purpose"]["allowed_use"],
            "improve_ecoflow_powerocean_integration_and_analysis_only",
        )
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

    def test_includes_all_relevant_ecoflow_states_without_private_ids(self) -> None:
        result = observation.build_anonymized_observation(
            self._states(),
            source_id="sample-a",
            observed_at="2026-09-14T23:39:10+02:00",
        )

        exported_states = result["anonymized_entities"]
        exported_by_suffix = {item["suffix"]: item for item in exported_states}

        self.assertIn("netz_leistung", exported_by_suffix)
        self.assertIn("private_seriennummer", exported_by_suffix)
        self.assertEqual(
            exported_by_suffix["private_seriennummer"]["state"],
            "<redacted>",
        )
        self.assertEqual(
            exported_by_suffix["netz_leistung"]["attributes"]["unit_of_measurement"],
            "W",
        )

        dumped = observation.json_dumps(result)
        self.assertNotIn("sensor.garage_ecoflow_powerocean_plus_netz_leistung", dumped)
        self.assertNotIn("friendly_name", dumped)
        self.assertNotIn("Garage", dumped)
        self.assertNotIn("R37SECRET123456", dumped)

    def test_skips_unrelated_home_assistant_states(self) -> None:
        states = self._states()
        states.append(
            {
                "entity_id": "sensor.kueche_temperatur",
                "state": "21.3",
                "attributes": {
                    "friendly_name": "Küche Temperatur",
                    "unit_of_measurement": "°C",
                },
            }
        )

        result = observation.build_anonymized_observation(
            states,
            source_id="sample-a",
            observed_at="2026-09-14T23:39:10+02:00",
        )

        dumped = observation.json_dumps(result)
        self.assertNotIn("kueche", dumped)
        self.assertNotIn("Küche", dumped)

    def test_can_include_anonymized_monthly_statistics(self) -> None:
        result = observation.build_anonymized_observation(
            self._states(),
            source_id="sample-a",
            observed_at="2026-09-14T23:39:10+02:00",
            statistics={
                "sensor.garage_ecoflow_powerocean_plus_netz_einspeisung": {
                    "unit_of_measurement": "kWh",
                    "rows": [
                        {"start": "2026-08-01T00:00:00+02:00", "sum": 3190.3159},
                        {"start": "2026-09-01T00:00:00+02:00", "sum": 3527.7588},
                    ],
                },
                "sensor.kueche_temperatur": {
                    "unit_of_measurement": "°C",
                    "rows": [{"start": "2026-09-01T00:00:00+02:00", "mean": 21.3}],
                },
            },
        )

        monthly = result["statistics"]["monthly"]
        self.assertIn("netz_einspeisung", monthly)
        self.assertNotIn("kueche_temperatur", monthly)
        self.assertEqual(monthly["netz_einspeisung"]["unit_of_measurement"], "kWh")
        self.assertEqual(monthly["netz_einspeisung"]["values"][0]["period"], "2026-08")
        self.assertEqual(monthly["netz_einspeisung"]["values"][1]["delta"], 337.4429)


if __name__ == "__main__":
    unittest.main()
