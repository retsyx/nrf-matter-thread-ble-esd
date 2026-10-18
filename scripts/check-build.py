#!/usr/bin/env python3
"""Check built firmware configuration and flash/RAM ranges without hardware."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import struct
import sys

from nordic import BOARDS, ROOT, build_directory
from provisioning import config_text, load as load_provisioning

APPLICATION_REGIONS = {'nrf52840': (0x27000, 0xEC000), 'nrf54l15': (0, 0x170000)}
RAM_REGION = (0x20000000, 0x20040000)
NRF52840_UICR = {
    0x10001200: 'PSELRESET[0]',
    0x10001204: 'PSELRESET[1]',
    0x10001208: 'APPROTECT',
}


def pairing_configuration(args: argparse.Namespace) -> dict[str, str]:
    """Read the requested label, independently of the generated build headers."""
    if getattr(args, 'default_provisioning', False):
        return configuration(ROOT / 'config/default-provisioning.conf') | {
            'CONFIG_CHIP_DEVICE_VENDOR_ID': '65521',
            'CONFIG_CHIP_DEVICE_PRODUCT_ID': '32772',
        }
    path = (args.provisioning.expanduser().resolve() if getattr(args, 'provisioning', None) else
            ROOT / 'provisioning' / args.board / 'provisioning.json')
    values = {}
    for line in config_text(load_provisioning(path)).splitlines():
        if line.startswith('CONFIG_') and '=' in line:
            name, value = line.split('=', 1)
            values[name] = value
    return values | {'CONFIG_CHIP_DEVICE_VENDOR_ID': '65521', 'CONFIG_CHIP_DEVICE_PRODUCT_ID': '32772'}


def check_pairing(actual: dict[str, str], expected: dict[str, str], *, compiled: bool = False) -> None:
    """Compare PIN, discriminator, salt, iterations and verifier as one identity."""
    for name, value in expected.items():
        raw = actual.get(name)
        require(raw is not None, f'{name}: missing pairing definition')
        if value.startswith('"'):
            require(json.loads(raw) == json.loads(value), f'{name}: differs from the selected provisioning')
        else:
            radix = 0 if compiled or name != 'CONFIG_CHIP_DEVICE_DISCRIMINATOR' else 16
            require(int(raw, radix) == int(value, 0), f'{name}: differs from the selected provisioning')


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def configuration(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        if line.startswith('CONFIG_') and '=' in line:
            name, value = line.split('=', 1)
            values[name] = value
        elif line.startswith('# CONFIG_') and line.endswith(' is not set'):
            values[line[2:-11]] = 'n'
    return values


def header_define(header: str, name: str) -> str:
    """Read one object-like macro, rejecting missing or ambiguous definitions."""
    matches = re.findall(rf'^[ \t]*#define[ \t]+{re.escape(name)}[ \t]+([^\n]*)$',
                         header, re.MULTILINE)
    require(len(matches) == 1, f'{name}: expected one generated devicetree definition')
    return re.sub(r'/\*.*?\*/|//.*', '', matches[0]).strip()


def header_integer(header: str, name: str) -> int:
    value = header_define(header, name)
    require(re.fullmatch(r'(?:0[xX][0-9a-fA-F]+|[0-9]+)', value) is not None,
            f'{name}: expected a generated integer literal, found {value!r}')
    return int(value, 16 if value.lower().startswith('0x') else 10)


def check_application_partition(config: dict[str, str], board: str,
                                devicetree_header: Path | None = None) -> None:
    """Check the partition used by the linker for fixed and mapped flash."""
    if config.get('CONFIG_FLASH_USES_MAPPED_PARTITION') == 'y':
        require(devicetree_header is not None,
                'Mapped flash requires the generated devicetree header')
        header = devicetree_header.read_text()
        node = header_define(header, 'DT_CHOSEN_zephyr_code_partition')
        require(re.fullmatch(r'DT_N(?:_[A-Za-z0-9_]+)?', node) is not None,
                'Unexpected zephyr,code-partition node identifier')
        for name in ('DT_CHOSEN_zephyr_code_partition_EXISTS', f'{node}_EXISTS',
                     f'{node}_STATUS_okay', f'{node}_COMPAT_MATCHES_zephyr_mapped_partition',
                     f'{node}_REG_NUM', f'{node}_REG_IDX_0_EXISTS'):
            require(header_integer(header, name) == 1,
                    f'{name}: chosen code partition must be enabled and mapped with one register range')
        address = header_integer(header, f'{node}_REG_IDX_0_VAL_ADDRESS')
        size = header_integer(header, f'{node}_REG_IDX_0_VAL_SIZE')
    else:
        for name in ('CONFIG_FLASH_LOAD_OFFSET', 'CONFIG_FLASH_LOAD_SIZE'):
            require(name in config, f'{name}: missing fixed-partition flash configuration')
        address = int(config['CONFIG_FLASH_LOAD_OFFSET'], 0)
        size = int(config['CONFIG_FLASH_LOAD_SIZE'], 0)
    start, end = APPLICATION_REGIONS[board]
    require(address == start,
            f'Application partition starts at {address:#x}; expected {start:#x}')
    require(size == end - start,
            f'Application partition size is {size:#x}; expected {end - start:#x}')


def check_configuration(config: dict[str, str], args: argparse.Namespace,
                        devicetree_header: Path | None = None,
                        pairing: dict[str, str] | None = None) -> None:
    usb_console = getattr(args, 'usb_console', False)
    console = args.console or usb_console
    expected = {
        'CONFIG_BOARD_TARGET': f'"{BOARDS[args.board]}"',
        'CONFIG_CHIP': 'y',
        'CONFIG_CHIP_FACTORY_DATA': 'n',
        'CONFIG_CHIP_FACTORY_DATA_NONE': 'y',
        'CONFIG_CHIP_FACTORY_RESET_ERASE_SETTINGS': 'n',
        'CONFIG_REBOOT': 'y',
        'CONFIG_CHIP_ENABLE_ICD_SUPPORT': 'y',
        'CONFIG_OPENTHREAD_MTD_SED': 'y',
        'CONFIG_USE_DT_CODE_PARTITION': 'y',
        'CONFIG_CHIP_ICD_LIT_SUPPORT': 'n',
        'CONFIG_CHIP_ICD_IDLE_MODE_DURATION': '600',
        'CONFIG_CHIP_ICD_ACTIVE_MODE_DURATION': '0',
        'CONFIG_CHIP_ICD_ACTIVE_MODE_THRESHOLD': '0',
        'CONFIG_CHIP_ICD_SLOW_POLL_INTERVAL': '15000',
        'CONFIG_CHIP_ICD_FAST_POLLING_INTERVAL': '200',
        'CONFIG_BOOTLOADER_MCUBOOT': 'n',
        'CONFIG_CHIP_BOOTLOADER_NONE': 'y',
        'CONFIG_SHELL': 'y' if console else 'n',
        'CONFIG_SERIAL': 'y' if console else 'n',
        'CONFIG_CONSOLE': 'y' if console else 'n',
        'CONFIG_UART_CONSOLE': 'y' if console else 'n',
        'CONFIG_LOG_BACKEND_UART': 'y' if console else 'n',
        'CONFIG_LOG_BACKEND_RTT': 'n' if console else 'y',
        'CONFIG_LOG_BACKEND_RTT_MODE_DROP': 'n' if console else 'y',
        'CONFIG_USB_DEVICE_STACK': 'y' if usb_console else 'n',
        'CONFIG_USB_DEVICE_STACK_NEXT': 'n',
        'CONFIG_BOARD_SERIAL_BACKEND_CDC_ACM': 'n',
        'CONFIG_BOOT_DELAY': '5000' if usb_console else '0',
        'CONFIG_MAILBOX_BOOT_DIAGNOSTICS': 'y' if usb_console else 'n',
        'CONFIG_MAILBOX_BATTERY_INSTALLED': 'y' if args.battery else 'n',
        'CONFIG_SRAM_SIZE': '256',
    }
    if usb_console:
        expected.update({
            'CONFIG_USB_CDC_ACM': 'y',
            'CONFIG_USB_DEVICE_INITIALIZE_AT_BOOT': 'y',
            'CONFIG_SHELL_BACKEND_SERIAL_CHECK_DTR': 'n',
            'CONFIG_SHELL_LOG_BACKEND': 'n',
            'CONFIG_USB_DEVICE_REMOTE_WAKEUP': 'n',
            'CONFIG_USB_CDC_ACM_RINGBUF_SIZE': '8192',
            'CONFIG_LOG_BUFFER_SIZE': '16384',
            'CONFIG_LOG_PROCESS_THREAD_STARTUP_DELAY_MS': '4000',
        })
    if args.board == 'nrf52840':
        expected.update({
            'CONFIG_NORDIC_QSPI_NOR': 'n',
            'CONFIG_NRFX_QSPI': 'n',
            'CONFIG_PM_EXTERNAL_FLASH_HAS_DRIVER': 'n',
        })
    for name, value in expected.items():
        # Zephyr omits some symbols whose dependencies are disabled entirely.
        require(config.get(name, 'n') == value,
                f'{name}: expected {value}, found {config.get(name, "missing")}')
    check_pairing(config, pairing if pairing is not None else pairing_configuration(args))
    require(int(config['CONFIG_SRAM_BASE_ADDRESS'], 0) == RAM_REGION[0], 'Unexpected SRAM base address')
    require(int(config['CONFIG_FLASH_BASE_ADDRESS'], 0) == 0, 'Unexpected flash base address')
    check_application_partition(config, args.board, devicetree_header)
    if args.board == 'nrf52840':
        require(config.get('CONFIG_BUILD_OUTPUT_UF2') == 'y', 'nRF52840 UF2 generation is disabled')


def check_compiled_pairing(path: Path, pairing: dict[str, str]) -> None:
    """Check the C literals actually compiled, including stale generated headers."""
    header = path.read_text()
    values = dict(re.findall(r'^#define\s+(CONFIG_\w+)\s+([^\n]+)$', header, re.MULTILINE))
    check_pairing(values, pairing, compiled=True)


def read_hex(path: Path) -> dict[int, int]:
    """Read Intel HEX, validating lengths, checksums, address records and EOF."""
    result: dict[int, int] = {}
    upper = 0
    eof = False
    for number, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        prefix = f'{path.name}:{number}'
        require(not eof, f'{prefix}: data after EOF record')
        require(line.startswith(':'), f'{prefix}: expected Intel HEX record')
        try:
            record = bytes.fromhex(line[1:])
        except ValueError as exc:
            raise RuntimeError(f'{prefix}: invalid hexadecimal record') from exc
        require(len(record) >= 5 and len(record) == record[0] + 5, f'{prefix}: invalid record length')
        require(sum(record) & 0xFF == 0, f'{prefix}: checksum mismatch')
        length, address, kind = record[0], int.from_bytes(record[1:3], 'big'), record[3]
        data = record[4:-1]
        if kind == 0:
            require(address + length <= 0x10000, f'{prefix}: data crosses its address segment')
            for offset, value in enumerate(data):
                target = upper + address + offset
                require(target not in result or result[target] == value, f'{prefix}: conflicting data at {target:#x}')
                result[target] = value
        elif kind == 1:
            require(length == 0 and address == 0, f'{prefix}: malformed EOF record')
            eof = True
        elif kind in (2, 4):
            require(length == 2 and address == 0, f'{prefix}: malformed extended address record')
            upper = int.from_bytes(data, 'big') << (4 if kind == 2 else 16)
        elif kind in (3, 5):
            require(length == 4 and address == 0, f'{prefix}: malformed start-address record')
        else:
            raise RuntimeError(f'{prefix}: unsupported record type {kind}')
    require(eof and result, f'{path.name}: missing EOF or application data')
    return result


def check_flash(data: dict[int, int], board: str, label: str) -> None:
    start, end = APPLICATION_REGIONS[board]
    outside = sorted(address for address in data if not start <= address < end)
    if outside:
        address = outside[0]
        register = NRF52840_UICR.get(address & ~3) if board == 'nrf52840' else None
        detail = f' (UICR {register}; requires a specific review of the register value)' if register else ''
        raise RuntimeError(f'{label} programs {address:#010x}{detail}; allowed application range is '
                           f'[{start:#x}, {end:#x}). Bootloader, settings and UICR writes are rejected.')


def read_uf2(source: Path | bytes) -> dict[int, int]:
    raw = source if isinstance(source, bytes) else source.read_bytes()
    require(bool(raw) and len(raw) % 512 == 0, 'UF2 must contain complete 512-byte blocks')
    count = len(raw) // 512
    seen = set()
    result: dict[int, int] = {}
    for offset in range(0, len(raw), 512):
        block = raw[offset:offset + 512]
        magic0, magic1, flags, address, size, number, total, family = struct.unpack_from('<8I', block)
        require((magic0, magic1, struct.unpack_from('<I', block, 508)[0]) ==
                (0x0A324655, 0x9E5D5157, 0x0AB16F30), 'UF2 magic mismatch')
        require(flags == 0x2000 and family == 0xADA52840, 'UF2 requires the nRF52840 family ID and ordinary flash blocks')
        require(0 < size <= 476 and total == count and number < count and number not in seen,
                'UF2 block size/count/number is invalid')
        seen.add(number)
        for index, value in enumerate(block[32:32 + size]):
            target = address + index
            require(target not in result or result[target] == value, f'Conflicting UF2 blocks at {target:#x}')
            result[target] = value
    require(len(seen) == count, 'UF2 block numbering is incomplete')
    return result


def check_elf_ram(path: Path, config: dict[str, str]) -> int:
    raw = path.read_bytes()
    require(raw[:6] == b'\x7fELF\x01\x01', 'Expected a little-endian ELF32 firmware image')
    require(len(raw) >= 52, 'Truncated ELF header')
    offset = struct.unpack_from('<I', raw, 28)[0]
    entry_size, count = struct.unpack_from('<HH', raw, 42)
    require(entry_size >= 32 and offset + entry_size * count <= len(raw), 'Truncated ELF program headers')
    base = int(config['CONFIG_SRAM_BASE_ADDRESS'], 0)
    end = base + int(config['CONFIG_SRAM_SIZE'], 0) * 1024
    used = 0
    for index in range(count):
        kind, file_offset, address, _, file_size, memory_size, _, _ = struct.unpack_from('<8I', raw, offset + index * entry_size)
        if kind != 1 or memory_size == 0:
            continue
        require(file_offset + file_size <= len(raw), 'ELF load segment exceeds its file')
        if 0x20000000 <= address < 0x30000000:
            require(base <= address and address + memory_size <= end,
                    f'ELF RAM segment [{address:#x}, {address + memory_size:#x}) exceeds configured SRAM')
            used += memory_size
    require(used > 0, 'ELF has no SRAM load segments')
    return used


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board', choices=BOARDS, default='nrf52840')
    console = parser.add_mutually_exclusive_group()
    console.add_argument('--console', action='store_true')
    console.add_argument('--usb-console', action='store_true')
    parser.add_argument('--battery', action='store_true')
    parser.add_argument('--build-dir', type=Path)
    provisioning = parser.add_mutually_exclusive_group()
    provisioning.add_argument('--provisioning', type=Path, help='custom provisioning.json used for this build')
    provisioning.add_argument('--default-provisioning', action='store_true',
                              help='require the published development credentials')
    args = parser.parse_args()
    build = build_directory(args) / 'zephyr'
    config = configuration(build / '.config')
    pairing = pairing_configuration(args)
    check_configuration(config, args, build / 'include/generated/zephyr/devicetree_generated.h', pairing)
    check_compiled_pairing(build / 'include/generated/zephyr/autoconf.h', pairing)
    image = read_hex(build / 'zephyr.hex')
    check_flash(image, args.board, 'HEX')
    ram = check_elf_ram(build / 'zephyr.elf', config)
    if args.board == 'nrf52840':
        uf2 = read_uf2(build / 'zephyr.uf2')
        check_flash(uf2, args.board, 'UF2')
        require(all(uf2.get(address) == value for address, value in image.items()), 'UF2 payload differs from the HEX firmware')
    start, end = APPLICATION_REGIONS[args.board]
    print(f'{args.board}: configuration and firmware checks passed')
    print(f'HEX: {len(image):,} data bytes in [{min(image):#x}, {max(image) + 1:#x}); '
          f'allowed [{start:#x}, {end:#x})')
    print(f'ELF RAM segments: {ram:,} bytes within {int(config["CONFIG_SRAM_SIZE"], 0)} KiB SRAM')
    if args.board == 'nrf52840':
        print('UF2: nRF52840 family, protected flash ranges and HEX payload match')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, KeyError, ValueError, struct.error) as exc:
        print(f'Build check failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
