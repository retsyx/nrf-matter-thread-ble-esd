#!/usr/bin/env python3
"""Monitor installed firmware via diagnostic serial console or a J-Link RTT probe."""
import argparse
from collections import deque
from pathlib import Path
import select
import subprocess
import sys
import time

from nordic import add_options, installed_tools, run

DEVICES = {'nrf52840': 'nRF52840_xxAA', 'nrf54l15': 'nRF54L15_M33'}


def _library(override: Path | None) -> Path:
    candidates = [override.expanduser()] if override else [
        Path('/Applications/SEGGER/JLink/libjlinkarm.dylib'),
        *sorted(Path('/Applications/SEGGER').glob('JLink_V*/libjlinkarm.dylib'), reverse=True),
        Path('/opt/SEGGER/JLink/libjlinkarm.so'),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError('Installed J-Link library not found. Pass --jlink-library with its existing path. No tools installed.')


def monitor(args: argparse.Namespace) -> None:
    commands = deque(args.command)
    interactive = sys.stdin.isatty()
    start = time.monotonic()
    next_command = start
    probe = None
    port = None
    rtt_started = False
    try:
        if args.rtt:
            import pylink
            from pylink.library import Library
            probe = pylink.JLink(lib=Library(dllpath=str(_library(args.jlink_library))))
            probe.open(serial_no=int(args.serial) if args.serial else None)
            probe.set_tif(pylink.enums.JLinkInterfaces.SWD)
            probe.connect(DEVICES[args.board], speed=4000)
            probe.rtt_start()
            rtt_started = True
            deadline = time.monotonic() + args.connect_timeout
            while True:
                try:
                    ready = probe.rtt_get_num_up_buffers() > args.channel
                except pylink.errors.JLinkRTTException:
                    # Discovery happens asynchronously after rtt_start().
                    ready = False
                if ready:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('RTT control block not found; ensure RTT firmware is running and select the correct channel')
                time.sleep(0.05)
        else:
            import serial
            # The nRF54L15's SAMD11 bridge needs DTR to forward UART traffic.
            # Keep it low for nRF52840 adapters that may wire it to reset.
            port = serial.Serial(port=None, baudrate=args.baud, timeout=0.05,
                                 write_timeout=1, rtscts=False, dsrdtr=False)
            port.dtr = args.board == 'nrf54l15'
            port.rts = False
            port.port = args.port
            port.open()
        start = time.monotonic()
        while not args.duration or time.monotonic() - start < args.duration:
            data = bytes(probe.rtt_read(args.channel, 8192)) if probe else port.read(8192)
            if data:
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
            if interactive and select.select([sys.stdin], [], [], 0)[0]:
                line = sys.stdin.readline()
                if line:
                    commands.append(line.rstrip('\r\n'))
                else:
                    interactive = False
            if commands and time.monotonic() >= next_command:
                outgoing = (commands.popleft() + '\r\n').encode()
                if probe:
                    if probe.rtt_get_num_down_buffers() <= args.channel:
                        raise RuntimeError('Selected RTT channel has no input buffer; use --console and UART for shell commands')
                    written = probe.rtt_write(args.channel, list(outgoing))
                    if written != len(outgoing):
                        raise RuntimeError('RTT input buffer is full; command could not be sent completely')
                else:
                    port.write(outgoing)
                next_command = time.monotonic() + 0.3
            if probe and not data:
                time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        if port:
            port.close()
        if probe:
            try:
                if rtt_started:
                    probe.rtt_stop()
            finally:
                probe.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_options(parser, variants=False)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--port', help='serial device; build firmware with --console or --usb-console')
    source.add_argument('--rtt', action='store_true', help='read default firmware RTT logs through a J-Link SWD probe')
    parser.add_argument('--serial', help='J-Link probe serial number for RTT')
    parser.add_argument('--jlink-library', type=Path, help='existing J-Link shared library')
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--duration', type=float, default=0, help='seconds; zero runs until interrupted')
    parser.add_argument('--connect-timeout', type=float, default=10, help='seconds to wait for RTT control block')
    parser.add_argument('--channel', type=int, default=0, help='RTT channel (default: 0)')
    parser.add_argument('--command', action='append', default=[], help='send a shell command after opening UART')
    parser.add_argument('--_worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.duration < 0 or args.connect_timeout <= 0 or args.baud <= 0 or args.channel < 0:
        parser.error('duration/channel must be nonnegative and timeout/baud positive')
    if args.serial and (not args.rtt or not args.serial.isdecimal()):
        parser.error('--serial requires --rtt and a numeric J-Link serial number')
    if args.jlink_library and not args.rtt:
        parser.error('--jlink-library requires --rtt')
    if args._worker:
        try:
            monitor(args)
        except Exception as exc:
            raise RuntimeError(f'Monitor failed: {exc}') from exc
        return
    sdk, python, env = installed_tools(args)
    module = 'pylink' if args.rtt else 'serial'
    subprocess.run([str(python), '-c', f'import {module}'], env=env, check=True)
    if args.rtt:
        _library(args.jlink_library)
    command = [str(python), '-u', str(Path(__file__).resolve()), *sys.argv[1:], '--_worker']
    run(command, cwd=sdk, env=env, dry_run=args.dry_run)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
