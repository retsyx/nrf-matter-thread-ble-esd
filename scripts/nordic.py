"""Find and use existing Nordic SDK tools"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SDK_VERSION = '3.4.1'
BOARDS = {
    'nrf52840': 'xiao_ble/nrf52840',
    'nrf54l15': 'xiao_nrf54l15/nrf54l15/cpuapp',
}


def add_options(parser: argparse.ArgumentParser, *, variants: bool = True) -> None:
    parser.add_argument('--board', choices=BOARDS, default='nrf52840',
                        help='target XIAO board (default: nrf52840)')
    parser.add_argument('--sdk', type=Path,
                        help=f'existing NCS {SDK_VERSION} west workspace (or NCS_SDK_ROOT)')
    parser.add_argument('--toolchain', type=Path,
                        help='existing Nordic toolchain bundle directory')
    if variants:
        console = parser.add_mutually_exclusive_group()
        console.add_argument('--console', action='store_true',
                             help='use the diagnostic external UART console/shell build')
        console.add_argument('--usb-console', action='store_true',
                             help='use native USB CDC console/shell (nrf52840 only)')
        parser.add_argument('--battery', action='store_true',
                            help='use the build that reports a fitted LiPo battery')
        parser.add_argument('--build-dir', type=Path, help='override the build directory')
    parser.add_argument('--dry-run', action='store_true',
                        help='check installed tools and print commands without running them')


def build_directory(args: argparse.Namespace) -> Path:
    usb_console = getattr(args, 'usb_console', False)
    if usb_console and args.board != 'nrf52840':
        raise RuntimeError('--usb-console is supported only on the XIAO nRF52840')
    if args.build_dir:
        return args.build_dir.expanduser().resolve()
    suffix = ('-usb-console' if usb_console else '-console' if args.console else '')
    suffix += '-battery' if args.battery else ''
    return ROOT / 'target' / 'build' / (args.board + suffix)


def _install_roots() -> list[Path]:
    if sys.platform == 'win32':
        return [Path('C:/ncs'), Path.home() / 'ncs']
    if sys.platform == 'darwin':
        return [Path('/opt/nordic/ncs'), Path.home() / 'ncs']
    return [Path.home() / 'ncs', Path('/opt/nordic/ncs')]


def _override(args: argparse.Namespace, option: str, env_key: str) -> Path | None:
    value = getattr(args, option, None) or os.environ.get(env_key)
    return Path(value).expanduser().resolve() if value else None


def _sdk_version(version_file: Path) -> str:
    contents = version_file.read_text().strip()
    # Older NCS releases stored just the version; newer releases use Zephyr's
    # VERSION fields, with optional release metadata such as "lts".
    if re.fullmatch(r'\d+\.\d+\.\d+', contents):
        return contents
    values = {}
    for line in contents.splitlines():
        key, separator, value = line.partition('=')
        if separator:
            key = key.strip()
            if key in values:
                raise RuntimeError(f'Invalid Nordic SDK version file at {version_file}: duplicate {key}.')
            values[key] = value.strip()
    fields = ('VERSION_MAJOR', 'VERSION_MINOR', 'PATCHLEVEL')
    if any(not re.fullmatch(r'\d+', values.get(key, '')) for key in fields):
        raise RuntimeError(f'Invalid Nordic SDK version file at {version_file}: '
                           'expected VERSION_MAJOR, VERSION_MINOR and PATCHLEVEL integers.')
    tweak = values.get('VERSION_TWEAK', '0')
    if not re.fullmatch(r'\d+', tweak):
        raise RuntimeError(f'Invalid Nordic SDK version file at {version_file}: VERSION_TWEAK must be an integer.')
    version = '.'.join(str(int(values[key])) for key in fields)
    if int(tweak):
        version += '.' + str(int(tweak))
    if values.get('EXTRAVERSION'):
        version += '-' + values['EXTRAVERSION']
    return version


def _validate_sdk(sdk: Path) -> Path:
    for marker in ('.west/config', 'nrf/VERSION', 'nrf/west.yml', 'zephyr/CMakeLists.txt',
                   'modules/lib/matter/src/app/chip_data_model.cmake'):
        if not (sdk / marker).is_file():
            raise RuntimeError(f'Incomplete Nordic SDK at {sdk}: missing {marker}. '
                               'Select an installed NCS west workspace with --sdk or NCS_SDK_ROOT.')
    version = _sdk_version(sdk / 'nrf/VERSION')
    if version != SDK_VERSION:
        raise RuntimeError(f'NCS {SDK_VERSION} is required; {sdk} contains {version}. '
                           'Select it with --sdk or NCS_SDK_ROOT.')
    return sdk


def _sdk_directory(args: argparse.Namespace) -> Path:
    selected = _override(args, 'sdk', 'NCS_SDK_ROOT')
    if selected:
        return _validate_sdk(selected)
    # An activated Nordic terminal or a project inside a west workspace.
    if os.environ.get('ZEPHYR_BASE'):
        return _validate_sdk(Path(os.environ['ZEPHYR_BASE']).expanduser().resolve().parent)
    for start in (Path.cwd(), ROOT):
        for candidate in (start, *start.parents):
            if (candidate / '.west/config').is_file():
                return _validate_sdk(candidate)
    candidates = [root / f'v{SDK_VERSION}' for root in _install_roots()]
    found = {path.resolve() for path in candidates if (path / '.west/config').is_file()}
    if len(found) == 1:
        return _validate_sdk(found.pop())
    raise RuntimeError(f'Select the installed NCS {SDK_VERSION} workspace with --sdk or NCS_SDK_ROOT. '
                       'No tools installed.')


def _bundle_versions(toolchain: Path) -> set[str]:
    """Use Nordic's version mapping; bundle IDs differ between platforms/releases."""
    index = toolchain.parent / 'toolchains.json'
    versions: set[str] = set()
    if index.is_file():
        def visit(value):
            if isinstance(value, dict):
                if value.get('identifier', {}).get('bundle_id') == toolchain.name:
                    versions.update(v.removeprefix('v') for v in value.get('ncs_versions', []))
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(json.loads(index.read_text()))
    manifest = toolchain / 'manifest.json'
    if not versions and manifest.is_file():
        version = json.loads(manifest.read_text()).get('ncs_version', {})
        if version.get('type') == 'tag' and version.get('tag_name'):
            versions.add(version['tag_name'].removeprefix('v'))
    return versions


def _sdk_bundle_id(sdk: Path) -> str | None:
    """Reproduce Nordic's scripts/print_toolchain_checksum.sh for this host."""
    platform = {'linux': 'linux', 'darwin': 'darwin', 'win32': 'win10'}.get(sys.platform)
    if platform is None:
        return None
    scripts = sdk / 'nrf/scripts'
    sources = (scripts / 'requirements-fixed.txt', scripts / f'tools-versions-{platform}.yml')
    if not all(path.is_file() for path in sources):
        return None
    # Nordic concatenates these files, removes CR at line ends, and takes the
    # first ten SHA-256 digits. Preserve blank lines and final-newline state.
    contents = b''.join(path.read_bytes() for path in sources)
    contents = re.sub(rb'\r(?=\n|$)', b'', contents)
    return hashlib.sha256(contents).hexdigest()[:10]


def _toolchain_bundle_id(toolchain: Path) -> str | None:
    manifest = toolchain / 'manifest.json'
    identifier = (json.loads(manifest.read_text()).get('bundle_id', toolchain.name)
                  if manifest.is_file() else toolchain.name)
    return identifier if isinstance(identifier, str) and re.fullmatch(r'[0-9a-f]{10}', identifier) else None


def _matches_sdk_bundle(toolchain: Path, sdk: Path) -> bool:
    expected = _sdk_bundle_id(sdk)
    return expected is not None and _toolchain_bundle_id(toolchain) == expected


def _validate_toolchain(toolchain: Path, sdk: Path) -> Path:
    if not (toolchain / 'environment.json').is_file():
        raise RuntimeError(f'Nordic toolchain not found at {toolchain} (missing environment.json). '
                           'Select it with --toolchain or NCS_TOOLCHAIN_ROOT. No tools installed.')
    expected = _sdk_bundle_id(sdk)
    actual = _toolchain_bundle_id(toolchain)
    if expected is not None and actual is not None:
        if actual != expected:
            raise RuntimeError(f'Toolchain at {toolchain} has bundle ID {actual}, but NCS {SDK_VERSION} '
                               f'requires {expected} on {sys.platform}.')
        # Official bundles can retain the PR branch used to build them in
        # installer metadata. The SDK-derived ID establishes compatibility.
        return toolchain
    versions = _bundle_versions(toolchain)
    if versions and SDK_VERSION not in versions:
        raise RuntimeError(f'Toolchain at {toolchain} targets NCS {", ".join(sorted(versions))}, '
                           f'but NCS {SDK_VERSION} is required.')
    return toolchain


def _toolchain_directory(args: argparse.Namespace, sdk: Path) -> Path:
    selected = _override(args, 'toolchain', 'NCS_TOOLCHAIN_ROOT')
    if selected:
        return _validate_toolchain(selected, sdk)
    # Find the bundle containing the active Python or Zephyr SDK. This also
    # supports custom installation roots without depending on a shell utility.
    active = [Path(sys.executable)]
    if os.environ.get('ZEPHYR_SDK_INSTALL_DIR'):
        active.insert(0, Path(os.environ['ZEPHYR_SDK_INSTALL_DIR']))
    for name in ('python', 'python3', 'cmake'):
        executable = shutil.which(name)
        if executable:
            active.append(Path(executable))
    for path in active:
        path = path.expanduser().resolve()
        for parent in (path, *path.parents):
            if (parent / 'environment.json').is_file():
                return _validate_toolchain(parent, sdk)
    roots = [sdk.parent, *_install_roots()]
    found = {path.resolve() for root in roots for path in (root / 'toolchains').glob('*')
             if (path / 'environment.json').is_file()}
    matching = {path for path in found if _matches_sdk_bundle(path, sdk)}
    if not matching:
        matching = {path for path in found if SDK_VERSION in _bundle_versions(path)}
    candidates = matching or {path for path in found if not _bundle_versions(path)}
    if len(candidates) == 1:
        return _validate_toolchain(candidates.pop(), sdk)
    detail = ' Multiple bundles found: ' + ', '.join(str(p) for p in sorted(candidates)) if candidates else ''
    raise RuntimeError('Select the matching installed Nordic toolchain with --toolchain or '
                       'NCS_TOOLCHAIN_ROOT, or use a Nordic toolchain terminal.' + detail + ' No tools installed.')


def _toolchain_environment(toolchain: Path, *, compiler: bool = False) -> dict[str, str]:
    env = os.environ.copy()
    for key in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'CONDA_PREFIX'):
        env.pop(key, None)
    if compiler:
        for key in ('CC', 'CXX', 'AS', 'AR', 'LD', 'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS',
                    'GCC_EXEC_PREFIX', 'COMPILER_PATH', 'LIBRARY_PATH', 'CPATH',
                    'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH',
                    'CMAKE_PREFIX_PATH', 'CMAKE_GENERATOR', 'CMAKE_TOOLCHAIN_FILE',
                    'CMAKE_C_COMPILER_LAUNCHER', 'CMAKE_CXX_COMPILER_LAUNCHER', 'ZEPHYR_BASE',
                    'ZEPHYR_TOOLCHAIN_VARIANT', 'ZEPHYR_SDK_INSTALL_DIR', 'GNUARMEMB_TOOLCHAIN_PATH',
                    'CROSS_COMPILE', 'ZEPHYR_EXTRA_MODULES', 'ZEPHYR_MODULES',
                    'BOARD_ROOT', 'DTS_ROOT', 'CONF_FILE', 'EXTRA_CONF_FILE',
                    'DTC_OVERLAY_FILE', 'EXTRA_DTC_OVERLAY_FILE'):
            env.pop(key, None)
        # Retain OS utilities, but do not inherit Homebrew, other toolchains or
        # virtual environments. Every build tool is verified below.
        if os.name == 'nt':
            system_root = Path(env.get('SystemRoot', 'C:/Windows'))
            env['PATH'] = os.pathsep.join(str(path) for path in (system_root / 'System32', system_root))
        else:
            env['PATH'] = os.defpath
    for variable in json.loads((toolchain / 'environment.json').read_text())['env_vars']:
        key = variable['key']
        if variable['type'] == 'relative_paths':
            value = os.pathsep.join(str(toolchain / item) for item in variable['values'])
            treatment = variable.get('existing_value_treatment', 'overwrite')
            if env.get(key) and treatment == 'prepend_to':
                value += os.pathsep + env[key]
            elif env.get(key) and treatment == 'append_to':
                value = env[key] + os.pathsep + value
        elif variable['type'] == 'string':
            value = variable['value']
        else:
            raise RuntimeError(f'Unsupported toolchain environment type: {variable["type"]}')
        env[key] = value
    env.update(CCACHE_DISABLE='1', XDG_CACHE_HOME=str(ROOT / 'target/build/.cache'),
               PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    return env


def installed_tools(args: argparse.Namespace, *, compiler: bool = False) -> tuple[Path, Path, dict[str, str]]:
    sdk = _sdk_directory(args)
    toolchain = _toolchain_directory(args, sdk)
    env = _toolchain_environment(toolchain, compiler=compiler)
    env['ZEPHYR_BASE'] = str(sdk / 'zephyr')
    env['NCS_SDK_ROOT'] = str(sdk)
    env['NCS_TOOLCHAIN_ROOT'] = str(toolchain)
    python = next((Path(path).resolve() for name in ('python3', 'python')
                   if (path := shutil.which(name, path=env.get('PATH', '')))
                   and Path(path).resolve().is_relative_to(toolchain)), None)
    if python is None:
        raise RuntimeError(f'Bundled Python not found in {toolchain}. No tools installed.')
    try:
        imports = 'import west, jinja2, lark, matter.idl' if compiler else 'import west'
        subprocess.run([str(python), '-c', imports], env=env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if compiler:
            for command in ('cmake', 'ninja', 'gn', 'git', 'dtc',
                            'arm-zephyr-eabi-gcc', 'arm-zephyr-eabi-g++'):
                executable = shutil.which(command, path=env['PATH'])
                if not executable or not Path(executable).resolve().is_relative_to(toolchain):
                    raise RuntimeError(f'Bundled {command} not found. No tools installed.')
                subprocess.run([executable, '--version'], env=env, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f'Installed Nordic toolchain could not run: {exc}. No tools installed.') from exc
    return sdk, python, env


def run(command: list[str], *, cwd: Path, env: dict[str, str], dry_run: bool) -> None:
    print(f'Working directory: {cwd}', flush=True)
    print(shlex.join(command), flush=True)
    if not dry_run:
        subprocess.run(command, cwd=cwd, env=env, check=True)
