"""Flash checks must use the credential identity selected for the firmware."""
from __future__ import annotations

import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
SPEC = importlib.util.spec_from_file_location('mailbox_flash_provisioning', SCRIPTS / 'flash.py')
assert SPEC is not None and SPEC.loader is not None
flash = importlib.util.module_from_spec(SPEC)
with mock.patch.object(sys, 'path', [str(SCRIPTS), *sys.path]):
    SPEC.loader.exec_module(flash)


class FlashProvisioningTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix='mailbox-flash-provisioning-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.build = self.root / 'build'

    def commands(self, *selection: str) -> list[list[str]]:
        tools = (Path('/ncs/v3.4.1'), Path('/tools/bin/python3'), {'PATH': '/tools/bin'})
        argv = ['flash.py', '--runner', 'uf2', '--dry-run', '--build-dir', str(self.build), *selection]
        with mock.patch.object(flash, 'installed_tools', return_value=tools), \
                mock.patch.object(flash, 'run') as run, \
                mock.patch.object(flash.subprocess, 'run') as process, \
                mock.patch.object(sys, 'argv', argv):
            flash.main()
        process.assert_not_called()
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            self.assertIs(call.kwargs['dry_run'], True)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][:2], ['/tools/bin/python3', str(flash.ROOT / 'scripts/check-build.py')])
        self.assertEqual(commands[1], ['/tools/bin/python3', str(flash.ROOT / 'scripts/uf2_flash.py'),
                                       str(self.build / 'zephyr/zephyr.uf2')])
        return commands

    def test_explicit_custom_credentials_are_forwarded_to_checker_only(self) -> None:
        manifest = self.root / 'mailbox-01/provisioning.json'
        check, _ = self.commands('--provisioning', str(manifest))
        self.assertEqual(check[-2:], ['--provisioning', str(manifest)])
        self.assertNotIn('--default-provisioning', check)

    def test_published_credentials_are_forwarded_to_checker_only(self) -> None:
        check, _ = self.commands('--default-provisioning')
        self.assertEqual(check[-1], '--default-provisioning')
        self.assertNotIn('--provisioning', check)

    def test_implicit_local_credentials_use_checker_board_default(self) -> None:
        check, _ = self.commands()
        self.assertEqual(check[2:], ['--board', 'nrf52840', '--build-dir', str(self.build)])

    def test_conflicting_credential_selections_fail_before_tools_or_devices(self) -> None:
        argv = ['flash.py', '--runner', 'uf2', '--dry-run', '--provisioning', '/device/provisioning.json',
                '--default-provisioning']
        with mock.patch.object(flash, 'installed_tools') as installed, \
                mock.patch.object(flash, 'run') as run, \
                mock.patch.object(sys, 'argv', argv), \
                contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as result:
            flash.main()
        self.assertEqual(result.exception.code, 2)
        installed.assert_not_called()
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
