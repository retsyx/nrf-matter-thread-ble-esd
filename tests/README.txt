Run: python3 scripts/test-driver.py

Uses Python 3 and an already-installed host C/C++ compiler; CC/CXX can override
compiler commands. It installs nothing and removes its temporary build output.

The checks compile the real Nordic Bma400Motion transport, Bosch SensorAPI and
BatteryMonitor against mocked Zephyr device, I2C, ADC, GPIO and delay services.
They cover both documented I2C addresses, unchanged motion sensitivity and INT1
mapping, disabled FIFO read circuit and automatic normal-mode switching, unused
GEN2/auto-low-power timer, configuration restoration on reinitialization, every
initialization transfer failure (including the FIFO power write), signed samples,
failed interrupt reads, IRQ service without delay, switched/permanent battery
dividers, explicit battery opt-out, conversion/range/saturation failures and safe
GPIO cleanup.

These checks establish software adapter behavior. They do not establish board
pin wiring, ADC calibration, physical motion response, Matter commissioning,
radio timing, sleep current or battery discharge lifetime. Firmware builds and
hardware tests remain separate validation steps. native_stubs is never included
in firmware builds.

The host checks also run the factory-reset button state machine: debounce,
five-second hold, release cancellation, coalesced edges, stale timeouts,
boot-held/stuck button protection, read-failure recovery and long uptime.

Build portability checks (no host compiler needed):
  python -m unittest discover -s tests -p 'test_*.py'

Run with the Python bundled in a Nordic toolchain terminal. These checks use
temporary SDK/toolchain fixtures to verify discovery at custom locations,
version pairing, override precedence, environment isolation, supported build
paths, explicit CMake tool selection and stale-cache detection.
They also cover structured SDK version files, pristine cleanup after an SDK
has been removed, and protected application ranges for fixed/mapped partitions.
ZAP tests mock the CLI to verify discovery, build-directory outputs, dry runs,
preservation of previous artifacts on generation failure and unchanged-file
timestamps. Running these tests does not require a ZAP installation.

Provisioning tests check the published QR/manual/SPAKE2+ vector, manifest
validation, retained identities, conflicting-input rejection, orphan artifacts,
concurrent first creation and unchanged build-input timestamps. Cryptographic
generation tests run when cryptography is available (included in the Nordic
toolchain). PNG card checks cover its format, readable manual pairing code,
QR content and regeneration without changing the saved identity. CLI checks
cover pairing-card output paths and fallback to installed Nordic Python when
qrcode, cryptography or Pillow is missing. Build-tool tests verify explicit
GitHub defaults and custom local generation, including protection from
--pristine deleting provisioning files.
