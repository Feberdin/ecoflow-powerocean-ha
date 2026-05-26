"""
Unit-Tests für `daily_report.py`.

Zweck:
    Diese Tests prüfen die reine Tagesbericht-Logik ohne Home-Assistant-Stack.

Input:
    - Künstliche Zeitpunkte
    - Beispielhafte Einspeiseleistungen und SOC-Werte
    - Tarifwerte aus UI-ähnlichen Eingaben

Output:
    - Verifikation von Tarif-Normalisierung, Energieintegration,
      Akku-100%-Dauer, Tageswechsel, Formatierung und Nachrichtentext

Wichtige Invarianten:
    - Keine Home-Assistant-Imports für die getestete Kernlogik nötig
    - Tests laufen mit Standardbibliothek (`unittest`)

Debug-Hinweis:
    - Ausführen mit:
      `python3 -m unittest tests.test_daily_report`
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = REPO_ROOT / "custom_components" / "ecoflow_powerocean"
PACKAGE_NAME = "custom_components.ecoflow_powerocean"


def _load_module(module_name: str, file_path: Path):
    """Lädt ein Modul direkt von Dateipfad, ohne `__init__.py` auszuführen."""
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
_load_module(f"{PACKAGE_NAME}.backup_helpers", MODULE_DIR / "backup_helpers.py")
daily_report = _load_module(
    f"{PACKAGE_NAME}.daily_report",
    MODULE_DIR / "daily_report.py",
)


class DailyReportTestCase(unittest.TestCase):
    """Prüft die testbare Kernlogik des Sonnenuntergangsberichts."""

    def setUp(self) -> None:
        self.start = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)

    def _accumulator(self):
        return daily_report.DailyReportAccumulator(
            daily_report.DailyReportState(local_date=self.start.date().isoformat())
        )

    def test_tariff_normalization_clamps_invalid_values(self) -> None:
        self.assertEqual(daily_report.normalize_feed_in_tariff("abc"), 0.077)
        self.assertEqual(daily_report.normalize_feed_in_tariff(-1), 0.0)
        self.assertEqual(daily_report.normalize_feed_in_tariff(2), 1.0)
        self.assertEqual(daily_report.normalize_feed_in_tariff("0.068"), 0.068)
        self.assertEqual(daily_report.normalize_feed_in_tariff("0,068"), 0.068)

    def test_notify_entity_is_normalized_to_service_target(self) -> None:
        normalized = daily_report.normalize_daily_report_options(
            {
                "enable_daily_sunset_report": True,
                "daily_report_notify_target": "notify.mobile_app",
                "daily_report_feed_in_tariff_eur_per_kwh": 0.077,
            }
        )

        self.assertEqual(
            normalized["daily_report_notify_target"],
            {"entity_id": "notify.mobile_app"},
        )
        self.assertTrue(
            daily_report.has_notification_target(
                normalized["daily_report_notify_target"]
            )
        )
        self.assertEqual(
            daily_report.notification_target_entity_id(
                normalized["daily_report_notify_target"]
            ),
            "notify.mobile_app",
        )

    def test_energy_integration_counts_export_power(self) -> None:
        acc = self._accumulator()
        acc.update(self.start, export_power_w=1000.0, soc_percent=80)
        acc.update(
            self.start + timedelta(minutes=30),
            export_power_w=0.0,
            soc_percent=80,
        )

        self.assertAlmostEqual(acc.state.daily_export_kwh, 0.5)

    def test_energy_integration_ignores_negative_export_power(self) -> None:
        acc = self._accumulator()
        acc.update(self.start, export_power_w=-500.0, soc_percent=80)
        acc.update(
            self.start + timedelta(minutes=30),
            export_power_w=0.0,
            soc_percent=80,
        )

        self.assertEqual(acc.state.daily_export_kwh, 0.0)

    def test_full_soc_duration_counts_only_full_soc(self) -> None:
        acc = self._accumulator()
        acc.update(self.start, export_power_w=0.0, soc_percent=100)
        acc.update(
            self.start + timedelta(minutes=10),
            export_power_w=0.0,
            soc_percent=100,
        )
        acc.update(
            self.start + timedelta(minutes=20),
            export_power_w=0.0,
            soc_percent=100,
        )
        acc.update(
            self.start + timedelta(minutes=30),
            export_power_w=0.0,
            soc_percent=99,
        )

        self.assertEqual(acc.state.battery_full_seconds, 1800.0)

        acc.update(
            self.start + timedelta(minutes=40),
            export_power_w=0.0,
            soc_percent=99,
        )
        self.assertEqual(acc.state.battery_full_seconds, 1800.0)

    def test_day_change_resets_daily_counters(self) -> None:
        acc = self._accumulator()
        acc.update(self.start, export_power_w=1000.0, soc_percent=100)
        acc.update(
            self.start + timedelta(minutes=30),
            export_power_w=1000.0,
            soc_percent=100,
        )

        next_day = self.start + timedelta(days=1)
        acc.update(next_day, export_power_w=1000.0, soc_percent=100)

        self.assertEqual(acc.state.local_date, next_day.date().isoformat())
        self.assertEqual(acc.state.daily_export_kwh, 0.0)
        self.assertEqual(acc.state.battery_full_seconds, 0.0)

    def test_duration_formatting(self) -> None:
        self.assertEqual(daily_report.format_duration(0), "0 min")
        self.assertEqual(daily_report.format_duration(59 * 60), "59 min")
        self.assertEqual(daily_report.format_duration(65 * 60), "1 h 05 min")

    def test_message_uses_tariff_to_calculate_value(self) -> None:
        state = daily_report.DailyReportState(
            local_date=self.start.date().isoformat(),
            daily_export_kwh=10.0,
            battery_full_seconds=65 * 60,
            last_update_iso=self.start.isoformat(),
        )

        self.assertAlmostEqual(
            daily_report.calculate_report_value_eur(10.0, 0.077),
            0.77,
        )
        message = daily_report.build_daily_report_message(
            state,
            tariff_eur_per_kwh=0.077,
            has_enough_data=True,
        )

        self.assertIn("Heute eingespeist: 10,00 kWh", message)
        self.assertIn("Vergütung: 0,77 € (0,0770 €/kWh)", message)
        self.assertIn("Akku bei 100 %: 1 h 05 min", message)


if __name__ == "__main__":
    unittest.main()
