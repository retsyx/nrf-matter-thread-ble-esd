"""Check that fixed and mapped firmware partitions stay in the application range."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
NORDIC_SPEC = importlib.util.spec_from_file_location('checker_nordic', SCRIPTS / 'nordic.py')
assert NORDIC_SPEC is not None and NORDIC_SPEC.loader is not None
nordic = importlib.util.module_from_spec(NORDIC_SPEC)
NORDIC_SPEC.loader.exec_module(nordic)
SPEC = importlib.util.spec_from_file_location('firmware_checker', SCRIPTS / 'check-build.py')
assert SPEC is not None and SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
with mock.patch.dict(sys.modules, {'nordic': nordic}):
    SPEC.loader.exec_module(checker)


def mapped_header(node: str = 'DT_N_S_custom_app', address: int = 0,
                  size: int = 0x170000) -> str:
    # The chosen partition deliberately has no slot0 label: the checker must
    # follow zephyr,code-partition instead of accepting any matching slot.
    return '\n'.join([
        f'#define DT_CHOSEN_zephyr_code_partition {node}',
        '#define DT_CHOSEN_zephyr_code_partition_EXISTS 1',
        f'#define {node}_EXISTS 1',
        f'#define {node}_STATUS_okay 1',
        f'#define {node}_COMPAT_MATCHES_zephyr_mapped_partition 1',
        f'#define {node}_REG_NUM 1',
        f'#define {node}_REG_IDX_0_EXISTS 1',
        f'#define {node}_REG_IDX_0_VAL_ADDRESS {address} /* generated address */',
        f'#define {node}_REG_IDX_0_VAL_SIZE {size:#x} /* generated size */',
    ]) + '\n'


class ApplicationPartitionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix='firmware-check-')
        self.addCleanup(temporary.cleanup)
        self.header = Path(temporary.name) / 'devicetree_generated.h'

    def check_mapped(self, header: str) -> None:
        self.header.write_text(header)
        checker.check_application_partition(
            {'CONFIG_FLASH_USES_MAPPED_PARTITION': 'y'}, 'nrf54l15', self.header)

    def test_legacy_fixed_partition_needs_no_header(self) -> None:
        checker.check_application_partition(
            {'CONFIG_FLASH_LOAD_OFFSET': '0x27000', 'CONFIG_FLASH_LOAD_SIZE': '0xc5000'},
            'nrf52840')

    def test_legacy_fixed_partition_rejects_bootloader_or_settings_overlap(self) -> None:
        for offset, size in [('0', '0xec000'), ('0x27000', '0xcd000')]:
            with self.subTest(offset=offset, size=size), self.assertRaises(RuntimeError):
                checker.check_application_partition(
                    {'CONFIG_FLASH_LOAD_OFFSET': offset, 'CONFIG_FLASH_LOAD_SIZE': size}, 'nrf52840')

    def test_legacy_fixed_partition_rejects_missing_range(self) -> None:
        with self.assertRaisesRegex(RuntimeError, 'missing fixed-partition'):
            checker.check_application_partition({'CONFIG_FLASH_LOAD_OFFSET': '0'}, 'nrf54l15')

    def test_mapped_partition_follows_chosen_node_without_slot0_label(self) -> None:
        self.check_mapped(mapped_header())

    def test_mapped_partition_requires_header(self) -> None:
        with self.assertRaisesRegex(RuntimeError, 'requires the generated'):
            checker.check_application_partition({'CONFIG_FLASH_USES_MAPPED_PARTITION': 'y'}, 'nrf54l15')

    def test_matching_slot0_does_not_mask_wrong_chosen_partition(self) -> None:
        correct_slot = mapped_header('DT_N_S_slot0').splitlines()[2:]
        wrong_chosen = mapped_header('DT_N_S_storage', address=0x170000, size=0xd000)
        with self.assertRaisesRegex(RuntimeError, 'starts at'):
            self.check_mapped(wrong_chosen + '\n'.join(correct_slot))

    def test_mapped_range_ignores_stale_legacy_config(self) -> None:
        self.header.write_text(mapped_header(size=0x17d000))
        with self.assertRaisesRegex(RuntimeError, 'partition size'):
            checker.check_application_partition({
                'CONFIG_FLASH_USES_MAPPED_PARTITION': 'y',
                'CONFIG_FLASH_LOAD_OFFSET': '0', 'CONFIG_FLASH_LOAD_SIZE': '0x170000',
            }, 'nrf54l15', self.header)

    def test_mapped_partition_rejects_short_or_long_image_region(self) -> None:
        for size in [0, 0x160000, 0x17d000]:
            with self.subTest(size=size), self.assertRaisesRegex(RuntimeError, 'partition size'):
                self.check_mapped(mapped_header(size=size))

    def test_mapped_partition_rejects_missing_or_disabled_metadata(self) -> None:
        for suffix in ['_EXISTS', '_STATUS_okay', '_COMPAT_MATCHES_zephyr_mapped_partition',
                       '_REG_NUM', '_REG_IDX_0_EXISTS', '_REG_IDX_0_VAL_ADDRESS', '_REG_IDX_0_VAL_SIZE']:
            macro = f'#define DT_N_S_custom_app{suffix} '
            lines = mapped_header().splitlines()
            for replacement in [None, '0']:
                changed = '\n'.join(line if not line.startswith(macro) else
                                    '' if replacement is None else macro + replacement for line in lines)
                if suffix == '_REG_IDX_0_VAL_ADDRESS' and replacement == '0':
                    continue  # Zero is the correct application address.
                with self.subTest(suffix=suffix, replacement=replacement), self.assertRaises(RuntimeError):
                    self.check_mapped(changed)

    def test_mapped_partition_rejects_missing_chosen_or_duplicate_definitions(self) -> None:
        header = mapped_header()
        for altered in ['\n'.join(header.splitlines()[1:]),
                        header + '#define DT_CHOSEN_zephyr_code_partition DT_N_S_custom_app\n']:
            with self.subTest(header=altered), self.assertRaises(RuntimeError):
                self.check_mapped(altered)

    def test_mapped_partition_rejects_expressions_or_invalid_literals(self) -> None:
        for value in ['(0x170000)', '1507328 + 1', '-1', 'invalid']:
            altered = mapped_header().replace('0x170000 /* generated size */', value)
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, 'integer literal'):
                self.check_mapped(altered)


if __name__ == '__main__':
    unittest.main()
