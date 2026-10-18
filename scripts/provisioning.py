"""Generate and retain local Matter commissioning credentials and pairing codes.

Encoding follows the NCS Matter SDK's src/setup_payload generators. SPAKE2+
follows scripts/tools/spake2p/spake2p.py: PBKDF2-SHA256 over the little-endian
32-bit passcode, two 40-byte scalars reduced modulo the P-256 group order.
Only verifier generation/checking and QR image rendering need optional modules;
load() and config_text() can be used by the build checker with standard Python.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import struct
import tempfile


VENDOR_ID = 65521
PRODUCT_ID = 32772
ITERATIONS = 1000
INVALID_PASSCODES = frozenset({0, 11111111, 22222222, 33333333, 44444444,
                              55555555, 66666666, 77777777, 88888888,
                              99999999, 12345678, 87654321})
P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
BASE38 = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-.'
ARTIFACTS = ('provisioning.json', 'provisioning.conf', 'pairing.png', 'pairing.svg', 'pairing.txt')


def _integer(value: object, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise RuntimeError(f'{name} must be an integer between {low} and {high}.')
    return value


def _passcode(value: object) -> int:
    value = _integer(value, 'Matter passcode', 1, 99999998)
    if value in INVALID_PASSCODES:
        raise RuntimeError(f'{value:08d} is a forbidden Matter passcode.')
    return value


def _discriminator(value: object) -> int:
    # The SDK's transitional provider treats the compile-time value zero as
    # "no fallback". Do not produce a valid QR code that this firmware cannot use.
    return _integer(value, 'Matter discriminator (NCS supports nonzero values here)', 1, 4095)


def _base64(value: object, name: str, low: int, high: int) -> bytes:
    if not isinstance(value, str):
        raise RuntimeError(f'{name} must be a base64 string.')
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError(f'{name} must be valid base64.') from exc
    if not low <= len(decoded) <= high:
        raise RuntimeError(f'{name} must decode to {low}..{high} bytes.')
    if base64.b64encode(decoded).decode('ascii') != value:
        raise RuntimeError(f'{name} must use canonical base64 encoding.')
    return decoded


def _verhoeff(payload: str) -> str:
    # Verhoeff base-10 multiplication and permutation, as used by Matter.
    multiply = (
        (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
        (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
        (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
        (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
        (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
        (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
        (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
        (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
        (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
        (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
    )
    permutation = (1, 5, 7, 6, 2, 8, 3, 0, 9, 4)
    accumulator = 0
    for index, digit in enumerate(reversed(payload), 1):
        permuted = int(digit)
        for _ in range(index % 8):
            permuted = permutation[permuted]
        accumulator = multiply[accumulator][permuted]
    return str(5 - accumulator if 0 < accumulator < 5 else accumulator)


def manual_code(passcode: int, discriminator: int) -> str:
    """Eleven-digit standard-flow manual pairing code, including check digit."""
    short = discriminator >> 8
    payload = f'{short >> 2}{((short & 3) << 14) | (passcode & 0x3fff):05d}{passcode >> 14:04d}'
    return payload + _verhoeff(payload)


def qr_payload(passcode: int, discriminator: int) -> str:
    """Standard commissioning flow, BLE rendezvous, fixed project VID/PID."""
    # Fields in increasing bit order: version3, VID16, PID16, flow2,
    # rendezvous8, discriminator12, passcode27, padding4 (88 bits total).
    packed = ((VENDOR_ID << 3) | (PRODUCT_ID << 19) | (2 << 37) |
              (discriminator << 45) | (passcode << 57)).to_bytes(11, 'little')
    encoded = ''
    for offset in range(0, len(packed), 3):
        chunk = packed[offset:offset + 3]
        value = int.from_bytes(chunk, 'little')
        for _ in range({1: 2, 2: 4, 3: 5}[len(chunk)]):
            encoded += BASE38[value % 38]
            value //= 38
    return 'MT:' + encoded


def generate_verifier(passcode: int, salt: bytes, iterations: int) -> bytes:
    """Derive Matter's 97-byte serialized SPAKE2+ verifier using P-256."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    except ImportError as exc:
        raise RuntimeError('The installed Nordic Python needs cryptography for Matter provisioning.') from exc
    material = hashlib.pbkdf2_hmac('sha256', struct.pack('<I', passcode), salt, iterations, 80)
    w0 = int.from_bytes(material[:40], 'big') % P256_ORDER
    w1 = int.from_bytes(material[40:], 'big') % P256_ORDER
    point = ec.derive_private_key(w1, ec.SECP256R1()).public_key()
    return w0.to_bytes(32, 'big') + point.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)


def validate(data: object, *, verify_verifier: bool = False) -> dict:
    """Validate a manifest; optional crypto check is used before generating/building."""
    if not isinstance(data, dict):
        raise RuntimeError('Matter provisioning manifest must contain a JSON object.')
    expected = {'version', 'vendor_id', 'product_id', 'passcode', 'discriminator',
                'iterations', 'salt', 'verifier', 'manual_code', 'qr_payload'}
    if set(data) != expected:
        raise RuntimeError('Matter provisioning manifest has missing or unexpected fields.')
    _integer(data['version'], 'Provisioning version', 1, 1)
    _integer(data['vendor_id'], 'Vendor ID', VENDOR_ID, VENDOR_ID)
    _integer(data['product_id'], 'Product ID', PRODUCT_ID, PRODUCT_ID)
    passcode = _passcode(data['passcode'])
    discriminator = _discriminator(data['discriminator'])
    iterations = _integer(data['iterations'], 'SPAKE2+ iterations', 1000, 100000)
    salt = _base64(data['salt'], 'SPAKE2+ salt', 16, 32)
    verifier = _base64(data['verifier'], 'SPAKE2+ verifier', 97, 97)
    if verifier[32] != 4:
        raise RuntimeError('SPAKE2+ verifier must contain an uncompressed P-256 point.')
    for key, derived in (('manual_code', manual_code(passcode, discriminator)),
                         ('qr_payload', qr_payload(passcode, discriminator))):
        if data[key] != derived:
            raise RuntimeError(f'Matter {key} does not match the provisioned credentials.')
    if verify_verifier and not secrets.compare_digest(verifier, generate_verifier(passcode, salt, iterations)):
        raise RuntimeError('SPAKE2+ verifier does not match the passcode, salt and iterations.')
    return data


def load(path: Path) -> dict:
    """Load one provisioning.json and validate its schema and pairing payloads."""
    try:
        return validate(json.loads(Path(path).read_text(encoding='utf-8')))
    except (OSError, ValueError, RuntimeError) as exc:
        raise RuntimeError(f'Cannot use Matter provisioning manifest {path}: {exc}') from exc


def config_text(data: dict) -> str:
    """Produce all five commissioning Kconfig settings, including a matching verifier."""
    validate(data)
    return (
        '# Generated local Matter commissioning credentials. Keep private.\n'
        f'CONFIG_CHIP_DEVICE_SPAKE2_PASSCODE={data["passcode"]}\n'
        f'CONFIG_CHIP_DEVICE_DISCRIMINATOR=0x{data["discriminator"]:03X}\n'
        f'CONFIG_CHIP_DEVICE_SPAKE2_IT={data["iterations"]}\n'
        f'CONFIG_CHIP_DEVICE_SPAKE2_SALT="{data["salt"]}"\n'
        f'CONFIG_CHIP_DEVICE_SPAKE2_TEST_VERIFIER="{data["verifier"]}"\n'
    )


def generate(*, passcode: int | None = None, discriminator: int | None = None,
             iterations: int = ITERATIONS, salt: bytes | None = None) -> dict:
    """Create one credential set without writing it to disk."""
    if passcode is None:
        while (passcode := secrets.randbelow(99999998) + 1) in INVALID_PASSCODES:
            pass
    passcode = _passcode(passcode)
    discriminator = _discriminator(secrets.randbelow(4095) + 1 if discriminator is None else discriminator)
    iterations = _integer(iterations, 'SPAKE2+ iterations', 1000, 100000)
    salt = secrets.token_bytes(32) if salt is None else salt
    if not isinstance(salt, bytes) or not 16 <= len(salt) <= 32:
        raise RuntimeError('SPAKE2+ salt must be 16..32 bytes.')
    data = {
        'version': 1, 'vendor_id': VENDOR_ID, 'product_id': PRODUCT_ID,
        'passcode': passcode, 'discriminator': discriminator, 'iterations': iterations,
        'salt': base64.b64encode(salt).decode('ascii'),
        'verifier': base64.b64encode(generate_verifier(passcode, salt, iterations)).decode('ascii'),
        'manual_code': manual_code(passcode, discriminator),
        'qr_payload': qr_payload(passcode, discriminator),
    }
    return validate(data)


def _pairing_svg(data: dict) -> bytes:
    try:
        import qrcode
        from qrcode.image.svg import SvgPathFillImage
    except ImportError as exc:
        raise RuntimeError('The installed Nordic Python needs qrcode for pairing.svg.') from exc
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4, box_size=10)
    code.add_data(data['qr_payload'])
    code.make(fit=True)
    output = io.BytesIO()
    code.make_image(image_factory=SvgPathFillImage).save(output)
    return output.getvalue()


def _pairing_png(data: dict) -> bytes:
    """Render a printable pairing card with an untouched, integer-scaled QR."""
    try:
        import qrcode
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError('The installed Nordic Python needs qrcode and Pillow for pairing.png.') from exc
    # Preserve the QR's four-module white quiet zone; never resize or decorate
    # its pixels. At 300 DPI the standard card is 3 x 4 inches.
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4, box_size=20)
    code.add_data(data['qr_payload'])
    code.make(fit=True)
    qr = code.make_image(fill_color='black', back_color='white').convert('RGB')
    width = max(900, qr.width + 240)
    qr_top = 220
    qr_bottom = qr_top + qr.height
    card = Image.new('RGB', (width, qr_bottom + 320), 'white')
    card.paste(qr, ((width - qr.width) // 2, qr_top))
    draw = ImageDraw.Draw(card)
    # Pillow's embedded font keeps output portable without system font paths.
    def centered(text: str, top: int, size: int) -> None:
        draw.text((width // 2, top), text, fill='black',
                  font=ImageFont.load_default(size=size), anchor='mt')

    centered('XIAO Mailbox', 75, 50)
    centered('Matter pairing card', 145, 30)
    centered('Manual pairing code', qr_bottom + 44, 28)
    manual = data['manual_code']
    centered(f'{manual[:4]}-{manual[4:7]}-{manual[7:]}', qr_bottom + 92, 64)
    centered('Keep this card for pairing after a factory reset.', qr_bottom + 214, 24)
    output = io.BytesIO()
    card.save(output, format='PNG', dpi=(300, 300))
    return output.getvalue()


def _atomic_write(path: Path, contents: bytes) -> None:
    # Leave unchanged files untouched. In particular, provisioning.conf is a
    # CMake/Kconfig input; updating its mtime needlessly reconfigures every build.
    try:
        if path.read_bytes() == contents:
            return
    except FileNotFoundError:
        pass
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(contents)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def ensure(output_dir: Path, *, passcode: int | None = None,
           discriminator: int | None = None) -> dict:
    """Create or reuse credentials; existing identities are never silently replaced.

    Derived files may be regenerated from the manifest. A mismatching explicit
    passcode/discriminator, invalid manifest, or orphan pairing files fail closed.
    Use a new directory to intentionally create another device identity.
    """
    output_dir = Path(output_dir)
    manifest = output_dir / 'provisioning.json'
    if passcode is not None:
        _passcode(passcode)
    if discriminator is not None:
        _discriminator(discriminator)
    is_new = not manifest.exists()
    if not is_new:
        data = load(manifest)
    else:
        if any((output_dir / name).exists() for name in ARTIFACTS):
            raise RuntimeError(f'Refusing to replace incomplete provisioning in {output_dir}; '
                               'restore provisioning.json or choose a new directory.')
        data = generate(passcode=passcode, discriminator=discriminator)
    for key, value in (('passcode', passcode), ('discriminator', discriminator)):
        if value is not None and data[key] != value:
            raise RuntimeError(f'Existing {key} in {manifest} differs; choose a new output directory.')
    validate(data, verify_verifier=True)
    # Prepare derived artifacts before committing new credentials, so a missing
    # QR dependency cannot leave a half-generated new provisioning directory.
    artifacts = {
        'provisioning.conf': config_text(data).encode('utf-8'),
        'pairing.png': _pairing_png(data),
        'pairing.svg': _pairing_svg(data),
        'pairing.txt': (
            'Matter pairing credentials — keep with this device.\n'
            f'Manual pairing code: {data["manual_code"]}\n'
            f'QR payload: {data["qr_payload"]}\n'
            f'Setup passcode: {data["passcode"]:08d}\n'
            f'Discriminator: {data["discriminator"]}\n'
            'Factory reset preserves these credentials.\n'
        ).encode('utf-8'),
    }
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if is_new:
        # Exclusive creation handles concurrent first builds without rotating
        # the identity another process has already saved.
        try:
            descriptor = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise RuntimeError(f'Provisioning was created concurrently at {manifest}; retry to reuse it.') from exc
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write('\n')
    for name, contents in artifacts.items():
        _atomic_write(output_dir / name, contents)
    return data
