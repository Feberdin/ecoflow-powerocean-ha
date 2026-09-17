"""Regression tests for measured energy, without HA, MQTT or production data.

Input: synthetic powers and timestamps. Output: exact counter/availability checks.
The real accumulator class is compiled with only its HA boundary replaced.
Debug: python3 -m unittest discover -s tests -p 'test_energy_quality.py' -v.
"""

from __future__ import annotations

import ast
import logging
import math
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from test_backup_helpers import backup_helpers, proto_decoder


class BoundaryEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    def __class_getitem__(cls, _type):
        return cls

    def _handle_coordinator_update(self):
        pass

    @property
    def available(self):
        return True


class EnergyQualityTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 2, 1, 10, tzinfo=UTC)
        self.power = 1000.0
        source = (
            Path(__file__).resolve().parents[1]
            / "custom_components/ecoflow_powerocean/sensor.py"
        )
        tree = ast.parse(source.read_text())
        node = next(
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef)
            and n.name == "EcoFlowEnergyAccumulatorSensor"
        )
        scope = {
            "CoordinatorEntity": BoundaryEntity,
            "EcoFlowCoordinator": object,
            "RestoreSensor": type("RestoreSensor", (), {}),
            "SensorDeviceClass": SimpleNamespace(ENERGY="energy"),
            "SensorStateClass": SimpleNamespace(TOTAL_INCREASING="total_increasing"),
            "UnitOfEnergy": SimpleNamespace(KILO_WATT_HOUR="kWh"),
            "callback": lambda fn: fn,
            "datetime": datetime,
            "Any": Any,
            "dt_util": SimpleNamespace(utcnow=lambda: self.now),
            "_LOGGER": logging.getLogger("energy_quality_test"),
            "math": math,
            "DATA_ENERGY_STREAM": "energy_stream",
            "DATA_EMS_HEARTBEAT": "ems_heartbeat",
        }
        exec(
            compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"),
            scope,
        )
        self.coordinator = SimpleNamespace(
            data={"energy_stream": object()}, gap_event_id=0, last_gap_seconds=0
        )
        description = SimpleNamespace(
            key="solar_energy", power_fn=lambda _data: self.power
        )
        self.sensor = scope["EcoFlowEnergyAccumulatorSensor"](
            self.coordinator, description, {}, "synthetic"
        )
        self.sensor._handle_coordinator_update()

    def advance(self, seconds, power):
        self.now += timedelta(seconds=seconds)
        self.power = power
        self.sensor._handle_coordinator_update()

    def test_regular_samples_integrate_left_riemann_energy(self):
        self.advance(60, 2000)
        self.assertAlmostEqual(self.sensor._accumulated_kwh, 1 / 60)

    def test_overnight_reconnect_does_not_invent_solar_production(self):
        self.coordinator.gap_event_id = 1
        self.coordinator.last_gap_seconds = 12 * 3600
        self.advance(12 * 3600, 2000)
        self.assertEqual(self.sensor._accumulated_kwh, 0)

    def test_long_silent_gap_is_not_integrated_even_without_reconnect_event(self):
        self.advance(3600, 2000)
        self.assertEqual(self.sensor._accumulated_kwh, 0)

    def test_missing_power_breaks_integration_without_a_false_zero_measurement(self):
        self.advance(60, None)
        self.assertEqual(self.sensor._accumulated_kwh, 0)
        self.assertFalse(self.sensor.available)
        self.advance(60, 1000)
        self.assertEqual(self.sensor._accumulated_kwh, 0)
        self.advance(60, 1000)
        self.assertAlmostEqual(self.sensor._accumulated_kwh, 1 / 60)

    def test_nonfinite_and_negative_accumulator_power_are_rejected(self):
        for value in (math.nan, math.inf, -1):
            self.power = value
            self.assertIsNone(self.sensor._get_power_w())

    def test_inverter_phase_output_is_not_a_grid_meter(self):
        data = {
            "ems_heartbeat": proto_decoder.EmsHeartbeatData(
                phase_a=proto_decoder.PhaseData(act_pwr=-500),
                mppt_strings=[proto_decoder.MpptStringData(power_w=2000)],
                battery_power_w=800,
            )
        }
        self.assertIsNone(backup_helpers.grid_power_w(data))
        self.assertIsNone(backup_helpers.load_power_w(data))
        self.assertEqual(backup_helpers.solar_power_w(data), 2000)
        self.assertEqual(backup_helpers.battery_power_w(data), -800)

    def test_old_ems_does_not_keep_daytime_solar_alive_on_fresh_battery_updates(self):
        data = {
            "ems_heartbeat": proto_decoder.EmsHeartbeatData(
                mppt_strings=[proto_decoder.MpptStringData(power_w=2000)]
            ),
            "ems_heartbeat_observed_at": self.now - timedelta(hours=4),
            "batteries_observed_at": self.now,
        }
        self.assertIsNone(backup_helpers.solar_power_w(data))

    def test_missing_grid_is_not_evidence_of_grid_quiet(self):
        self.assertFalse(
            backup_helpers._sample_indicates_grid_quiet(
                SimpleNamespace(grid_power_w=None), grid_power_threshold_w=50
            )
        )

    def test_power_sensor_exposes_acquisition_time_not_entity_change_time(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "custom_components/ecoflow_powerocean/sensor.py"
        )
        tree = ast.parse(source.read_text())
        cls = next(
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "EcoFlowSystemSensor"
        )
        method = next(
            n
            for n in cls.body
            if isinstance(n, ast.FunctionDef) and n.name == "extra_state_attributes"
        )
        scope = {
            "Any": Any,
            "DATA_ENERGY_STREAM": "energy_stream",
            "DATA_ENERGY_STREAM_OBSERVED_AT": "energy_stream_observed_at",
            "DATA_EMS_HEARTBEAT_OBSERVED_AT": "ems_heartbeat_observed_at",
            "grid_power_w": backup_helpers.grid_power_w,
        }
        exec(
            compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"),
            scope,
        )
        Fixture = type(
            "Fixture", (), {"extra_state_attributes": scope["extra_state_attributes"]}
        )
        fixture = Fixture()
        fixture.entity_description = SimpleNamespace(
            key="grid_power", uses_coordinator_data=True
        )
        fixture.coordinator = SimpleNamespace(
            data={
                "energy_stream": proto_decoder.EnergyStreamData(source="detail_report"),
                "energy_stream_observed_at": self.now,
            }
        )
        self.assertEqual(
            fixture.extra_state_attributes,
            {
                "power_source": "detail_report",
                "power_observed_at": self.now.isoformat(),
            },
        )
