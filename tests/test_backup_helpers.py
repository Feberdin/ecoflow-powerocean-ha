"""
Unit-Tests für `backup_helpers.py`.

Zweck:
    Diese Tests prüfen die reine Backup-/Outage-Logik unabhängig von Home Assistant.

Input:
    - Künstliche Snapshot-Serien mit festen Zeitpunkten
    - Beispielhafte Optionswerte

Output:
    - Verifikation von Normalisierung, Laufzeitberechnung und Outage-Heuristik

Wichtige Invarianten:
    - Kein Import der eigentlichen HA-Integration nötig
    - Tests sollen mit Standardbibliothek (`unittest`) lokal laufen

Debug-Hinweis:
    - Ausführen mit:
      `python3 -m unittest tests.test_backup_helpers`
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
    """Lädt ein Modul direkt von Dateipfad, ohne `__init__.py` der Integration auszuführen."""
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
backup_helpers = _load_module(
    f"{PACKAGE_NAME}.backup_helpers",
    MODULE_DIR / "backup_helpers.py",
)


class BackupHelpersTestCase(unittest.TestCase):
    """Prüft den optionalen Backup-Helper-Layer mit festen Snapshot-Serien."""

    def setUp(self) -> None:
        self.now = datetime(2026, 4, 21, 18, 0, tzinfo=UTC)
        self.config = backup_helpers.backup_helper_config_from_mapping(
            {
                "enable_backup_helpers": True,
                "backup_reserved_soc_percent": 10,
                "power_outage_grid_power_threshold_w": 50,
                "power_outage_frequency_min_hz": 1.0,
                "backup_runtime_smoothing_minutes": 10,
                "backup_critical_runtime_minutes": 120,
            }
        )

    def _snapshot(
        self,
        *,
        seconds_ago: int,
        soc: int = 50,
        energy_wh: float = 10000.0,
        load_w: float = 800.0,
        grid_w: float = 5.0,
        solar_w: float = 100.0,
        battery_w: float = 750.0,
        frequency_hz: float | None = None,
    ):
        return backup_helpers.BackupSnapshot(
            observed_at=self.now - timedelta(seconds=seconds_ago),
            total_soc_percent=soc,
            total_energy_wh=energy_wh,
            load_power_w=load_w,
            grid_power_w=grid_w,
            solar_power_w=solar_w,
            battery_power_w=battery_w,
            grid_frequency_hz=frequency_hz,
        )

    def test_normalize_backup_helper_options_clamps_invalid_values(self) -> None:
        normalized = backup_helpers.normalize_backup_helper_options(
            {
                "enable_backup_helpers": 1,
                "backup_reserved_soc_percent": 150,
                "power_outage_grid_power_threshold_w": "3",
                "power_outage_frequency_min_hz": "99",
                "backup_runtime_smoothing_minutes": 0,
                "backup_critical_runtime_minutes": "99999",
            }
        )

        self.assertTrue(normalized["enable_backup_helpers"])
        self.assertEqual(normalized["backup_reserved_soc_percent"], 99)
        self.assertEqual(normalized["power_outage_grid_power_threshold_w"], 10)
        self.assertEqual(normalized["power_outage_frequency_min_hz"], 49.9)
        self.assertEqual(normalized["backup_runtime_smoothing_minutes"], 1)
        self.assertEqual(normalized["backup_critical_runtime_minutes"], 1440)

    def test_evaluate_backup_state_detects_likely_power_outage(self) -> None:
        snapshots = [
            self._snapshot(seconds_ago=40),
            self._snapshot(seconds_ago=20),
            self._snapshot(seconds_ago=0),
        ]

        evaluation = backup_helpers.evaluate_backup_state(
            snapshots,
            config=self.config,
            has_seen_valid_grid_frequency=True,
        )

        self.assertTrue(evaluation.enabled)
        self.assertTrue(evaluation.power_outage)
        self.assertTrue(evaluation.backup_active)
        self.assertEqual(evaluation.outage_reason, "grid_outage_likely")
        self.assertEqual(evaluation.usable_energy_wh, 4000.0)
        self.assertEqual(evaluation.runtime_estimate_minutes, 300.0)
        self.assertEqual(evaluation.recommended_action, "normal")

    def test_evaluate_backup_state_does_not_flag_outage_without_frequency_history(self) -> None:
        snapshots = [
            self._snapshot(seconds_ago=40),
            self._snapshot(seconds_ago=20),
            self._snapshot(seconds_ago=0),
        ]

        evaluation = backup_helpers.evaluate_backup_state(
            snapshots,
            config=self.config,
            has_seen_valid_grid_frequency=False,
        )

        self.assertFalse(evaluation.power_outage)
        self.assertFalse(evaluation.backup_active)
        self.assertEqual(evaluation.outage_reason, "grid_frequency_signal_unavailable")

    def test_evaluate_backup_state_marks_runtime_as_critical(self) -> None:
        snapshots = [
            self._snapshot(seconds_ago=40, energy_wh=3000.0, load_w=700.0, grid_w=3.0, solar_w=30.0, battery_w=660.0),
            self._snapshot(seconds_ago=20, energy_wh=3000.0, load_w=700.0, grid_w=3.0, solar_w=30.0, battery_w=660.0),
            self._snapshot(seconds_ago=0, energy_wh=3000.0, load_w=700.0, grid_w=3.0, solar_w=30.0, battery_w=660.0),
        ]

        evaluation = backup_helpers.evaluate_backup_state(
            snapshots,
            config=self.config,
            has_seen_valid_grid_frequency=True,
        )

        self.assertTrue(evaluation.backup_reserve_critical)
        self.assertAlmostEqual(evaluation.runtime_estimate_minutes, 102.9, places=1)
        self.assertEqual(evaluation.recommended_action, "shed_load")

        shutdown_snapshots = [
            self._snapshot(seconds_ago=40, energy_wh=3000.0, load_w=1200.0, grid_w=3.0, solar_w=120.0, battery_w=1100.0),
            self._snapshot(seconds_ago=20, energy_wh=3000.0, load_w=1200.0, grid_w=3.0, solar_w=120.0, battery_w=1100.0),
            self._snapshot(seconds_ago=0, energy_wh=3000.0, load_w=1200.0, grid_w=3.0, solar_w=120.0, battery_w=1100.0),
        ]

        shutdown_evaluation = backup_helpers.evaluate_backup_state(
            shutdown_snapshots,
            config=self.config,
            has_seen_valid_grid_frequency=True,
        )

        self.assertTrue(shutdown_evaluation.backup_reserve_critical)
        self.assertEqual(shutdown_evaluation.recommended_action, "shutdown_recommended")


if __name__ == "__main__":
    unittest.main()
