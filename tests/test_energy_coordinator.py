"""Actual MQTT callback contract with synthetic decoded samples and a fake HA loop.

Input: delayed/reordered samples; output: monotonic device acquisition time.
No MQTT connection or device command. Run with unittest discover -s tests.
"""

from __future__ import annotations
import ast
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path
from types import SimpleNamespace
from threading import Lock
import unittest
from test_backup_helpers import proto_decoder
from custom_components.ecoflow_powerocean import const


class EnergyCoordinatorTests(unittest.TestCase):
    def test_device_time_is_kept_and_older_details_do_not_replace_newer_sample(self):
        now = datetime(2026, 2, 1, 10, tzinfo=UTC)
        candidate = proto_decoder.EnergyStreamData(
            solar_w=2000, sampled_at=now - timedelta(seconds=20)
        )
        source = (
            Path(__file__).resolve().parents[1]
            / "custom_components/ecoflow_powerocean/coordinator.py"
        )
        tree = ast.parse(source.read_text())
        cls = next(
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "EcoFlowCoordinator"
        )
        method = next(
            n
            for n in cls.body
            if isinstance(n, ast.FunctionDef) and n.name == "_on_mqtt_message"
        )
        scope = {
            name: getattr(const, name)
            for name in dir(const)
            if name.startswith("DATA_")
        }
        scope.update(
            {
                "_LOGGER": logging.getLogger("callback_test"),
                "dt_util": SimpleNamespace(utcnow=lambda: now),
                "decode_mqtt_payload": lambda _raw: ([], candidate, None, None),
            }
        )
        future = ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0
        )
        module = ast.fix_missing_locations(
            ast.Module(body=[future, method], type_ignores=[])
        )
        exec(compile(module, str(source), "exec"), scope)
        instance = SimpleNamespace(data={}, _mqtt_lock=Lock())
        instance._handle_incoming_data = lambda data, _time: setattr(
            instance, "data", data
        )
        instance.hass = SimpleNamespace(
            loop=SimpleNamespace(call_soon_threadsafe=lambda fn, *args: fn(*args))
        )
        callback = scope["_on_mqtt_message"]
        callback(instance, None, None, SimpleNamespace(payload=b"synthetic"))
        self.assertEqual(
            instance.data["energy_stream_observed_at"], candidate.sampled_at
        )
        candidate = proto_decoder.EnergyStreamData(
            solar_w=9999, sampled_at=now - timedelta(seconds=60)
        )
        callback(instance, None, None, SimpleNamespace(payload=b"synthetic"))
        self.assertEqual(instance.data["energy_stream"].solar_w, 2000)
