#!/usr/bin/env python3
"""Build the Nordic port using installed NCS tools. Never installs tools."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from nordic import BOARDS, ROOT, add_options, build_directory, installed_tools, run
from generate import installed_zap


def validate_build_paths(*paths) -> None:
    for path in paths:
        if any(character.isspace() or character == ';' for character in str(path)):
            raise RuntimeError(f'Nordic/Zephyr builds require paths without whitespace or semicolons: {path}. '
                               'Use a supported location for the project, build directory, SDK and toolchain.')


def validate_build_cache(build: Path, sdk: Path, python: Path, env: dict[str, str]) -> None:
    cache = build / 'CMakeCache.txt'
    if not cache.is_file():
        return
    values = {}
    for line in cache.read_text().splitlines():
        key, separator, value = line.partition('=')
        if separator and ':' in key:
            values[key.split(':', 1)[0]] = value
    expected = {
        'CMAKE_HOME_DIRECTORY': ROOT,
        'ZEPHYR_BASE': sdk / 'zephyr',
        'Zephyr_DIR': sdk / 'zephyr/share/zephyr-package/cmake',
        'Python3_EXECUTABLE': python,
        '_Python3_EXECUTABLE': python,
    }
    for key, command in (('CMAKE_COMMAND', 'cmake'), ('CMAKE_MAKE_PROGRAM', 'ninja'),
                         ('GN_EXECUTABLE', 'gn'), ('GIT_EXECUTABLE', 'git'), ('DTC', 'dtc'),
                         ('CMAKE_C_COMPILER', 'arm-zephyr-eabi-gcc'),
                         ('CMAKE_CXX_COMPILER', 'arm-zephyr-eabi-g++')):
        executable = shutil.which(command, path=env['PATH'])
        if executable:
            expected[key] = Path(executable)
    for key, path in expected.items():
        if key in values and Path(values[key]).resolve() != path.resolve():
            raise RuntimeError(f'{build} caches a different project, SDK or toolchain ({key}). '
                               'Run the build again with --pristine.')


def pristine_removed_sdk_cache(build: Path, sdk: Path, env: dict[str, str], *, dry_run: bool) -> None:
    """Clean a recognized Zephyr build even after its original SDK is removed."""
    cache = build / 'CMakeCache.txt'
    if not cache.is_file():
        return
    values = {}
    for line in cache.read_text().splitlines():
        key, separator, value = line.partition('=')
        if separator and ':' in key:
            values[key.split(':', 1)[0]] = value
    old_base = values.get('ZEPHYR_BASE')
    if not old_base or (Path(old_base) / 'cmake/pristine.cmake').is_file():
        return
    source = values.get('APPLICATION_SOURCE_DIR') or values.get('CMAKE_HOME_DIRECTORY')
    if not source:
        raise RuntimeError(f'Refusing to pristine {build}: missing cached application source directory.')
    if ROOT.resolve().is_relative_to(build.resolve()) or Path(source).resolve().is_relative_to(build.resolve()):
        raise RuntimeError(f'Refusing to pristine {build}: it contains application source files.')
    cmake = shutil.which('cmake', path=env['PATH'])
    run([cmake, f'-DBINARY_DIR={build}', f'-DSOURCE_DIR={source}',
         '-P', str(sdk / 'zephyr/cmake/pristine.cmake')],
        cwd=ROOT, env=env, dry_run=dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_options(parser)
    parser.add_argument('-j', '--jobs', type=int, default=min(os.cpu_count() or 4, 8))
    parser.add_argument('--pristine', action='store_true', help='reconfigure the selected build from scratch')
    parser.add_argument('--zap', type=Path, help='installed ZAP CLI (or ZAP_CLI / zap-cli on PATH)')
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    build = build_directory(args)
    validate_build_paths(ROOT, build)
    sdk, python, env = installed_tools(args, compiler=True)
    validate_build_paths(sdk, python, env['ZEPHYR_SDK_INSTALL_DIR'])
    if not args.pristine:
        validate_build_cache(build, sdk, python, env)
    zap = installed_zap(args.zap)
    if args.pristine:
        pristine_removed_sdk_cache(build, sdk, env, dry_run=args.dry_run)
    command = [str(python), '-m', 'west', 'build', '--no-sysbuild', '-b', BOARDS[args.board],
               '-s', str(ROOT), '-d', str(build), '-p', 'always' if args.pristine else 'auto',
               '--', '-GNinja', '-DNCS_TOOLCHAIN_VERSION=NONE',
               f'-DZephyr_DIR={sdk / "zephyr/share/zephyr-package/cmake"}',
               f'-DPython3_EXECUTABLE={python}',
               f'-DMAILBOX_ZAP_EXECUTABLE={zap}',
               f'-DUSER_CACHE_DIR={ROOT / "target/build/.cache"}',
               '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON']
    configs = [ROOT / 'config' / f'{name}.conf'
               for name in ('console', 'battery') if getattr(args, name)]
    if args.usb_console:
        configs.append(ROOT / 'config/usb-console.conf')
    if configs:
        command.append('-DEXTRA_CONF_FILE=' + ';'.join(str(config) for config in configs))
    if args.console:
        command.append(f'-DEXTRA_DTC_OVERLAY_FILE={ROOT / "boards" / ("console_" + args.board + ".overlay")}')
    elif args.usb_console:
        command.append(f'-DEXTRA_DTC_OVERLAY_FILE={ROOT / "boards/usb_console_nrf52840.overlay"}')
    if args.board == 'nrf52840':
        command.append('-DCONFIG_BUILD_OUTPUT_UF2=y')
    # CMAKE_BUILD_PARALLEL_LEVEL also applies to Matter's nested builds.
    env['CMAKE_BUILD_PARALLEL_LEVEL'] = str(args.jobs)
    run(command, cwd=sdk, env=env, dry_run=args.dry_run)
    if not args.dry_run:
        print(f'Firmware: {build / "zephyr/zephyr.hex"}')
        if args.board == 'nrf52840':
            print(f'UF2: {build / "zephyr/zephyr.uf2"}')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
