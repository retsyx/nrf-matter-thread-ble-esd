#!/usr/bin/env python3
"""Write UF2 contents to one identified XIAO nRF52840, without copying metadata.

flash.py runs the complete configuration/artifact check before invoking this
helper. This helper also validates UF2 addresses/family before discovering USB.
Use the installed Nordic Python (including psutil); never installs tools.
"""
from __future__ import annotations

import argparse
import errno
import importlib.util
import os
from pathlib import Path
import sys

BOARD_IDS = frozenset(('Seeed_XIAO_nRF52840', 'Seeed_XIAO_nRF52840_Sense'))
FAT_TYPES = frozenset(('vfat', 'fat', 'msdos', 'msdosfs', 'fat32'))
CHUNK_SIZE = 16384


def read_board_id(mount: Path) -> str:
    lines = (mount / 'INFO_UF2.TXT').read_text().splitlines()
    ids = [value.strip() for line in lines
           for key, separator, value in [line.partition(':')]
           if separator and key.strip() == 'Board-ID']
    if len(ids) != 1 or not ids[0]:
        raise RuntimeError(f'{mount}: INFO_UF2.TXT must contain exactly one Board-ID')
    return ids[0]


def find_mount(board_id: str | None = None) -> tuple[Path, str]:
    if board_id is not None and board_id not in BOARD_IDS:
        raise RuntimeError('--board-id must identify Seeed_XIAO_nRF52840 or Seeed_XIAO_nRF52840_Sense')
    try:
        import psutil
    except ImportError as exc:
        raise RuntimeError('Installed Nordic Python lacks psutil. No tools installed.') from exc

    matches: dict[Path, str] = {}
    found: dict[Path, str] = {}
    for part in psutil.disk_partitions():
        if part.fstype.lower() not in FAT_TYPES:
            continue
        mount = Path(part.mountpoint).resolve()
        try:
            current_id = read_board_id(mount)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RuntimeError(f'Cannot read {mount}/INFO_UF2.TXT: {exc}') from exc
        found[mount] = current_id
        if current_id in BOARD_IDS and (board_id is None or current_id == board_id):
            matches[mount] = current_id
    if not matches:
        detail = '; found ' + ', '.join(f'{path} ({identity})' for path, identity in found.items()) if found else ''
        raise RuntimeError('No matching XIAO nRF52840 UF2 drive found' + detail)
    if len(matches) != 1:
        raise RuntimeError('Multiple matching XIAO nRF52840 UF2 drives found: ' +
                           ', '.join(str(path) for path in matches) + '. Select one with --board-id or disconnect the others.')
    return next(iter(matches.items()))


def validate_image(image: Path) -> bytes:
    spec = importlib.util.spec_from_file_location('mailbox_build_check', Path(__file__).with_name('check-build.py'))
    if spec is None or spec.loader is None:
        raise RuntimeError('Firmware artifact checker is missing')
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    # Validate the exact immutable bytes subsequently written, even if another
    # build replaces the output file during this operation.
    contents = image.read_bytes()
    data = checker.read_uf2(contents)
    checker.check_flash(data, 'nrf52840', 'UF2')
    return contents


def write_contents(destination: Path, contents: bytes) -> int:
    """Handle short writes and retain every write/fsync/close failure."""
    accepted = 0
    descriptor = None
    failure = None
    failed_phase = 'open'
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC |
                             getattr(os, 'O_NOFOLLOW', 0), 0o600)
        failed_phase = 'write'
        while accepted < len(contents):
            chunk = contents[accepted:accepted + CHUNK_SIZE]
            try:
                count = os.write(descriptor, chunk)
            except InterruptedError:
                continue
            if not 0 < count <= len(chunk):
                raise OSError(errno.EIO, 'UF2 write returned an invalid byte count')
            accepted += count
        failed_phase = 'fsync'
        os.fsync(descriptor)
    except OSError as exc:
        failure = exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as exc:
                if failure is None:
                    failure, failed_phase = exc, 'close'
                else:
                    failure.add_note(f'Closing the UF2 file also failed: {exc}')
    if failure is not None:
        detail = (' The drive may have disconnected after receiving the final block; '
                  'verify the application before treating the transfer as successful.'
                  if accepted == len(contents) else ' The transfer is incomplete.')
        raise RuntimeError(f'UF2 {failed_phase} failed after accepting {accepted:,}/{len(contents):,} bytes: '
                           f'{failure}.{detail}') from failure
    return accepted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--board-id', choices=sorted(BOARD_IDS))
    args = parser.parse_args()
    contents = validate_image(args.image)
    mount, identity = find_mount(args.board_id)
    # Re-read just before opening the output, detecting an ejection/replacement
    # between discovery and writing rather than writing to a different volume.
    if read_board_id(mount) != identity:
        raise RuntimeError(f'{mount}: Board-ID changed after discovery; no firmware written')
    print(f'Writing UF2 contents to {mount} ({identity})', flush=True)
    accepted = write_contents(mount / args.image.name, contents)
    print(f'Accepted all {accepted:,} bytes and synchronized the file contents. '
          'The UF2 bootloader normally disconnects to run the application.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
