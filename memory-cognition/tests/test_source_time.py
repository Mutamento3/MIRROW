"""Mixed historical timestamps must preserve actual chronology across consumers."""
import unittest

from memory_v2.source_time import instant
from memory_v2.store import MemoryV2Store
from memory_v2 import day_settlement, day_settlement_assembler, day_settlement_candidates, linking, day_repair_staging


class SourceTimeTests(unittest.TestCase):
    def test_mixed_legacy_and_offset_values_have_one_instant(self):
        parsers = [instant, MemoryV2Store._event_time_key, day_settlement._timestamp,
                   day_settlement_assembler._timestamp, day_settlement_candidates._timestamp,
                   linking._timestamp, day_repair_staging._event_time_key]
        for parse in parsers:
            with self.subTest(parse=parse.__module__):
                self.assertEqual(parse('2026-10-14T06:08:00'), parse('2026-10-14T06:08:00+08:00'))
                self.assertEqual(parse('2026-10-14T06:08:00'), parse('2026-10-13T22:08:00Z'))
                self.assertLess(parse('2026-10-14T06:08:00'), parse('2026-10-14T11:05:00+08:00'))

    def test_invalid_timestamp_is_not_an_ordering_fallback(self):
        with self.assertRaises(ValueError):
            instant('unknown')
