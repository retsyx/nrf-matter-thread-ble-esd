#!/usr/bin/env python3
"""Flash checked firmware using installed SWD tools or a UF2 content writer."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

from nordic import BOARDS, ROOT, add_options, build_directory, installed_tools, run
from uf2_flash import BOARD_IDS


def jlink_executable(env: dict[str, str], override: Path | None) -> Path:
    if override:
        candidates = [override.expanduser()]
    else:
        found = shutil.which('JLinkExe', path=env['PATH'])
        candidates = ([Path(found)] if found else []) + [Path('/Applications/SEGGER/JLink/JLinkExe')]
        candidates += sorted(Path('/Applications/SEGGER').glob('JLink_V*/JLinkExe'), reverse=True)
        candidates += [Path('/opt/SEGGER/JLink/JLinkExe')]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError('Installed J-Link Commander not found. Pass --commander with its existing path. No tools installed.')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_options(parser)
    parser.add_argument('--runner', choices=('jlink', 'uf2', 'pyocd'), default='jlink')
    parser.add_argument('--serial', help='SWD probe serial number')
    parser.add_argument('--commander', type=Path, help='path to installed JLinkExe')
    parser.add_argument('--board-id', choices=sorted(BOARD_IDS),
                        help='exact XIAO nRF52840 UF2 Board-ID (standard or Sense)')
    provisioning = parser.add_mutually_exclusive_group()
    provisioning.add_argument('--provisioning', type=Path,
                              help='custom provisioning.json used for this build')
    provisioning.add_argument('--default-provisioning', action='store_true',
                              help='require the published development pairing codes')
    args = parser.parse_args()
    if args.runner == 'uf2' and args.board != 'nrf52840':
        parser.error('UF2 is supported only on the XIAO nRF52840')
    if args.runner == 'uf2' and args.serial:
        parser.error('--serial selects an SWD probe and cannot be used with UF2')
    if args.board_id and args.runner != 'uf2':
        parser.error('--board-id requires --runner uf2')
    if args.commander and args.runner != 'jlink':
        parser.error('--commander requires --runner jlink')
    build = build_directory(args)
    sdk, python, env = installed_tools(args)
    cache = build / 'CMakeCache.txt'
    if cache.is_file():
        built_board = next((line.split('=', 1)[1] for line in cache.read_text().splitlines()
                            if line.startswith('BOARD:STRING=')), None)
        if built_board != BOARDS[args.board]:
            raise RuntimeError(f'Build directory targets {built_board}, but --board selects {BOARDS[args.board]}')
    suffix = 'uf2' if args.runner == 'uf2' else 'hex'
    image = build / 'zephyr' / f'zephyr.{suffix}'
    if not args.dry_run and (not image.is_file() or not (build / 'zephyr/runners.yaml').is_file()):
        raise RuntimeError(f'Build firmware first; missing {image} or runners.yaml')
    check = [str(python), str(ROOT / 'scripts/check-build.py'),
             '--board', args.board, '--build-dir', str(build)]
    for option in ('console', 'usb_console', 'battery'):
        if getattr(args, option):
            check.append('--' + option.replace('_', '-'))
    if args.provisioning:
        check += ['--provisioning', str(args.provisioning.expanduser().resolve())]
    elif args.default_provisioning:
        check.append('--default-provisioning')
    # Validate every runner's artifact before it can access a connected device.
    # Dry runs print this command and still work without a completed build.
    run(check, cwd=ROOT, env=env, dry_run=args.dry_run)
    if args.runner == 'uf2':
        # macOS shutil.copy uses fcopyfile and then chmod. The stock UF2 runner
        # can fail during the bootloader's normal disconnect; write only data.
        command = [str(python), str(ROOT / 'scripts/uf2_flash.py'), str(image)]
        if args.board_id:
            command += ['--board-id', args.board_id]
        run(command, cwd=ROOT, env=env, dry_run=args.dry_run)
    else:
        command = [str(python), '-m', 'west', 'flash', '-d', str(build),
                   '--skip-rebuild', '--runner', args.runner]
        # J-Link defaults to a full chip erase in this SDK. Explicitly forbid it
        # so the nRF52840's stock SoftDevice and UF2 bootloader survive SWD use.
        command += ['--no-erase', '--reset']
        if args.serial:
            command += ['--dev-id', args.serial]
        if args.runner == 'jlink':
            command += ['--commander', str(jlink_executable(env, args.commander))]
        else:
            subprocess.run([str(python), '-c', 'import pyocd'], env=env, check=True)
        run(command, cwd=sdk, env=env, dry_run=args.dry_run)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
