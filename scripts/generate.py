#!/usr/bin/env python3
"""Generate Matter build artifacts with an installed ZAP CLI; never writes source files."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from nordic import ROOT, installed_tools, run


def installed_zap(override: Path | None = None) -> Path:
    selected = override or os.environ.get('ZAP_CLI')
    if not selected:
        selected = shutil.which('zap-cli')
    if selected:
        executable = Path(selected).expanduser().resolve()
        if executable.is_file() and os.access(executable, os.X_OK):
            return executable
    raise RuntimeError('Installed ZAP CLI not found. Select it with --zap PATH or ZAP_CLI, '
                       'or put zap-cli on PATH. No tools installed.')


def generate(args: argparse.Namespace, output: Path, sdk: Path, env: dict[str, str]) -> None:
    matter = sdk / 'modules/lib/matter'
    env['ZEPHYR_CONNECTEDHOMEIP_MODULE_DIR'] = str(matter)
    templates = matter / 'src/app/zap-templates'
    source = ROOT / 'src/data_model/mailbox-motion.zap'
    require_paths = [source, templates / 'zcl/zcl.json', templates / 'app-templates.json',
                     templates / 'matter-idl-server.json']
    for path in require_paths:
        if not path.is_file():
            raise RuntimeError(f'Required installed SDK/model input not found: {path}. No tools installed.')
    if not args.dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='zap-', dir=output.parent) as temporary:
            _generate(args, Path(temporary), output, templates, source, env)
    else:
        _generate(args, output, output, templates, source, env)


def _generate(args, staging: Path, output: Path, templates: Path, source: Path,
              env: dict[str, str]) -> None:
    base = [str(args.zap), 'generate', '--noUi', '--noServer', '--tempState', '--skipPostGeneration',
            '-i', str(source), '-z', str(templates / 'zcl/zcl.json')]
    for name, directory in [('app-templates', staging / 'app'), ('matter-idl-server', staging / 'idl')]:
        if not args.dry_run:
            directory.mkdir(parents=True)
        command = base + ['-g', str(templates / f'{name}.json'), '-o', str(directory)]
        run(command, cwd=ROOT, env=env, dry_run=args.dry_run)
    if args.dry_run:
        print(f'Matter files would be generated in {output}.')
        return
    files = [template['output'] for template in json.loads((templates / 'app-templates.json').read_text())['templates']]
    replacements = [(staging / 'app' / name, output / 'zap-generated' / name) for name in files]
    replacements.append((staging / 'idl/Clusters.matter', output / 'mailbox-motion.matter'))
    for source_path, _ in replacements:
        if not source_path.is_file() or not source_path.stat().st_size:
            raise RuntimeError(f'ZAP did not produce required output: {source_path}. Build artifacts were not updated.')
    for source_path, destination in replacements:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.is_file() or destination.read_bytes() != source_path.read_bytes():
            shutil.copyfile(source_path, destination)
    print(f'Generated {len(replacements)} Matter artifacts in {output}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zap', type=Path, help='installed ZAP CLI (or ZAP_CLI / zap-cli on PATH)')
    parser.add_argument('--sdk', type=Path, help='existing NCS west workspace')
    parser.add_argument('--toolchain', type=Path, help='existing Nordic toolchain bundle directory')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'target/generate/matter',
                        help='generated build artifacts directory (default: target/generate/matter)')
    args = parser.parse_args()
    args.zap = installed_zap(args.zap)
    output = args.output_dir.expanduser().resolve()
    if output == ROOT / 'src' or (ROOT / 'src') in output.parents:
        raise RuntimeError('Generated Matter files must be outside the source tree. Choose a build output directory.')
    sdk, _, env = installed_tools(args)
    generate(args, output, sdk, env)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
