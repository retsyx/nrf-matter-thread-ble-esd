"""Regression checks for using relocatable, already installed Nordic tools.

Run with the bundled Python: python3 -m unittest discover -s tests -p 'test_*.py'.
The fixtures are directories and JSON files; no SDK download or host compiler is
needed, and executable checks are mocked rather than run on the test machine.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/nordic.py'
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location('nordic_build_tools', SCRIPT)
assert SPEC is not None and SPEC.loader is not None
nordic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nordic)
GENERATE_SPEC = importlib.util.spec_from_file_location('nordic_generate_script', SCRIPT.with_name('generate.py'))
assert GENERATE_SPEC is not None and GENERATE_SPEC.loader is not None
generate_script = importlib.util.module_from_spec(GENERATE_SPEC)
with mock.patch.dict(sys.modules, {'nordic': nordic}):
    GENERATE_SPEC.loader.exec_module(generate_script)
BUILD_SPEC = importlib.util.spec_from_file_location('nordic_build_script', SCRIPT.with_name('build.py'))
assert BUILD_SPEC is not None and BUILD_SPEC.loader is not None
build_script = importlib.util.module_from_spec(BUILD_SPEC)
with mock.patch.dict(sys.modules, {'nordic': nordic, 'generate': generate_script}):
    BUILD_SPEC.loader.exec_module(build_script)


class InstalledToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix='nordic build tests ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.environment = mock.patch.dict(os.environ, {'PATH': '/external/bin'}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.process = mock.patch.object(nordic.subprocess, 'run')
        self.run = self.process.start()
        self.run.return_value = subprocess.CompletedProcess([], 0, stdout='', stderr='')
        self.addCleanup(self.process.stop)
        roots = mock.patch.object(nordic, '_install_roots', return_value=[])
        roots.start()
        self.addCleanup(roots.stop)
        python = mock.patch.object(nordic.sys, 'executable', str(self.root / 'host/python3'))
        python.start()
        self.addCleanup(python.stop)
        project = mock.patch.object(nordic, 'ROOT', self.root / 'project')
        project.start()
        self.addCleanup(project.stop)
        cwd = mock.patch.object(nordic.Path, 'cwd', return_value=self.root / 'project')
        cwd.start()
        self.addCleanup(cwd.stop)

    def sdk(self, name: str = 'relocated SDK', version: str = '3.4.1') -> Path:
        directory = self.root / name
        for marker in ('.west/config', 'zephyr/CMakeLists.txt', 'nrf/west.yml',
                       'modules/lib/matter/src/app/chip_data_model.cmake'):
            path = directory / marker
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        major, minor, patch = version.split('.')
        (directory / 'nrf/VERSION').write_text(
            f'VERSION_MAJOR = {major}\nVERSION_MINOR = {minor}\nPATCHLEVEL = {patch}\n'
            'VERSION_TWEAK = 0\nEXTRAVERSION =\nVERSION_METADATA = lts\n')
        return directory

    def toolchain(self, name: str = 'relocated toolchain') -> Path:
        directory = self.root / name
        (directory / 'bin').mkdir(parents=True)
        for command in ('python3', 'cmake', 'ninja', 'gn', 'git', 'dtc',
                        'arm-zephyr-eabi-gcc', 'arm-zephyr-eabi-g++'):
            path = directory / 'bin' / command
            path.touch()
            path.chmod(0o755)
        (directory / 'environment.json').write_text(json.dumps({'env_vars': [
            {'key': 'PATH', 'type': 'relative_paths', 'values': ['bin', 'opt/compiler/bin'],
             'existing_value_treatment': 'prepend_to'},
            {'key': 'ZEPHYR_TOOLCHAIN_VARIANT', 'type': 'string', 'value': 'zephyr'},
            {'key': 'ZEPHYR_SDK_INSTALL_DIR', 'type': 'relative_paths',
             'values': ['opt/zephyr-sdk'], 'existing_value_treatment': 'overwrite'},
        ]}))
        return directory

    @staticmethod
    def sdk_toolchain_inputs(sdk: Path) -> None:
        scripts = sdk / 'nrf/scripts'
        scripts.mkdir(parents=True)
        (scripts / 'requirements-fixed.txt').write_bytes(b'west==1.4.0\nJinja2==3.1.6\n')
        for platform, version in (('linux', '3.12.4'), ('darwin', '3.12.5'), ('win10', '3.12.6')):
            (scripts / f'tools-versions-{platform}.yml').write_bytes(
                f'python:\n  version: {version}\n'.encode())

    @staticmethod
    def pr_bundle_metadata(toolchain: Path, bundle_id: str) -> None:
        (toolchain / 'manifest.json').write_text(json.dumps({
            'bundle_id': bundle_id,
            'ncs_version': {'type': 'branch', 'branch_name': 'PR-30809'},
        }))
        (toolchain.parent / 'toolchains.json').write_text(json.dumps([{
            'toolchains': [{'identifier': {'bundle_id': toolchain.name},
                            'ncs_versions': ['PR-30809']}],
        }]))

    @staticmethod
    def options(sdk: Path | None = None, toolchain: Path | None = None) -> argparse.Namespace:
        return argparse.Namespace(sdk=sdk, toolchain=toolchain)

    def test_explicit_relocated_directories_with_spaces(self) -> None:
        sdk = self.sdk()
        toolchain = self.toolchain()
        selected_sdk, python, environment = nordic.installed_tools(self.options(sdk, toolchain))
        self.assertEqual(selected_sdk, sdk)
        self.assertEqual(python, toolchain / 'bin/python3')
        self.assertEqual(environment['ZEPHYR_BASE'], str(sdk / 'zephyr'))
        self.assertEqual(environment['ZEPHYR_SDK_INSTALL_DIR'], str(toolchain / 'opt/zephyr-sdk'))
        self.assertEqual(environment['PATH'].split(os.pathsep)[:2],
                         [str(toolchain / 'bin'), str(toolchain / 'opt/compiler/bin')])
        self.assertEqual(self.run.call_args.args[0][0], str(python))
        self.assertIn('import west', self.run.call_args.args[0])

    def test_sdk_environment_override_can_use_an_arbitrary_directory(self) -> None:
        sdk = self.sdk()
        toolchain = self.toolchain()
        os.environ.update(NCS_SDK_ROOT=str(sdk), NCS_TOOLCHAIN_ROOT=str(toolchain))
        selected_sdk, python, _ = nordic.installed_tools(self.options())
        self.assertEqual(selected_sdk, sdk)
        self.assertEqual(python, toolchain / 'bin/python3')

    def test_explicit_arguments_take_precedence_over_environment(self) -> None:
        sdk = self.sdk()
        toolchain = self.toolchain()
        os.environ.update(NCS_SDK_ROOT=str(self.root / 'absent SDK'),
                          NCS_TOOLCHAIN_ROOT=str(self.root / 'absent toolchain'))
        selected_sdk, python, _ = nordic.installed_tools(self.options(sdk, toolchain))
        self.assertEqual(selected_sdk, sdk)
        self.assertEqual(python, toolchain / 'bin/python3')

    def test_missing_explicit_sdk_does_not_fall_back_to_environment(self) -> None:
        os.environ['NCS_SDK_ROOT'] = str(self.sdk())
        with self.assertRaises(RuntimeError):
            nordic.installed_tools(self.options(self.root / 'missing SDK', self.toolchain()))
        self.run.assert_not_called()

    def test_missing_explicit_toolchain_does_not_fall_back_to_environment(self) -> None:
        os.environ['NCS_TOOLCHAIN_ROOT'] = str(self.toolchain())
        with self.assertRaises(RuntimeError):
            nordic.installed_tools(self.options(self.sdk(), self.root / 'missing toolchain'))
        self.run.assert_not_called()

    def test_wrong_sdk_version_is_rejected_before_running_tools(self) -> None:
        with self.assertRaisesRegex(RuntimeError, '3[.]4[.]1'):
            nordic.installed_tools(self.options(self.sdk(version='3.2.0'), self.toolchain()))
        self.run.assert_not_called()

    def test_plain_version_file_is_still_understood(self) -> None:
        sdk = self.sdk()
        (sdk / 'nrf/VERSION').write_text('3.4.1\n')
        selected_sdk, _, _ = nordic.installed_tools(self.options(sdk, self.toolchain()))
        self.assertEqual(selected_sdk, sdk)

    def test_previous_sdk_plain_version_is_rejected(self) -> None:
        sdk = self.sdk()
        (sdk / 'nrf/VERSION').write_text('3.1.1\n')
        with self.assertRaisesRegex(RuntimeError, 'contains 3[.]1[.]1'):
            nordic.installed_tools(self.options(sdk, self.toolchain()))
        self.run.assert_not_called()

    def test_malformed_sdk_version_is_rejected_before_running_tools(self) -> None:
        sdk = self.sdk()
        toolchain = self.toolchain()
        version_file = sdk / 'nrf/VERSION'
        original = version_file.read_text()
        malformed = (
            '',
            original.replace('PATCHLEVEL = 1\n', ''),
            original.replace('VERSION_MINOR = 4', 'VERSION_MINOR = four'),
            original.replace('VERSION_TWEAK = 0', 'VERSION_TWEAK = invalid'),
            original + 'PATCHLEVEL = 1\n',
        )
        for contents in malformed:
            with self.subTest(contents=contents):
                version_file.write_text(contents)
                with self.assertRaisesRegex(RuntimeError, 'Invalid Nordic SDK version file'):
                    nordic.installed_tools(self.options(sdk, toolchain))
        self.run.assert_not_called()

    def test_prerelease_or_tweaked_sdk_does_not_match_release_pin(self) -> None:
        sdk = self.sdk()
        toolchain = self.toolchain()
        version_file = sdk / 'nrf/VERSION'
        original = version_file.read_text()
        for contents in (original.replace('EXTRAVERSION =\n', 'EXTRAVERSION = rc1\n'),
                         original.replace('VERSION_TWEAK = 0', 'VERSION_TWEAK = 1')):
            with self.subTest(contents=contents):
                version_file.write_text(contents)
                with self.assertRaisesRegex(RuntimeError, 'NCS 3[.]4[.]1 is required'):
                    nordic.installed_tools(self.options(sdk, toolchain))
        self.run.assert_not_called()

    def test_partial_sdk_checkout_is_rejected(self) -> None:
        sdk = self.sdk()
        (sdk / 'modules/lib/matter/src/app/chip_data_model.cmake').unlink()
        with self.assertRaises(RuntimeError):
            nordic.installed_tools(self.options(sdk, self.toolchain()))
        self.run.assert_not_called()

    def test_python_environment_from_the_host_is_removed(self) -> None:
        os.environ.update(PYTHONHOME='/host/python', PYTHONPATH='/host/modules')
        _, _, environment = nordic.installed_tools(self.options(self.sdk(), self.toolchain()))
        self.assertNotIn('PYTHONHOME', environment)
        self.assertNotIn('PYTHONPATH', environment)
        self.assertEqual(environment['PYTHONNOUSERSITE'], '1')
        self.assertNotIn('PYTHONHOME', self.run.call_args.kwargs['env'])
        self.assertNotIn('PYTHONPATH', self.run.call_args.kwargs['env'])

    def test_compiler_build_ignores_external_compiler_and_zephyr_overrides(self) -> None:
        sdk = self.sdk()
        toolchain = self.toolchain()
        os.environ.update(CC='/host/gcc', CXX='/host/g++',
                          ZEPHYR_BASE='/host/zephyr', ZEPHYR_TOOLCHAIN_VARIANT='host',
                          ZEPHYR_SDK_INSTALL_DIR='/host/zephyr-sdk')
        _, _, environment = nordic.installed_tools(self.options(sdk, toolchain), compiler=True)
        self.assertNotIn('CC', environment)
        self.assertNotIn('CXX', environment)
        self.assertEqual(environment['ZEPHYR_BASE'], str(sdk / 'zephyr'))
        self.assertEqual(environment['ZEPHYR_TOOLCHAIN_VARIANT'], 'zephyr')
        self.assertEqual(environment['ZEPHYR_SDK_INSTALL_DIR'], str(toolchain / 'opt/zephyr-sdk'))
        self.assertTrue(all('CC' not in call.kwargs['env'] for call in self.run.call_args_list))

    def test_external_compiler_on_path_is_not_accepted(self) -> None:
        sdk = self.sdk()
        toolchain = self.toolchain()
        def executable(name: str, **_) -> str:
            return str(toolchain / 'bin/python3') if name == 'python3' else '/external/bin/' + name
        with mock.patch.object(nordic.shutil, 'which', side_effect=executable):
            with self.assertRaisesRegex(RuntimeError, 'Bundled cmake'):
                nordic.installed_tools(self.options(sdk, toolchain), compiler=True)

    def test_bundled_python_failure_reports_a_useful_error(self) -> None:
        self.run.side_effect = subprocess.CalledProcessError(1, ['python3', '-c', 'import west'])
        with self.assertRaisesRegex(RuntimeError, 'toolchain'):
            nordic.installed_tools(self.options(self.sdk(), self.toolchain()))

    def test_active_nordic_terminal_discovers_relocated_sdk_and_bundle(self) -> None:
        sdk = self.sdk()
        toolchain = self.toolchain()
        os.environ.update(ZEPHYR_BASE=str(sdk / 'zephyr'), PATH=str(toolchain / 'bin'))
        selected_sdk, python, _ = nordic.installed_tools(self.options())
        self.assertEqual(selected_sdk, sdk)
        self.assertEqual(python, toolchain / 'bin/python3')

    def test_active_zephyr_sdk_identifies_bundle_when_python_is_external(self) -> None:
        sdk = self.sdk()
        toolchain = self.toolchain()
        os.environ['ZEPHYR_SDK_INSTALL_DIR'] = str(toolchain / 'opt/zephyr-sdk')
        with mock.patch.object(nordic.shutil, 'which', return_value=None):
            self.assertEqual(nordic._toolchain_directory(self.options(), sdk), toolchain)

    def test_version_mapping_discovers_platform_specific_bundle_id(self) -> None:
        sdk = self.sdk()
        toolchain = self.toolchain('toolchains/a different official bundle')
        incompatible = self.toolchain('toolchains/another platform bundle')
        (toolchain.parent / 'toolchains.json').write_text(json.dumps([{
            'toolchains': [
                {'identifier': {'bundle_id': toolchain.name}, 'ncs_versions': ['v3.4.1']},
                {'identifier': {'bundle_id': incompatible.name}, 'ncs_versions': ['v3.0.0']},
            ],
        }]))
        selected_sdk, python, _ = nordic.installed_tools(self.options(sdk))
        self.assertEqual(selected_sdk, sdk)
        self.assertEqual(python, toolchain / 'bin/python3')

    def test_multiple_unmapped_toolchains_require_an_explicit_override(self) -> None:
        sdk = self.sdk()
        first = self.toolchain('toolchains/first official bundle')
        self.toolchain('toolchains/second official bundle')
        with self.assertRaisesRegex(RuntimeError, '--toolchain'):
            nordic.installed_tools(self.options(sdk))
        self.run.assert_not_called()
        _, python, _ = nordic.installed_tools(self.options(sdk, first))
        self.assertEqual(python, first / 'bin/python3')

    def test_explicit_incompatible_bundle_is_rejected(self) -> None:
        sdk = self.sdk()
        toolchain = self.toolchain()
        (toolchain / 'manifest.json').write_text(json.dumps({
            'ncs_version': {'type': 'tag', 'tag_name': 'v3.0.0'},
        }))
        with self.assertRaisesRegex(RuntimeError, '3[.]4[.]1'):
            nordic.installed_tools(self.options(sdk, toolchain))
        self.run.assert_not_called()

    def test_compiler_accepts_sdk_checksum_despite_pr_version_metadata(self) -> None:
        sdk = self.sdk()
        self.sdk_toolchain_inputs(sdk)
        toolchain = self.toolchain('toolchains/b2d396f613')
        self.pr_bundle_metadata(toolchain, 'b2d396f613')
        mapping = (toolchain.parent / 'toolchains.json').read_bytes()
        with mock.patch.object(nordic.sys, 'platform', 'linux'):
            selected_sdk, python, _ = nordic.installed_tools(self.options(sdk, toolchain), compiler=True)
        self.assertEqual(selected_sdk, sdk)
        self.assertEqual(python, toolchain / 'bin/python3')
        self.assertEqual((toolchain.parent / 'toolchains.json').read_bytes(), mapping)
        self.assertIn('import west, jinja2, lark, matter.idl', self.run.call_args_list[0].args[0])
        self.assertTrue(any(call.args[0][0].endswith('/arm-zephyr-eabi-gcc')
                            for call in self.run.call_args_list))

    def test_sdk_checksum_discovers_renamed_bundle_with_pr_metadata(self) -> None:
        sdk = self.sdk()
        self.sdk_toolchain_inputs(sdk)
        toolchain = self.toolchain('toolchains/renamed official bundle')
        incompatible = self.toolchain('toolchains/unrelated bundle')
        self.pr_bundle_metadata(toolchain, 'b2d396f613')
        self.pr_bundle_metadata(incompatible, '0000000000')
        (toolchain.parent / 'toolchains.json').write_text(json.dumps([{
            'toolchains': [
                {'identifier': {'bundle_id': path.name}, 'ncs_versions': ['PR-30809']}
                for path in (toolchain, incompatible)
            ],
        }]))
        with mock.patch.object(nordic.sys, 'platform', 'linux'):
            selected_sdk, python, _ = nordic.installed_tools(self.options(sdk))
        self.assertEqual(selected_sdk, sdk)
        self.assertEqual(python, toolchain / 'bin/python3')

    def test_sdk_checksum_uses_platform_specific_inputs(self) -> None:
        sdk = self.sdk()
        self.sdk_toolchain_inputs(sdk)
        toolchain = self.toolchain()
        # Fixed hashes of the fixture requirements followed by each platform's
        # tools file, matching Nordic's print_toolchain_checksum.sh contract.
        for platform, bundle_id in (('linux', 'b2d396f613'), ('darwin', '267de0933d'),
                                    ('win32', 'f7d4ff42f9')):
            with self.subTest(platform=platform), mock.patch.object(nordic.sys, 'platform', platform):
                self.pr_bundle_metadata(toolchain, bundle_id)
                self.assertTrue(nordic._matches_sdk_bundle(toolchain, sdk))

    def test_sdk_checksum_uses_directory_id_when_manifest_has_no_id(self) -> None:
        sdk = self.sdk()
        self.sdk_toolchain_inputs(sdk)
        toolchain = self.toolchain('toolchains/b2d396f613')
        (toolchain / 'manifest.json').write_text(json.dumps({
            'ncs_version': {'type': 'branch', 'branch_name': 'PR-30809'},
        }))
        with mock.patch.object(nordic.sys, 'platform', 'linux'):
            self.assertTrue(nordic._matches_sdk_bundle(toolchain, sdk))

    def test_matching_directory_name_does_not_override_wrong_manifest_id(self) -> None:
        sdk = self.sdk()
        self.sdk_toolchain_inputs(sdk)
        toolchain = self.toolchain('toolchains/b2d396f613')
        self.pr_bundle_metadata(toolchain, '0000000000')
        with mock.patch.object(nordic.sys, 'platform', 'linux'):
            self.assertFalse(nordic._matches_sdk_bundle(toolchain, sdk))
            with self.assertRaisesRegex(RuntimeError, 'bundle ID'):
                nordic.installed_tools(self.options(sdk, toolchain))
        self.run.assert_not_called()

    def test_release_version_metadata_does_not_override_a_wrong_sdk_checksum(self) -> None:
        sdk = self.sdk()
        self.sdk_toolchain_inputs(sdk)
        toolchain = self.toolchain()
        self.pr_bundle_metadata(toolchain, '0000000000')
        (toolchain.parent / 'toolchains.json').write_text(json.dumps([{
            'toolchains': [{'identifier': {'bundle_id': toolchain.name},
                            'ncs_versions': ['v3.4.1']}],
        }]))
        with mock.patch.object(nordic.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(RuntimeError, 'bundle ID'):
                nordic.installed_tools(self.options(sdk, toolchain), compiler=True)
        self.run.assert_not_called()

    def test_pr_metadata_is_not_accepted_for_wrong_platform_bundle(self) -> None:
        sdk = self.sdk()
        self.sdk_toolchain_inputs(sdk)
        toolchain = self.toolchain()
        self.pr_bundle_metadata(toolchain, '267de0933d')
        with mock.patch.object(nordic.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(RuntimeError, 'bundle ID'):
                nordic.installed_tools(self.options(sdk, toolchain), compiler=True)
        self.run.assert_not_called()

    def test_sdk_checksum_normalizes_crlf_without_changing_content(self) -> None:
        sdk = self.sdk()
        self.sdk_toolchain_inputs(sdk)
        scripts = sdk / 'nrf/scripts'
        for name in ('requirements-fixed.txt', 'tools-versions-linux.yml'):
            path = scripts / name
            path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
        toolchain = self.toolchain()
        self.pr_bundle_metadata(toolchain, 'b2d396f613')
        with mock.patch.object(nordic.sys, 'platform', 'linux'):
            self.assertTrue(nordic._matches_sdk_bundle(toolchain, sdk))
            (scripts / 'requirements-fixed.txt').write_bytes(b'west==1.5.0\r\nJinja2==3.1.6\r\n')
            self.assertFalse(nordic._matches_sdk_bundle(toolchain, sdk))

    def test_sdk_checksum_strips_eof_cr_without_adding_a_newline(self) -> None:
        sdk = self.sdk()
        self.sdk_toolchain_inputs(sdk)
        tools = sdk / 'nrf/scripts/tools-versions-linux.yml'
        tools.write_bytes(b'python:\r\n  version: 3.12.4\r')
        toolchain = self.toolchain()
        self.pr_bundle_metadata(toolchain, '5249b053d3')
        with mock.patch.object(nordic.sys, 'platform', 'linux'):
            self.assertTrue(nordic._matches_sdk_bundle(toolchain, sdk))

    def test_missing_sdk_checksum_inputs_do_not_claim_a_bundle_match(self) -> None:
        sdk = self.sdk()
        toolchain = self.toolchain('toolchains/b2d396f613')
        with mock.patch.object(nordic.sys, 'platform', 'linux'):
            self.assertFalse(nordic._matches_sdk_bundle(toolchain, sdk))
            self.sdk_toolchain_inputs(sdk)
            (sdk / 'nrf/scripts/tools-versions-linux.yml').unlink()
            self.assertFalse(nordic._matches_sdk_bundle(toolchain, sdk))

    def test_standard_install_location_is_resolved_without_a_machine_specific_root(self) -> None:
        sdk = self.sdk('v3.4.1')
        toolchain = self.toolchain('toolchains/any official bundle')
        with mock.patch.object(nordic, '_install_roots', return_value=[self.root]):
            selected_sdk, python, _ = nordic.installed_tools(self.options())
        self.assertEqual(selected_sdk, sdk)
        self.assertEqual(python, toolchain / 'bin/python3')

    def test_compiler_build_drops_external_search_paths(self) -> None:
        sdk = self.sdk()
        toolchain = self.toolchain()
        os.environ.update(CMAKE_PREFIX_PATH='/host/cmake', CMAKE_GENERATOR='Unix Makefiles',
                          CMAKE_TOOLCHAIN_FILE='/host/other-toolchain.cmake',
                          CMAKE_C_COMPILER_LAUNCHER='/host/ccache',
                          CMAKE_CXX_COMPILER_LAUNCHER='/host/sccache',
                          GCC_EXEC_PREFIX='/host/gcc', CPATH='/host/include',
                          GNUARMEMB_TOOLCHAIN_PATH='/host/arm-gcc', CROSS_COMPILE='/host/arm-',
                          ZEPHYR_EXTRA_MODULES='/host/modules', VIRTUAL_ENV='/host/venv',
                          CONDA_PREFIX='/host/conda')
        _, _, environment = nordic.installed_tools(self.options(sdk, toolchain), compiler=True)
        for key in ('CMAKE_PREFIX_PATH', 'CMAKE_GENERATOR', 'CMAKE_TOOLCHAIN_FILE',
                    'CMAKE_C_COMPILER_LAUNCHER', 'CMAKE_CXX_COMPILER_LAUNCHER',
                    'GCC_EXEC_PREFIX', 'CPATH', 'GNUARMEMB_TOOLCHAIN_PATH',
                    'CROSS_COMPILE', 'ZEPHYR_EXTRA_MODULES', 'VIRTUAL_ENV', 'CONDA_PREFIX'):
            self.assertNotIn(key, environment)
        self.assertNotIn('/external/bin', environment['PATH'].split(os.pathsep))


class BuildScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        project = mock.patch.object(build_script, 'ROOT', Path('/portable-project'))
        project.start()
        self.addCleanup(project.stop)
        cache = mock.patch.object(build_script, 'validate_build_cache')
        self.cache_validation = cache.start()
        self.addCleanup(cache.stop)
        zap = mock.patch.object(build_script, 'installed_zap', return_value=Path('/installed ZAP/zap-cli'))
        self.zap_selection = zap.start()
        self.addCleanup(zap.stop)

    def test_unsupported_path_characters_are_reported_clearly(self) -> None:
        for path in ('/project with spaces', '/project;second', '/project\ttab', '/project\nline'):
            with self.subTest(path=path):
                with self.assertRaisesRegex(RuntimeError, 'whitespace or semicolons'):
                    build_script.validate_build_paths(Path(path))

    def test_portable_paths_are_accepted(self) -> None:
        build_script.validate_build_paths(Path('/project'), Path('/ncs/v3.4.1'),
                                          Path('/ncs/toolchains/platform-specific-bundle'))

    def test_invalid_project_path_is_rejected_before_loading_tools(self) -> None:
        with mock.patch.object(build_script, 'ROOT', Path('/project with spaces')), \
                mock.patch.object(build_script, 'installed_tools') as installed, \
                mock.patch.object(build_script, 'run') as run, \
                mock.patch.object(sys, 'argv', ['build.py', '--dry-run', '--build-dir', '/build']):
            with self.assertRaisesRegex(RuntimeError, 'whitespace or semicolons'):
                build_script.main()
        installed.assert_not_called()
        run.assert_not_called()

    def test_invalid_build_directory_is_rejected_before_loading_tools(self) -> None:
        with mock.patch.object(build_script, 'installed_tools') as installed, \
                mock.patch.object(build_script, 'run') as run, \
                mock.patch.object(sys, 'argv', ['build.py', '--dry-run', '--build-dir', '/build;other']):
            with self.assertRaisesRegex(RuntimeError, 'whitespace or semicolons'):
                build_script.main()
        installed.assert_not_called()
        run.assert_not_called()

    def test_unsupported_installed_tool_paths_are_rejected_before_west_build(self) -> None:
        fixtures = (
            (Path('/ncs with spaces/v3.4.1'), Path('/tools/bin/python3'), '/tools/opt/zephyr-sdk'),
            (Path('/ncs/v3.4.1'), Path('/tools;other/bin/python3'), '/tools/opt/zephyr-sdk'),
            (Path('/ncs/v3.4.1'), Path('/tools/bin/python3'), '/tools/opt/zephyr sdk'),
        )
        for sdk, python, zephyr_sdk in fixtures:
            with self.subTest(sdk=sdk, python=python, zephyr_sdk=zephyr_sdk), \
                    mock.patch.object(build_script, 'installed_tools', return_value=(
                        sdk, python, {'ZEPHYR_SDK_INSTALL_DIR': zephyr_sdk})), \
                    mock.patch.object(build_script, 'run') as run, \
                    mock.patch.object(sys, 'argv', ['build.py', '--dry-run', '--build-dir', '/build']):
                with self.assertRaisesRegex(RuntimeError, 'whitespace or semicolons'):
                    build_script.main()
            run.assert_not_called()

    def test_build_command_pins_selected_sdk_python_and_generator(self) -> None:
        sdk = Path('/relocated-ncs/v3.4.1')
        python = Path('/relocated-tools/bin/python3')
        environment = {'ZEPHYR_SDK_INSTALL_DIR': '/relocated-tools/opt/zephyr-sdk'}
        with mock.patch.object(build_script, 'installed_tools', return_value=(sdk, python, environment)) as installed, \
                mock.patch.object(build_script, 'run') as run, \
                mock.patch.object(sys, 'argv', ['build.py', '--dry-run', '--build-dir', '/build', '-j', '3']):
            build_script.main()
        self.assertTrue(installed.call_args.kwargs['compiler'])
        command = run.call_args.args[0]
        self.assertEqual(command[:4], [str(python), '-m', 'west', 'build'])
        self.assertIn('-GNinja', command)
        self.assertFalse(any(argument.startswith('-DBOARD_ROOT=') for argument in command))
        self.assertIn(f'-DZephyr_DIR={sdk / "zephyr/share/zephyr-package/cmake"}', command)
        self.assertIn(f'-DPython3_EXECUTABLE={python}', command)
        self.assertIn('-DMAILBOX_ZAP_EXECUTABLE=/installed ZAP/zap-cli', command)
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs['cwd'], sdk)
        self.assertTrue(run.call_args.kwargs['dry_run'])
        self.assertEqual(run.call_args.kwargs['env']['CMAKE_BUILD_PARALLEL_LEVEL'], '3')
        self.cache_validation.assert_called_once_with(Path('/build'), sdk, python, environment)

    def test_explicit_zap_path_is_forwarded_to_discovery(self) -> None:
        tools = (Path('/ncs/v3.4.1'), Path('/tools/bin/python3'),
                 {'ZEPHYR_SDK_INSTALL_DIR': '/tools/opt/zephyr-sdk'})
        with mock.patch.object(build_script, 'installed_tools', return_value=tools), \
                mock.patch.object(build_script, 'run'), \
                mock.patch.object(sys, 'argv', ['build.py', '--dry-run', '--build-dir', '/build',
                                               '--zap', '/custom ZAP/zap-cli']):
            build_script.main()
        self.zap_selection.assert_called_once_with(Path('/custom ZAP/zap-cli'))

    def test_real_local_build_generates_or_reuses_credentials_before_west(self) -> None:
        tools = (Path('/ncs/v3.4.1'), Path('/tools/bin/python3'),
                 {'ZEPHYR_SDK_INSTALL_DIR': '/tools/opt/zephyr-sdk'})
        with mock.patch.object(build_script, 'installed_tools', return_value=tools), \
                mock.patch.object(build_script, 'run') as run, \
                mock.patch.object(sys, 'argv', ['build.py', '--board', 'nrf54l15', '--build-dir', '/build']):
            build_script.main()
        self.assertEqual(run.call_count, 2)
        provision, build = run.call_args_list
        self.assertEqual(provision.args[0], ['/tools/bin/python3', '/portable-project/scripts/provision.py',
                                           '--output-dir', '/portable-project/provisioning/nrf54l15'])
        self.assertIs(provision.kwargs['dry_run'], False)
        self.assertIn('-DEXTRA_CONF_FILE=/portable-project/provisioning/nrf54l15/provisioning.conf',
                      build.args[0])

    def test_github_profile_explicitly_uses_defaults_without_generating_credentials(self) -> None:
        tools = (Path('/ncs/v3.4.1'), Path('/tools/bin/python3'),
                 {'ZEPHYR_SDK_INSTALL_DIR': '/tools/opt/zephyr-sdk'})
        with mock.patch.object(build_script, 'installed_tools', return_value=tools), \
                mock.patch.object(build_script, 'load_provisioning') as load, \
                mock.patch.object(build_script, 'run') as run, \
                mock.patch.object(sys, 'argv', ['build.py', '--default-provisioning', '--build-dir', '/build']):
            build_script.main()
        load.assert_not_called()
        run.assert_called_once()
        self.assertIn('-DEXTRA_CONF_FILE=/portable-project/config/default-provisioning.conf', run.call_args.args[0])
        self.assertIn('-DEXTRA_DTC_OVERLAY_FILE=', run.call_args.args[0])

    def test_explicit_identity_is_validated_and_shared_across_build_variants(self) -> None:
        tools = (Path('/ncs/v3.4.1'), Path('/tools/bin/python3'),
                 {'ZEPHYR_SDK_INSTALL_DIR': '/tools/opt/zephyr-sdk'})
        with mock.patch.object(build_script, 'installed_tools', return_value=tools), \
                mock.patch.object(build_script, 'load_provisioning') as load, \
                mock.patch.object(build_script, 'run') as run, \
                mock.patch.object(sys, 'argv', ['build.py', '--dry-run', '--console', '--build-dir', '/build',
                                               '--provisioning', '/device/provisioning.json']):
            build_script.main()
        load.assert_called_once_with(Path('/device/provisioning.json'))
        run.assert_called_once()  # Dry runs never create credentials or pairing images.
        self.assertIn('-DEXTRA_CONF_FILE=/portable-project/config/console.conf;/device/provisioning.conf',
                      run.call_args.args[0])

    def test_pristine_cannot_delete_provisioning_inside_the_build_directory(self) -> None:
        tools = (Path('/ncs/v3.4.1'), Path('/tools/bin/python3'),
                 {'ZEPHYR_SDK_INSTALL_DIR': '/tools/opt/zephyr-sdk'})
        with mock.patch.object(build_script, 'installed_tools', return_value=tools), \
                mock.patch.object(build_script, 'pristine_removed_sdk_cache') as pristine, \
                mock.patch.object(build_script, 'run') as run, \
                mock.patch.object(sys, 'argv', ['build.py', '--pristine', '--build-dir', '/build',
                                               '--provisioning', '/build/credentials/provisioning.json']):
            with self.assertRaisesRegex(RuntimeError, 'outside the build directory'):
                build_script.main()
        pristine.assert_not_called()
        run.assert_not_called()

    def test_missing_zap_stops_before_west_build(self) -> None:
        self.zap_selection.side_effect = RuntimeError('Installed ZAP CLI not found.')
        tools = (Path('/ncs/v3.4.1'), Path('/tools/bin/python3'),
                 {'ZEPHYR_SDK_INSTALL_DIR': '/tools/opt/zephyr-sdk'})
        with mock.patch.object(build_script, 'installed_tools', return_value=tools), \
                mock.patch.object(build_script, 'run') as run, \
                mock.patch.object(sys, 'argv', ['build.py', '--dry-run', '--build-dir', '/build']):
            with self.assertRaisesRegex(RuntimeError, 'ZAP'):
                build_script.main()
        run.assert_not_called()

    def test_pristine_build_reconfigures_without_reusing_cache(self) -> None:
        self.cache_validation.side_effect = RuntimeError('Stale compiler cache')
        tools = (Path('/ncs/v3.4.1'), Path('/tools/bin/python3'),
                 {'ZEPHYR_SDK_INSTALL_DIR': '/tools/opt/zephyr-sdk'})
        with mock.patch.object(build_script, 'installed_tools', return_value=tools), \
                mock.patch.object(build_script, 'run') as run, \
                mock.patch.object(sys, 'argv', ['build.py', '--dry-run', '--build-dir', '/build', '--pristine']):
            build_script.main()
        self.cache_validation.assert_not_called()
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('-p') + 1], 'always')

    def test_stale_cache_stops_build_with_a_pristine_instruction(self) -> None:
        self.cache_validation.side_effect = RuntimeError('Run the build again with --pristine.')
        tools = (Path('/ncs/v3.4.1'), Path('/tools/bin/python3'),
                 {'ZEPHYR_SDK_INSTALL_DIR': '/tools/opt/zephyr-sdk'})
        with mock.patch.object(build_script, 'installed_tools', return_value=tools), \
                mock.patch.object(build_script, 'run') as run, \
                mock.patch.object(sys, 'argv', ['build.py', '--dry-run', '--build-dir', '/build']):
            with self.assertRaisesRegex(RuntimeError, '--pristine'):
                build_script.main()
        run.assert_not_called()

    def test_pristine_build_cleans_removed_sdk_cache_before_west(self) -> None:
        tools = (Path('/ncs/v3.4.1'), Path('/tools/bin/python3'),
                 {'ZEPHYR_SDK_INSTALL_DIR': '/tools/opt/zephyr-sdk'})
        with mock.patch.object(build_script, 'installed_tools', return_value=tools), \
                mock.patch.object(build_script, 'pristine_removed_sdk_cache') as pristine, \
                mock.patch.object(build_script, 'run'), \
                mock.patch.object(sys, 'argv', ['build.py', '--dry-run', '--build-dir', '/build', '--pristine']):
            build_script.main()
        pristine.assert_called_once_with(Path('/build'), tools[0], tools[2], dry_run=True)


class BuildCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix='nordic-cache-tests-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.build = self.root / 'build'
        self.build.mkdir()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.sdk = self.root / 'sdk'
        (self.sdk / 'zephyr/share/zephyr-package/cmake').mkdir(parents=True)
        self.toolchain = self.root / 'toolchain'
        (self.toolchain / 'bin').mkdir(parents=True)
        for command in ('python3', 'cmake', 'ninja', 'gn', 'git', 'dtc',
                        'arm-zephyr-eabi-gcc', 'arm-zephyr-eabi-g++'):
            executable = self.toolchain / 'bin' / command
            executable.touch()
            executable.chmod(0o755)
        self.python = self.toolchain / 'bin/python3'
        self.environment = {'PATH': str(self.toolchain / 'bin')}
        project = mock.patch.object(build_script, 'ROOT', self.project)
        project.start()
        self.addCleanup(project.stop)

    def cache(self, **values: Path) -> None:
        (self.build / 'CMakeCache.txt').write_text('\n'.join(
            f'{key}:FILEPATH={value}' for key, value in values.items()) + '\n')

    def validate(self) -> None:
        build_script.validate_build_cache(self.build, self.sdk, self.python, self.environment)

    def test_fresh_build_without_a_cache_is_accepted(self) -> None:
        self.validate()

    def test_stale_sdk_cache_requires_pristine(self) -> None:
        for key in ('ZEPHYR_BASE', 'Zephyr_DIR'):
            with self.subTest(key=key):
                self.cache(**{key: self.root / 'old-sdk/zephyr'})
                with self.assertRaisesRegex(RuntimeError, '--pristine'):
                    self.validate()

    def test_stale_compiler_cache_requires_pristine(self) -> None:
        for key in ('CMAKE_C_COMPILER', 'CMAKE_CXX_COMPILER'):
            with self.subTest(key=key):
                self.cache(**{key: self.root / 'external-compiler'})
                with self.assertRaisesRegex(RuntimeError, '--pristine'):
                    self.validate()

    def test_stale_python_cache_requires_pristine(self) -> None:
        for key in ('Python3_EXECUTABLE', '_Python3_EXECUTABLE'):
            with self.subTest(key=key):
                self.cache(**{key: self.root / 'host/python3'})
                with self.assertRaisesRegex(RuntimeError, '--pristine'):
                    self.validate()

    def test_same_installation_through_symlinks_is_accepted(self) -> None:
        project_alias = self.root / 'project-alias'
        sdk_alias = self.root / 'sdk-alias'
        toolchain_alias = self.root / 'toolchain-alias'
        try:
            for alias, target in ((project_alias, self.project), (sdk_alias, self.sdk),
                                  (toolchain_alias, self.toolchain)):
                alias.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f'Creating directory symlinks is unavailable: {exc}')
        self.cache(CMAKE_HOME_DIRECTORY=project_alias,
                   ZEPHYR_BASE=sdk_alias / 'zephyr',
                   Zephyr_DIR=sdk_alias / 'zephyr/share/zephyr-package/cmake',
                   Python3_EXECUTABLE=toolchain_alias / 'bin/python3',
                   CMAKE_C_COMPILER=toolchain_alias / 'bin/arm-zephyr-eabi-gcc')
        self.validate()

    def test_pristine_removed_sdk_uses_installed_sdk_cleanup(self) -> None:
        self.cache(ZEPHYR_BASE=self.root / 'removed-sdk/zephyr', CMAKE_HOME_DIRECTORY=self.project)
        with mock.patch.object(build_script, 'run') as run:
            build_script.pristine_removed_sdk_cache(self.build, self.sdk, self.environment, dry_run=False)
        run.assert_called_once_with([
            str(self.toolchain / 'bin/cmake'), f'-DBINARY_DIR={self.build}',
            f'-DSOURCE_DIR={self.project}', '-P', str(self.sdk / 'zephyr/cmake/pristine.cmake'),
        ], cwd=self.project, env=self.environment, dry_run=False)

    def test_pristine_available_old_sdk_keeps_normal_west_cleanup(self) -> None:
        old_base = self.root / 'old-sdk/zephyr'
        (old_base / 'cmake').mkdir(parents=True)
        (old_base / 'cmake/pristine.cmake').touch()
        self.cache(ZEPHYR_BASE=old_base)
        with mock.patch.object(build_script, 'run') as run:
            build_script.pristine_removed_sdk_cache(self.build, self.sdk, self.environment, dry_run=False)
        run.assert_not_called()

    def test_pristine_prefers_cached_application_source_over_cmake_source(self) -> None:
        application = self.root / 'original-application'
        self.cache(ZEPHYR_BASE=self.root / 'removed-sdk/zephyr',
                   CMAKE_HOME_DIRECTORY=self.root / 'sysbuild', APPLICATION_SOURCE_DIR=application)
        with mock.patch.object(build_script, 'run') as run:
            build_script.pristine_removed_sdk_cache(self.build, self.sdk, self.environment, dry_run=False)
        self.assertIn(f'-DSOURCE_DIR={application}', run.call_args.args[0])

    def test_pristine_malformed_or_empty_zephyr_cache_is_left_alone(self) -> None:
        for contents in ('ZEPHYR_BASE:PATH\n', 'ZEPHYR_BASE:PATH=\n', 'ZEPHYR_BASE=no-cache-type\n'):
            with self.subTest(contents=contents), mock.patch.object(build_script, 'run') as run:
                (self.build / 'CMakeCache.txt').write_text(contents)
                build_script.pristine_removed_sdk_cache(self.build, self.sdk, self.environment, dry_run=False)
            run.assert_not_called()

    def test_pristine_removed_sdk_without_cached_source_is_rejected(self) -> None:
        self.cache(ZEPHYR_BASE=self.root / 'removed-sdk/zephyr')
        with mock.patch.object(build_script, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'missing cached application source directory'):
                build_script.pristine_removed_sdk_cache(self.build, self.sdk, self.environment, dry_run=False)
        run.assert_not_called()

    def test_pristine_directory_containing_cached_source_is_rejected(self) -> None:
        for source in (self.build, self.build / 'original-source'):
            with self.subTest(source=source), mock.patch.object(build_script, 'run') as run:
                self.cache(ZEPHYR_BASE=self.root / 'removed-sdk/zephyr', APPLICATION_SOURCE_DIR=source)
                with self.assertRaisesRegex(RuntimeError, 'contains application source files'):
                    build_script.pristine_removed_sdk_cache(self.build, self.sdk, self.environment, dry_run=False)
            run.assert_not_called()

    def test_pristine_directory_containing_current_project_is_rejected(self) -> None:
        self.cache(ZEPHYR_BASE=self.root / 'removed-sdk/zephyr', CMAKE_HOME_DIRECTORY=self.project)
        with mock.patch.object(build_script, 'ROOT', self.build / 'current-project'), \
                mock.patch.object(build_script, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'contains application source files'):
                build_script.pristine_removed_sdk_cache(self.build, self.sdk, self.environment, dry_run=False)
        run.assert_not_called()

    def test_pristine_non_zephyr_cache_is_left_alone(self) -> None:
        self.cache(CMAKE_HOME_DIRECTORY=self.project)
        with mock.patch.object(build_script, 'run') as run:
            build_script.pristine_removed_sdk_cache(self.build, self.sdk, self.environment, dry_run=False)
        run.assert_not_called()

    def test_pristine_without_cache_does_not_run_cleanup(self) -> None:
        with mock.patch.object(build_script, 'run') as run:
            build_script.pristine_removed_sdk_cache(self.build, self.sdk, self.environment, dry_run=False)
        run.assert_not_called()

    def test_pristine_dry_run_preserves_cached_build_files(self) -> None:
        self.cache(ZEPHYR_BASE=self.root / 'removed-sdk/zephyr', CMAKE_HOME_DIRECTORY=self.project)
        sentinel = self.build / 'zephyr.hex'
        sentinel.write_text('existing firmware\n')
        cache_contents = (self.build / 'CMakeCache.txt').read_text()
        with mock.patch.object(build_script, 'run', wraps=nordic.run) as run, \
                mock.patch.object(nordic.subprocess, 'run') as process, \
                mock.patch.object(sys, 'stdout'):
            build_script.pristine_removed_sdk_cache(self.build, self.sdk, self.environment, dry_run=True)
        run.assert_called_once()
        self.assertTrue(run.call_args.kwargs['dry_run'])
        process.assert_not_called()
        self.assertEqual(sentinel.read_text(), 'existing firmware\n')
        self.assertEqual((self.build / 'CMakeCache.txt').read_text(), cache_contents)


if __name__ == '__main__':
    unittest.main()
