"""Check ZAP discovery and generation without installing or running ZAP."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
NORDIC_SPEC = importlib.util.spec_from_file_location('generator_nordic', SCRIPTS / 'nordic.py')
assert NORDIC_SPEC is not None and NORDIC_SPEC.loader is not None
nordic = importlib.util.module_from_spec(NORDIC_SPEC)
NORDIC_SPEC.loader.exec_module(nordic)
SPEC = importlib.util.spec_from_file_location('matter_generator', SCRIPTS / 'generate.py')
assert SPEC is not None and SPEC.loader is not None
generate_script = importlib.util.module_from_spec(SPEC)
with mock.patch.dict(sys.modules, {'nordic': nordic}):
    SPEC.loader.exec_module(generate_script)


class ZapDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix='zap discovery tests ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = mock.patch.dict(os.environ, {'PATH': '/external/bin'}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def executable(self, name: str) -> Path:
        path = self.root / name
        path.touch()
        path.chmod(0o755)
        return path

    def test_explicit_executable_with_spaces_is_accepted(self) -> None:
        zap = self.executable('installed ZAP CLI')
        self.assertEqual(generate_script.installed_zap(zap), zap)

    def test_explicit_executable_takes_precedence_over_environment_and_path(self) -> None:
        zap = self.executable('explicit-zap')
        os.environ['ZAP_CLI'] = str(self.executable('environment-zap'))
        with mock.patch.object(generate_script.shutil, 'which', return_value=str(self.executable('path-zap'))):
            self.assertEqual(generate_script.installed_zap(zap), zap)

    def test_environment_override_is_used(self) -> None:
        zap = self.executable('environment ZAP')
        os.environ['ZAP_CLI'] = str(zap)
        self.assertEqual(generate_script.installed_zap(), zap)

    def test_missing_explicit_override_does_not_fall_back(self) -> None:
        zap = self.executable('environment-zap')
        os.environ['ZAP_CLI'] = str(zap)
        with mock.patch.object(generate_script.shutil, 'which', return_value=str(zap)):
            with self.assertRaisesRegex(RuntimeError, 'ZAP'):
                generate_script.installed_zap(self.root / 'missing-zap')

    def test_invalid_environment_override_does_not_fall_back(self) -> None:
        os.environ['ZAP_CLI'] = str(self.root / 'missing-zap')
        with mock.patch.object(generate_script.shutil, 'which', return_value=str(self.executable('path-zap'))):
            with self.assertRaisesRegex(RuntimeError, 'ZAP'):
                generate_script.installed_zap()

    def test_executable_is_discovered_on_path(self) -> None:
        zap = self.executable('zap-cli')
        with mock.patch.object(generate_script.shutil, 'which', side_effect=lambda name: str(zap) if name == 'zap-cli' else None):
            self.assertEqual(generate_script.installed_zap(), zap)

    def test_no_zap_reports_installation_requirement(self) -> None:
        with mock.patch.object(generate_script.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'ZAP'):
                generate_script.installed_zap()


class ZapGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix='zap-generation-tests-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / 'project'
        self.model = self.project / 'src/data_model/mailbox-motion.zap'
        self.model.parent.mkdir(parents=True)
        self.model.write_text('{"fixture": "model"}\n')
        self.sdk = self.root / 'sdk'
        self.matter = self.sdk / 'modules/lib/matter'
        self.templates = self.matter / 'src/app/zap-templates'
        (self.templates / 'zcl').mkdir(parents=True)
        (self.templates / 'zcl/zcl.json').write_text('{}')
        self.app_files = {'access.h': 'generated access\n', 'gen_config.h': 'generated config\n'}
        (self.templates / 'app-templates.json').write_text(json.dumps({
            'templates': [{'output': name} for name in self.app_files],
        }))
        (self.templates / 'matter-idl-server.json').write_text('{}')
        self.idl = 'generated matter IDL\n'
        self.output = self.root / 'build/matter'
        self.args = argparse.Namespace(zap=self.root / 'installed-zap', dry_run=False)
        self.environment: dict[str, str] = {}
        project = mock.patch.object(generate_script, 'ROOT', self.project)
        project.start()
        self.addCleanup(project.stop)
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def fake_zap(self, command: list[str], *, cwd: Path, env: dict[str, str], dry_run: bool) -> None:
        if dry_run:
            return
        directory = Path(command[command.index('-o') + 1])
        directory.mkdir(parents=True, exist_ok=True)
        template = Path(command[command.index('-g') + 1]).name
        files = self.app_files if template == 'app-templates.json' else {'Clusters.matter': self.idl}
        for name, contents in files.items():
            destination = directory / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(contents)

    def generate(self) -> None:
        with mock.patch.object(generate_script, 'run', side_effect=self.fake_zap):
            generate_script.generate(self.args, self.output, self.sdk, self.environment)

    def outputs(self) -> dict[Path, str]:
        return {**{self.output / 'zap-generated' / name: content for name, content in self.app_files.items()},
                self.output / 'mailbox-motion.matter': self.idl}

    def seed_final_outputs(self, content: str = 'previous output\n') -> dict[Path, bytes]:
        snapshot = {}
        for path in self.outputs():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            snapshot[path] = path.read_bytes()
        return snapshot

    def assert_outputs_unchanged(self, snapshot: dict[Path, bytes]) -> None:
        self.assertEqual({path: path.read_bytes() for path in snapshot}, snapshot)

    def test_generation_writes_only_to_selected_build_output(self) -> None:
        self.generate()
        for path, contents in self.outputs().items():
            self.assertEqual(path.read_text(), contents)
        self.assertEqual([path for path in self.project.rglob('*') if path.is_file()], [self.model])
        self.assertEqual(self.environment['ZEPHYR_CONNECTEDHOMEIP_MODULE_DIR'], str(self.matter))

    def test_dry_run_does_not_create_outputs_or_touch_source(self) -> None:
        self.args.dry_run = True
        self.generate()
        self.assertFalse(self.output.exists())
        self.assertEqual([path for path in self.project.rglob('*') if path.is_file()], [self.model])

    def test_missing_sdk_input_fails_before_running_zap(self) -> None:
        (self.templates / 'zcl/zcl.json').unlink()
        with mock.patch.object(generate_script, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'input'):
                generate_script.generate(self.args, self.output, self.sdk, self.environment)
        run.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_incomplete_generation_preserves_all_previous_outputs(self) -> None:
        previous = self.seed_final_outputs()
        self.app_files.pop('gen_config.h')
        with self.assertRaisesRegex(RuntimeError, 'output'):
            self.generate()
        self.assert_outputs_unchanged(previous)

    def test_empty_idl_preserves_all_previous_outputs(self) -> None:
        previous = self.seed_final_outputs()
        self.idl = ''
        with self.assertRaisesRegex(RuntimeError, 'output'):
            self.generate()
        self.assert_outputs_unchanged(previous)

    def test_zap_failure_preserves_previous_outputs_and_source(self) -> None:
        previous = self.seed_final_outputs()
        with mock.patch.object(generate_script, 'run', side_effect=subprocess.CalledProcessError(1, ['zap-cli'])):
            with self.assertRaises(subprocess.CalledProcessError):
                generate_script.generate(self.args, self.output, self.sdk, self.environment)
        self.assert_outputs_unchanged(previous)
        self.assertEqual([path for path in self.project.rglob('*') if path.is_file()], [self.model])

    def test_identical_generation_keeps_existing_file_timestamps(self) -> None:
        self.generate()
        timestamp = 1_500_000_000_000_000_000
        for path in self.outputs():
            os.utime(path, ns=(timestamp, timestamp))
        self.generate()
        self.assertTrue(all(path.stat().st_mtime_ns == timestamp for path in self.outputs()))

    def test_changed_generation_updates_only_changed_files(self) -> None:
        self.generate()
        timestamp = 1_500_000_000_000_000_000
        for path in self.outputs():
            os.utime(path, ns=(timestamp, timestamp))
        self.app_files['access.h'] = 'updated access\n'
        self.generate()
        self.assertEqual((self.output / 'zap-generated/access.h').read_text(), 'updated access\n')
        self.assertEqual((self.output / 'zap-generated/gen_config.h').stat().st_mtime_ns, timestamp)
        self.assertEqual((self.output / 'mailbox-motion.matter').stat().st_mtime_ns, timestamp)

    def test_cli_rejects_source_tree_output_before_running_tools(self) -> None:
        with mock.patch.object(generate_script, 'installed_zap', return_value=self.args.zap), \
                mock.patch.object(generate_script, 'installed_tools') as tools, \
                mock.patch.object(generate_script, 'generate') as generate, \
                mock.patch.object(sys, 'argv', ['generate.py', '--output-dir', str(self.model.parent)]):
            with self.assertRaisesRegex(RuntimeError, 'source tree'):
                generate_script.main()
        tools.assert_not_called()
        generate.assert_not_called()
        self.assertEqual([path for path in self.project.rglob('*') if path.is_file()], [self.model])

    def test_cli_dry_run_uses_requested_output_without_creating_it(self) -> None:
        with mock.patch.object(generate_script, 'installed_zap', return_value=self.args.zap), \
                mock.patch.object(generate_script, 'installed_tools',
                                  return_value=(self.sdk, Path('/bundled/python3'), self.environment)), \
                mock.patch.object(generate_script, 'run', side_effect=self.fake_zap), \
                mock.patch.object(sys, 'argv', ['generate.py', '--dry-run', '--output-dir', str(self.output)]):
            generate_script.main()
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
