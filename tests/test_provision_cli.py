"""Provisioner CLI output and installed-Python dependency selection checks."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('matter_provision_cli', SCRIPTS / 'provision.py')
assert SPEC is not None and SPEC.loader is not None
provision = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provision)


class ProvisionCliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix='matter provision cli ')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.arguments = ['provision.py', '--output-dir', str(self.directory)]

    def test_reports_png_card_alongside_svg_and_text_when_reusing_credentials(self) -> None:
        (self.directory / 'provisioning.json').touch()
        output = io.StringIO()
        with mock.patch.object(provision.importlib.util, 'find_spec', return_value=object()), \
                mock.patch.object(provision, 'ensure') as ensure, \
                mock.patch.object(provision, 'installed_tools') as tools, \
                mock.patch.object(sys, 'argv', self.arguments), \
                mock.patch.object(sys, 'stdout', output):
            provision.main()
        ensure.assert_called_once_with(self.directory, passcode=None, discriminator=None)
        tools.assert_not_called()
        self.assertIn(f'Reused Matter credentials: {self.directory / "provisioning.json"}', output.getvalue())
        self.assertIn(f'Pairing card: {self.directory / "pairing.png"}', output.getvalue())
        self.assertIn(f'Pairing QR: {self.directory / "pairing.svg"}', output.getvalue())
        self.assertIn(f'Pairing details: {self.directory / "pairing.txt"}', output.getvalue())

    def test_missing_pillow_selects_the_installed_nordic_python(self) -> None:
        python = Path('/installed-nordic/bin/python3')
        environment = {'PATH': '/installed-nordic/bin'}
        with mock.patch.object(provision.importlib.util, 'find_spec',
                               side_effect=lambda module: None if module == 'PIL' else object()) as find, \
                mock.patch.object(provision, 'installed_tools',
                                  return_value=(Path('/sdk'), python, environment)), \
                mock.patch.object(provision.subprocess, 'run') as run, \
                mock.patch.object(provision, 'ensure') as ensure, \
                mock.patch.object(sys, 'argv', self.arguments):
            provision.main()
        self.assertEqual(find.call_args_list, [mock.call('qrcode'), mock.call('cryptography'), mock.call('PIL')])
        run.assert_called_once_with([str(python), str(SCRIPTS / 'provision.py'), *self.arguments[1:]],
                                    env=environment, check=True)
        ensure.assert_not_called()

    def test_missing_pillow_in_the_selected_python_fails_without_recursion(self) -> None:
        error = io.StringIO()
        with mock.patch.object(provision.importlib.util, 'find_spec',
                               side_effect=lambda module: None if module == 'PIL' else object()), \
                mock.patch.object(provision, 'installed_tools',
                                  return_value=(Path('/sdk'), Path(sys.executable), {})), \
                mock.patch.object(provision.subprocess, 'run') as run, \
                mock.patch.object(provision, 'ensure') as ensure, \
                mock.patch.object(sys, 'argv', self.arguments), \
                mock.patch.object(sys, 'stderr', error):
            with self.assertRaises(SystemExit) as failure:
                provision.main()
        self.assertEqual(failure.exception.code, 1)
        self.assertIn('The installed Nordic Python lacks Pillow. No packages installed.', error.getvalue())
        run.assert_not_called()
        ensure.assert_not_called()

    def test_missing_dependencies_are_named_when_no_nordic_python_is_available(self) -> None:
        error = io.StringIO()
        with mock.patch.object(provision.importlib.util, 'find_spec', return_value=None), \
                mock.patch.object(provision, 'installed_tools', side_effect=RuntimeError('SDK not found.')), \
                mock.patch.object(provision, 'ensure') as ensure, \
                mock.patch.object(sys, 'argv', self.arguments), \
                mock.patch.object(sys, 'stderr', error):
            with self.assertRaises(SystemExit) as failure:
                provision.main()
        self.assertEqual(failure.exception.code, 1)
        self.assertIn('Matter provisioning needs qrcode, cryptography, Pillow; SDK not found.', error.getvalue())
        ensure.assert_not_called()


if __name__ == '__main__':
    unittest.main()
