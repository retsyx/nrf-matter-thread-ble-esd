# XIAO hardware notes

The port uses an **external BMA400**, wired to the same XIAO header pins on both
boards. The onboard Sense IMU is a different device and is not a replacement.

The SMD carrier PCBs implement these connections for
both modules. Use [board-nRF52840](../hw/board-nRF52840/README.md) or
[board-nRF54L15](../hw/board-nRF54L15/README.md) for the
matching bare standard XIAO. Each module mounts directly to 14 edge lands and
its own two underside battery lands. The differing battery contacts require
separate carriers. KiCad sources, fabrication export commands and assembly instructions
are included; each board's assembly notes explain the exact
land patterns, exclusions and underside programming access. The original MG24
carrier itself is not a Nordic footprint.

| Connection | XIAO header | nRF52840 GPIO | nRF54L15 GPIO |
| --- | --- | --- | --- |
| BMA400 VDD/VDDIO | 3V3 | 3.3 V rail | 3.3 V rail |
| BMA400 GND | GND | Ground | Ground |
| BMA400 SDA | D4 | P0.04 | P1.10 |
| BMA400 SCL | D5 | P0.05 | P1.11 |
| BMA400 INT1 | D2 | P0.28 | P1.06 |

Use a 3.3 V compatible BMA400 board with suitable I2C pull-ups. The application
uses 100 kHz I2C and an active-high, push-pull INT1 signal. Both TWIM devices
enable Zephyr runtime power management so the driver can suspend between
transactions. The sensor stays powered to detect motion while the CPU sleeps. INT1 uses
GPIO SENSE/PORT edge detection (`sense-edge-mask`) rather than a high-accuracy
GPIOTE IN channel.

The default BMA400 address is `0x14` (SDO low). For a sensor with SDO high, change
`bma400@14` to `bma400@15` and `reg = <0x14>` to `reg = <0x15>` in the selected
application overlay under `boards/`. Keep the `mailbox_bma400` label: the
transport reads the address from devicetree.

## Battery monitoring

| Board | ADC input | Divider | Enable control |
| --- | --- | --- | --- |
| nRF52840 | P0.31 / AIN7 | 1 MΩ upper, 510 kΩ lower | P0.14 stays low |
| nRF54L15 | P1.14 / AIN7 | 10 kΩ upper, 10 kΩ lower | P1.15 high while sampling |

**nRF52840:** Seeed warns that driving P0.14 high can expose the ADC input to
excessive voltage during battery charging. This port keeps P0.14 low with a GPIO
hog and leaves the divider enabled, including builds without battery reporting.
Do not add code that switches that pin high to save power. The divider has a
small continuous load. See [Seeed's charging FAQ](https://wiki.seeedstudio.com/XIAO_BLE/#q3-what-are-the-considerations-when-using-xiao-nrf52840-sense-for-battery-charging).

**nRF54L15:** Its load switch disconnects the battery before the entire divider.
The gate starts low; the battery monitor enables it, waits 2 ms, samples the ADC,
and disables it. Divider resistance, ADC gain and input channel come from the
board overlay. Enable battery reporting only when a suitable battery is fitted.

## Console and programming

| Board | Diagnostic console | Programming |
| --- | --- | --- |
| nRF52840 | D6 TX / P1.11, D7 RX / P1.12; external 3.3 V UART adapter | UF2 bootloader or external SWD probe |
| nRF54L15 | UART20 P1.09 TX / P1.08 RX; onboard SAMD11 USB serial bridge | Onboard SAMD11 CMSIS-DAP through OpenOCD, or external J-Link / Nordic programmer via SWD |

The nRF54L15's D6/D7 pins belong to UART21 and are separate from the SAMD11
console. Its USB connector is connected to SAMD11, not directly to nRF54L15.
The NCS 3.4.1 XIAO board definition supplies OpenOCD, J-Link, nrfutil and
nrfjprog runners. OpenOCD defaults to the onboard SAMD11 CMSIS-DAP interface.
The scripts use installed tools and do not install a programmer.

Normal firmware uses RTT diagnostics and disables UART peripherals. The
`--console` variant loads the corresponding `console_*.overlay` to enable UART.

## Unused peripherals and antenna

User LEDs are driven off. The nRF52840 Sense IMU and microphone power switches
(P1.08 and P1.10) are low. On nRF54L15 Sense, their shared P0.01 power switch is
low. The charging indicator remains controlled by the board's charger hardware.

The nRF52840's unused P25Q16H flash receives command `0xB9` before Matter starts.
The command is sent through GPIO in SPI mode 0 because the QSPI driver is
disabled. CS is high afterward, clock is low, WP/HOLD are high, and the unused
MISO input buffer is disconnected with no pull. The command changes the flash's
power state without modifying its contents; the
[Puya datasheet](https://files.seeedstudio.com/wiki/github_weiruanexample/Flash_P25Q16H-UXH-IR_Datasheet.pdf)
specifies the command and timing in sections 10.30 and 5.3.

The nRF54L15 has no onboard external flash. The SDK's RF switch regulators drive
P2.03 high and P2.05 low to power the switch and select the onboard ceramic
antenna. Selecting an external antenna requires fitting an antenna and adding
`&rfsw_ctl { enable-gpios = <&gpio2 5 GPIO_ACTIVE_HIGH>; };` to the application
overlay so the SDK's regulator drives P2.05 high.

## Memory and clocks

| Board | Application range | Settings range | SRAM |
| --- | --- | --- | --- |
| nRF52840 | `0x27000–0xEBFFF` (788 KiB) | `0xEC000–0xF3FFF` (32 KiB) | 256 KiB |
| nRF54L15 | `0x0–0x16FFFF` (1472 KiB) | `0x170000–0x17CFFF` (52 KiB) | 256 KiB |

The nRF52840 layout retains the SDK's stock SoftDevice reservation below
`0x27000` and UF2 bootloader reservation from `0xF4000`. The Zephyr application
uses Nordic's controller; the reserved SoftDevice region is retained to keep
the existing bootloader layout. Avoid chip erase or recovery operations when
preserving that bootloader.

The nRF54L15 uses the SDK's `xiao_nrf54l15/nrf54l15/cpuapp` board definition.
Its application overlay replaces the SDK's MCUboot/dual-slot partition layout
with the ranges above; the application boots directly and has no OTA secondary
slot. NCS 3.4.1 already assigns the full 256 KiB SRAM and 1524 KiB RRAM to
CPUAPP. Enabling FLPR later requires revising those regions and the partitions.

The nRF54L15 HFXO and LFXO use internal 16000 fF load-capacitance settings,
following the SDK's XIAO board description. The SDK's oscillator startup timing
is retained.
These are hardware defaults, not a measured frequency calibration.

The board definitions and GPIO setup have been compiled with the installed
SDK. Hardware operation, RF performance, voltage accuracy and sleep current
still require measurements on the actual boards.

Pin and circuit references:

- [Seeed nRF52840 pin map and FAQ](https://wiki.seeedstudio.com/XIAO_BLE/)
- [Seeed nRF52840 schematic](https://files.seeedstudio.com/wiki/XIAO-BLE/Seeed_Studio_XIAO_nRF52840_PDF.pdf)
- [Seeed nRF54L15 pin map and antenna example](https://wiki.seeedstudio.com/xiao_nrf54l15_sense_getting_started/)
- [Seeed nRF54L15 schematic](https://files.seeedstudio.com/wiki/XIAO_nRF54L15/Getting_Start/nRF54L15_Schematic.pdf)
