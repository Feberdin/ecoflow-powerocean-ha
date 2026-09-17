"""Synthetic EnergyStreamDetail protocol tests, no cloud connection or real frames.

Schema: foxthefox/ioBroker.ecoflow-mqtt, ef_poweroceanplus_data.js, 96/34.
Input: protobuf batches and a fixed UTC clock. Output: newest fresh sample only.
Debug: python3 -m unittest discover -s tests -p 'test_energy_stream_detail.py' -v.
"""

from datetime import UTC, datetime
import math
import struct
import unittest
from unittest.mock import patch
from test_system_power import (
    proto_decoder,
    _field_bytes,
    _field_varint,
    _header,
    _encode_varint,
)


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 2, 1, 10, tzinfo=UTC)


def item(timestamp, solar=2000):
    fields = b"".join(
        _encode_varint((i << 3) | 5) + struct.pack("<f", v)
        for i, v in [(1, 1200), (2, -300), (3, solar), (4, -500)]
    )
    return fields + _field_varint(5, timestamp) + _field_varint(7, 40)


def message(*samples):
    return _field_bytes(
        1,
        _header(
            cmd_func=96, cmd_id=34, pdata=b"".join(_field_bytes(1, s) for s in samples)
        ),
    )


class EnergyStreamDetailTests(unittest.TestCase):
    def decode(self, raw):
        with patch.object(proto_decoder, "datetime", FixedClock, create=True):
            return proto_decoder.decode_mqtt_payload(raw)[1]

    def test_detail_batch_selects_newest_timestamp_not_last_array_entry(self):
        now = int(FixedClock.now().timestamp())
        stream = self.decode(message(item(now, 2000), item(now - 60, 1000)))
        self.assertIsNotNone(stream)
        self.assertEqual(stream.solar_w, 2000)
        self.assertEqual(stream.grid_w, -300)
        self.assertEqual(stream.load_w, 1200)
        self.assertEqual(stream.battery_w, -500)
        self.assertEqual(stream.soc, 40)
        self.assertEqual(stream.sampled_at, FixedClock.now())

    def test_historical_future_missing_and_nonfinite_samples_are_not_live(self):
        now = int(FixedClock.now().timestamp())
        for raw in (item(now - 3600), item(now + 3600), item(0), item(now, math.nan)):
            with self.subTest(sample=raw.hex()):
                self.assertIsNone(self.decode(message(raw)))

    def test_valid_night_zero_is_preserved(self):
        stream = self.decode(message(item(int(FixedClock.now().timestamp()), 0)))
        self.assertIsNotNone(stream)
        self.assertEqual(stream.solar_w, 0)
