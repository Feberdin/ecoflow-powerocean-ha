"""
Unit-Tests für die System-Power-Reserve-Automatik.

Zweck:
    Diese Tests prüfen die reine Entscheidungslogik ohne Home-Assistant-Teststack.

Input:
    - Künstliche SOC-Werte
    - Tag-/Nachtstatus
    - Bekannter System-Power-Zustand
    - Persistenter Merker, ob die Integration selbst ausgeschaltet hat

Output:
    - Eine robuste Entscheidung: einschalten, ausschalten oder nichts tun

Wichtige Invarianten:
    - Die Automatik ist defensiv: nie bei fehlendem SOC oder unbekanntem
      System-Power-Zustand schalten.
    - Abschalten passiert nur nachts und nur bei erreichter Backup-Reserve.
    - Wiedereinschalten passiert nach einer automatischen Abschaltung spätestens
      am Morgen oder vorher mit Hysterese.

Debug-Hinweis:
    - Ausführen mit:
      `python3 -m unittest discover -s tests -p 'test_system_power_guard.py'`
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
system_power_guard = _load_module(
    f"{PACKAGE_NAME}.system_power_guard",
    MODULE_DIR / "system_power_guard.py",
)


class SystemPowerReserveGuardTestCase(unittest.TestCase):
    """Prüft die Reserve-Automatik ohne MQTT- oder HA-Seiteneffekte."""

    def _decision(
        self,
        *,
        now_is_day: bool = False,
        soc_percent: float | None = 10.0,
        reserve_soc_percent: int = 10,
        restart_margin_percent: int = 2,
        system_power_on: bool | None = True,
        auto_powered_off: bool = False,
        power_outage: bool = False,
        backup_active: bool = False,
    ):
        return system_power_guard.decide_system_power_reserve_guard(
            now_is_day=now_is_day,
            soc_percent=soc_percent,
            reserve_soc_percent=reserve_soc_percent,
            restart_margin_percent=restart_margin_percent,
            system_power_on=system_power_on,
            auto_powered_off=auto_powered_off,
            power_outage=power_outage,
            backup_active=backup_active,
        )

    def test_turns_off_at_reserved_soc_after_sunset(self) -> None:
        decision = self._decision(now_is_day=False, soc_percent=10.0)

        self.assertEqual(decision.action, system_power_guard.ACTION_TURN_OFF)
        self.assertEqual(decision.reason, "reserve_reached")

    def test_keeps_running_above_reserved_soc_after_sunset(self) -> None:
        decision = self._decision(now_is_day=False, soc_percent=10.1)

        self.assertIsNone(decision.action)
        self.assertEqual(decision.reason, "reserve_not_reached")

    def test_does_not_turn_off_during_day(self) -> None:
        decision = self._decision(now_is_day=True, soc_percent=5.0)

        self.assertIsNone(decision.action)
        self.assertEqual(decision.reason, "daylight")

    def test_turns_on_at_sunrise_after_automatic_shutdown(self) -> None:
        decision = self._decision(
            now_is_day=True,
            soc_percent=10.0,
            system_power_on=False,
            auto_powered_off=True,
        )

        self.assertEqual(decision.action, system_power_guard.ACTION_TURN_ON)
        self.assertEqual(decision.reason, "daylight_after_auto_shutdown")

    def test_turns_on_at_night_after_soc_recovers_past_hysteresis(self) -> None:
        decision = self._decision(
            now_is_day=False,
            soc_percent=12.0,
            system_power_on=False,
            auto_powered_off=True,
        )

        self.assertEqual(decision.action, system_power_guard.ACTION_TURN_ON)
        self.assertEqual(decision.reason, "soc_above_restart_threshold")

    def test_does_not_switch_during_detected_outage_or_backup(self) -> None:
        outage_decision = self._decision(power_outage=True)
        backup_decision = self._decision(backup_active=True)

        self.assertIsNone(outage_decision.action)
        self.assertEqual(outage_decision.reason, "outage_or_backup_active")
        self.assertIsNone(backup_decision.action)
        self.assertEqual(backup_decision.reason, "outage_or_backup_active")

    def test_missing_soc_or_unknown_power_state_never_switches(self) -> None:
        missing_soc = self._decision(soc_percent=None)
        unknown_power = self._decision(system_power_on=None)

        self.assertIsNone(missing_soc.action)
        self.assertEqual(missing_soc.reason, "missing_soc")
        self.assertIsNone(unknown_power.action)
        self.assertEqual(unknown_power.reason, "unknown_system_power_state")

    def test_sunrise_after_auto_shutdown_turns_on_without_fresh_soc(self) -> None:
        decision = self._decision(
            now_is_day=True,
            soc_percent=None,
            system_power_on=None,
            auto_powered_off=True,
        )

        self.assertEqual(decision.action, system_power_guard.ACTION_TURN_ON)
        self.assertEqual(decision.reason, "daylight_after_auto_shutdown")

    def test_manual_off_without_auto_marker_is_left_untouched(self) -> None:
        decision = self._decision(
            now_is_day=True,
            soc_percent=80.0,
            system_power_on=False,
            auto_powered_off=False,
        )

        self.assertIsNone(decision.action)
        self.assertEqual(decision.reason, "manual_or_external_off")


if __name__ == "__main__":
    unittest.main()
