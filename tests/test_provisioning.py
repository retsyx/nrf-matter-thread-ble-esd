"""Validate pairing interoperability and safe retention of local device identities."""
from __future__ import annotations

import base64
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
SPEC = importlib.util.spec_from_file_location('provisioning', SCRIPTS / 'provisioning.py')
assert SPEC is not None and SPEC.loader is not None
provisioning = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provisioning)

# The published Matter/NCS development vector. This is also the project's
# existing default QR/manual code and must not change for GitHub releases.
DEFAULT = {
    'version': 1, 'vendor_id': 65521, 'product_id': 32772,
    'passcode': 20202021, 'discriminator': 3840, 'iterations': 1000,
    'salt': 'U1BBS0UyUCBLZXkgU2FsdA==',
    'verifier': ('uWFwqugDNGiEck/po7KHwwMwwqZgN10XuyBajPGuyzUEV/iree4lOrao5GuwnlQ65CJzbeUB49s31EH+'
                 'NEkg0JVI5MGCQGMMT/SRPFNRODm3wH/MBiehuFc6FJ/NH6Rmzw=='),
    'manual_code': '34970112332', 'qr_payload': 'MT:4CT9142C00KA0648G00',
}
HAS_CRYPTOGRAPHY = importlib.util.find_spec('cryptography') is not None
HAS_IMAGES = all(importlib.util.find_spec(name) is not None for name in ('PIL', 'qrcode'))
HAS_QR_DECODER = importlib.util.find_spec('cv2') is not None


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix='matter-provisioning-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_existing_debug_pairing_codes_are_unchanged(self) -> None:
        self.assertEqual(provisioning.manual_code(20202021, 3840), DEFAULT['manual_code'])
        self.assertEqual(provisioning.qr_payload(20202021, 3840), DEFAULT['qr_payload'])
        self.assertEqual(provisioning.validate(DEFAULT), DEFAULT)

    def test_custom_pairing_codes_match_independent_sdk_vectors(self) -> None:
        # Generated independently with the installed SDK's SetupPayload class,
        # standard flow, BLE rendezvous (2), VID 0xFFF1 and PID 0x8004.
        vectors = [
            (1, 1, '00000100007', 'MT:4CT91CEK01ID0000000'),
            (99999998, 4095, '35759861036', 'MT:4CT91MBW171DQ36B420'),
            (23456789, 1234, '11128514311', 'MT:4CT91KQS02GR6U6VI00'),
        ]
        for passcode, discriminator, manual, qr in vectors:
            with self.subTest(passcode=passcode, discriminator=discriminator):
                self.assertEqual(provisioning.manual_code(passcode, discriminator), manual)
                self.assertEqual(provisioning.qr_payload(passcode, discriminator), qr)

    def test_complete_kconfig_includes_matching_verifier_inputs(self) -> None:
        lines = provisioning.config_text(DEFAULT).splitlines()
        self.assertIn('CONFIG_CHIP_DEVICE_DISCRIMINATOR=0xF00', lines)
        self.assertIn('CONFIG_CHIP_DEVICE_SPAKE2_PASSCODE=20202021', lines)
        self.assertIn('CONFIG_CHIP_DEVICE_SPAKE2_IT=1000', lines)
        self.assertIn('CONFIG_CHIP_DEVICE_SPAKE2_SALT="' + DEFAULT['salt'] + '"', lines)
        self.assertIn('CONFIG_CHIP_DEVICE_SPAKE2_TEST_VERIFIER="' + DEFAULT['verifier'] + '"', lines)

    def test_github_profile_matches_the_published_spake2_identity(self) -> None:
        profile = SCRIPTS.parent / 'config/default-provisioning.conf'
        actual = dict(line.split('=', 1) for line in profile.read_text().splitlines() if line.startswith('CONFIG_'))
        expected = dict(line.split('=', 1) for line in provisioning.config_text(DEFAULT).splitlines()
                        if line.startswith('CONFIG_'))
        name = 'CONFIG_CHIP_DEVICE_DISCRIMINATOR'
        self.assertEqual(int(actual.pop(name), 16), int(expected.pop(name), 16))
        self.assertEqual(actual, expected)

    def test_load_needs_no_optional_modules(self) -> None:
        manifest = self.directory / 'provisioning.json'
        manifest.write_text(json.dumps(DEFAULT))
        with mock.patch.dict(sys.modules, {'cryptography': None, 'qrcode': None}):
            self.assertEqual(provisioning.load(manifest), DEFAULT)

    def test_invalid_or_mismatching_manifest_is_rejected(self) -> None:
        mutations = {
            'version': 2, 'vendor_id': 1, 'product_id': 1,
            'passcode': 12345678, 'discriminator': 0, 'iterations': 999,
            'salt': 'not-base64', 'verifier': DEFAULT['salt'],
            'manual_code': '34970112333', 'qr_payload': 'MT:WRONG',
        }
        for field, value in mutations.items():
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                provisioning.validate({**DEFAULT, field: value})
        with self.assertRaises(RuntimeError):
            provisioning.validate({**DEFAULT, 'extra': 1})
        with self.assertRaises(RuntimeError):
            provisioning.validate({**DEFAULT, 'version': True})

    def test_all_invalid_matter_passcodes_are_rejected(self) -> None:
        for passcode in [*provisioning.INVALID_PASSCODES, -1, 99999999, True]:
            with self.subTest(passcode=passcode), self.assertRaises(RuntimeError):
                provisioning.generate(passcode=passcode, discriminator=1)

    def test_invalid_saved_json_never_rotates_to_new_credentials(self) -> None:
        manifest = self.directory / 'provisioning.json'
        manifest.write_text('{broken json')
        with self.assertRaises(RuntimeError):
            provisioning.ensure(self.directory)
        self.assertEqual(manifest.read_text(), '{broken json')

    def test_orphan_artifact_does_not_trigger_new_identity(self) -> None:
        for name in ('pairing.svg', 'pairing.png'):
            artifact = self.directory / name
            artifact.write_bytes(b'saved pairing code')
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'incomplete provisioning'):
                provisioning.ensure(self.directory)
            self.assertFalse((self.directory / 'provisioning.json').exists())
            artifact.unlink()


@unittest.skipUnless(HAS_IMAGES, 'qrcode and Pillow are provided by the Nordic toolchain')
class PairingCardTests(unittest.TestCase):
    def test_printable_png_card_is_deterministic(self) -> None:
        from PIL import Image
        first = provisioning._pairing_png(DEFAULT)
        self.assertEqual(first, provisioning._pairing_png(DEFAULT))
        with Image.open(io.BytesIO(first)) as card:
            self.assertEqual(card.format, 'PNG')
            self.assertEqual(card.mode, 'RGB')
            self.assertEqual(card.size, (900, 1200))
            self.assertAlmostEqual(card.info['dpi'][0], 300, places=1)
            self.assertEqual(card.getpixel((0, 0)), (255, 255, 255))

    @unittest.skipUnless(HAS_QR_DECODER, 'OpenCV is used only for independent QR validation')
    def test_qr_remains_scannable_on_the_complete_card(self) -> None:
        import cv2
        import numpy
        from PIL import Image
        # Include the leading-zero manual-code case from the independent SDK vectors.
        for data in (DEFAULT, DEFAULT | {'manual_code': '00000100007', 'qr_payload': 'MT:4CT91CEK01ID0000000'}):
            with self.subTest(payload=data['qr_payload']):
                with Image.open(io.BytesIO(provisioning._pairing_png(data))) as card:
                    decoded, points, _ = cv2.QRCodeDetector().detectAndDecode(numpy.array(card))
                self.assertIsNotNone(points)
                self.assertEqual(decoded, data['qr_payload'])


@unittest.skipUnless(HAS_CRYPTOGRAPHY, 'cryptography is provided by the Nordic toolchain')
class CredentialGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix='matter-provisioning-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / 'device'
        # QR encoding itself is cross-checked separately against the SDK;
        # retaining an identity must also be testable without qrcode installed.
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(provisioning, '_pairing_svg', return_value=b'<svg/>').start()
        mock.patch.object(provisioning, '_pairing_png', return_value=b'PNG card').start()

    def test_spake2_verifier_matches_ncs_default_vector(self) -> None:
        data = provisioning.generate(passcode=20202021, discriminator=3840,
                                     salt=base64.b64decode(DEFAULT['salt']))
        self.assertEqual(data, DEFAULT)

    def test_random_generation_retries_forbidden_passcode(self) -> None:
        with mock.patch.object(provisioning.secrets, 'randbelow', side_effect=[11111110, 20202020, 4094]):
            data = provisioning.generate()
        self.assertEqual(data['passcode'], 20202021)
        self.assertEqual(data['discriminator'], 4095)
        self.assertEqual(len(base64.b64decode(data['salt'])), 32)

    def test_repeated_generation_reuses_identity_and_restores_derived_files(self) -> None:
        first = provisioning.ensure(self.directory, passcode=20202021, discriminator=3840)
        manifest = self.directory / 'provisioning.json'
        saved = manifest.read_bytes()
        (self.directory / 'pairing.txt').unlink()
        (self.directory / 'pairing.png').unlink()
        (self.directory / 'provisioning.conf').write_text('stale')
        second = provisioning.ensure(self.directory)
        self.assertEqual(first, second)
        self.assertEqual(manifest.read_bytes(), saved)
        self.assertEqual((self.directory / 'provisioning.conf').read_text(), provisioning.config_text(first))
        for name in provisioning.ARTIFACTS:
            self.assertTrue((self.directory / name).is_file())
        if sys.platform != 'win32':
            self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)

    def test_requested_identity_change_is_rejected_without_overwrite(self) -> None:
        first = provisioning.ensure(self.directory, passcode=20202021, discriminator=3840)
        for options in ({'passcode': 20202022}, {'discriminator': 3841}):
            with self.subTest(options=options), self.assertRaisesRegex(RuntimeError, 'differs'):
                provisioning.ensure(self.directory, **options)
        self.assertEqual(provisioning.load(self.directory / 'provisioning.json'), first)

    def test_reuse_does_not_touch_incremental_build_input(self) -> None:
        provisioning.ensure(self.directory, passcode=20202021, discriminator=3840)
        config = self.directory / 'provisioning.conf'
        # Give the file an old, recognizable mtime without relying on sleeps
        # or the filesystem's timestamp resolution.
        provisioning.os.utime(config, ns=(1234567890000000000, 1234567890000000000))
        provisioning.ensure(self.directory)
        self.assertEqual(config.stat().st_mtime_ns, 1234567890000000000)

    def test_stale_but_well_formed_verifier_is_rejected(self) -> None:
        provisioning.ensure(self.directory, passcode=20202021, discriminator=3840)
        manifest = self.directory / 'provisioning.json'
        data = json.loads(manifest.read_text())
        data['salt'] = base64.b64encode(b'changed-salt-1234').decode('ascii')
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(RuntimeError, 'verifier does not match'):
            provisioning.ensure(self.directory)

    def test_concurrent_initialization_does_not_write_unsaved_pairing_codes(self) -> None:
        original_open = provisioning.os.open
        self.directory.mkdir()

        def concurrent_open(path, flags, mode=0o777):
            if Path(path).name == 'provisioning.json':
                Path(path).write_text(json.dumps(DEFAULT))
            return original_open(path, flags, mode)

        with mock.patch.object(provisioning.os, 'open', side_effect=concurrent_open):
            with self.assertRaisesRegex(RuntimeError, 'concurrently'):
                provisioning.ensure(self.directory, passcode=20202022, discriminator=3841)
        self.assertEqual(provisioning.load(self.directory / 'provisioning.json'), DEFAULT)
        self.assertFalse((self.directory / 'pairing.svg').exists())


if __name__ == '__main__':
    unittest.main()
