"""
Unit-Tests fuer `backup_notification.py`.

Zweck:
    Diese Tests pruefen die Stromausfall-Benachrichtigung ohne kompletten
    Home-Assistant-Teststack.

Input:
    - Kuenstliche `BackupEvaluation`-Objekte
    - Fake-Home-Assistant-Service fuer `notify.send_message`

Output:
    - Verifikation von Options-Normalisierung, Einmalversand pro Ausfallphase,
      Rearming nach Normalzustand und Testnachricht

Wichtige Invarianten:
    - Die Erkennung selbst bleibt in `backup_helpers.py`
    - Der Testbutton darf den Ausfall-Merker nicht setzen

Debug-Hinweis:
    - Ausfuehren mit:
      `python3 -m unittest tests.test_backup_notification`
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from datetime import UTC, datetime
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
backup_helpers = _load_module(
    f"{PACKAGE_NAME}.backup_helpers",
    MODULE_DIR / "backup_helpers.py",
)
_load_module(f"{PACKAGE_NAME}.daily_report", MODULE_DIR / "daily_report.py")
backup_notification = _load_module(
    f"{PACKAGE_NAME}.backup_notification",
    MODULE_DIR / "backup_notification.py",
)


class BackupNotificationTestCase(unittest.TestCase):
    """Prueft die optionale Stromausfall-Benachrichtigung."""

    def setUp(self) -> None:
        self.now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)

    def _evaluation(
        self,
        *,
        power_outage: bool,
        backup_active: bool,
        enabled: bool = True,
    ):
        return backup_helpers.BackupEvaluation(
            enabled=enabled,
            observed_at=self.now,
            usable_energy_wh=4000.0,
            smoothed_load_power_w=800.0,
            runtime_estimate_minutes=300.0,
            runtime_estimate_hours=5.0,
            backup_reserve_critical=False,
            power_outage=power_outage,
            backup_active=backup_active,
            recommended_action="normal",
            outage_reason="grid_outage_likely" if power_outage else "normal",
            has_seen_valid_grid_frequency=True,
        )

    def test_options_normalize_notify_entity(self) -> None:
        normalized = backup_notification.normalize_backup_outage_notification_options(
            {
                "enable_backup_outage_notification": True,
                "backup_outage_notify_target": "notify.mobile_app",
            }
        )

        self.assertTrue(normalized["enable_backup_outage_notification"])
        self.assertEqual(
            normalized["backup_outage_notify_target"],
            {"entity_id": "notify.mobile_app"},
        )

    def test_send_logic_sends_once_and_rearms_after_recovery(self) -> None:
        state = backup_notification.BackupOutageNotificationState()
        outage = self._evaluation(power_outage=True, backup_active=True)
        normal = self._evaluation(power_outage=False, backup_active=False)

        self.assertTrue(
            backup_notification.should_send_backup_outage_notification(
                outage,
                state,
            )
        )
        state.notification_sent_for_active_outage = True
        self.assertFalse(
            backup_notification.should_send_backup_outage_notification(
                outage,
                state,
            )
        )
        self.assertTrue(
            backup_notification.should_reset_backup_outage_notification(
                normal,
                state,
            )
        )

    def test_manager_sends_once_per_outage_phase(self) -> None:
        class FakeServices:
            def __init__(self) -> None:
                self.calls = []

            async def async_call(
                self,
                domain,
                service,
                data,
                *,
                target=None,
                blocking=False,
            ) -> None:
                self.calls.append(
                    {
                        "domain": domain,
                        "service": service,
                        "data": data,
                        "target": target,
                        "blocking": blocking,
                    }
                )

        class FakeHass:
            def __init__(self) -> None:
                self.services = FakeServices()

        class FakeEntry:
            entry_id = "entry"
            options = {
                "enable_backup_outage_notification": True,
                "backup_outage_notify_target": "notify.mobile_app",
            }

        class FakeCoordinator:
            def __init__(self, evaluation) -> None:
                self.backup_evaluation = evaluation

        hass = FakeHass()
        coordinator = FakeCoordinator(
            self._evaluation(power_outage=True, backup_active=True)
        )
        manager = backup_notification.BackupOutageNotificationManager(
            hass,
            FakeEntry(),
            coordinator,
        )

        asyncio.run(manager.async_process_coordinator_update())
        asyncio.run(manager.async_process_coordinator_update())

        self.assertEqual(len(hass.services.calls), 1)
        self.assertEqual(hass.services.calls[0]["domain"], "notify")
        self.assertEqual(hass.services.calls[0]["service"], "send_message")
        self.assertEqual(
            hass.services.calls[0]["target"],
            {"entity_id": "notify.mobile_app"},
        )

        coordinator.backup_evaluation = self._evaluation(
            power_outage=False,
            backup_active=False,
        )
        asyncio.run(manager.async_process_coordinator_update())
        coordinator.backup_evaluation = self._evaluation(
            power_outage=True,
            backup_active=True,
        )
        asyncio.run(manager.async_process_coordinator_update())

        self.assertEqual(len(hass.services.calls), 2)

    def test_test_notification_does_not_mark_outage_as_sent(self) -> None:
        class FakeServices:
            def __init__(self) -> None:
                self.calls = []

            async def async_call(self, *args, **kwargs) -> None:
                self.calls.append((args, kwargs))

        class FakeHass:
            def __init__(self) -> None:
                self.services = FakeServices()

        class FakeEntry:
            entry_id = "entry"
            options = {
                "enable_backup_outage_notification": True,
                "backup_outage_notify_target": "notify.mobile_app",
            }

        class FakeCoordinator:
            def __init__(self, evaluation) -> None:
                self.backup_evaluation = evaluation

        hass = FakeHass()
        manager = backup_notification.BackupOutageNotificationManager(
            hass,
            FakeEntry(),
            FakeCoordinator(self._evaluation(power_outage=False, backup_active=False)),
        )

        sent = asyncio.run(manager.async_send_test_notification())

        self.assertTrue(sent)
        self.assertFalse(manager.state.notification_sent_for_active_outage)
        self.assertEqual(len(hass.services.calls), 1)
        args, kwargs = hass.services.calls[0]
        self.assertEqual(args[0], "notify")
        self.assertEqual(args[1], "send_message")
        self.assertEqual(args[2]["title"], "EcoFlow Stromausfall-Test")
        self.assertEqual(kwargs["target"], {"entity_id": "notify.mobile_app"})


if __name__ == "__main__":
    unittest.main()
