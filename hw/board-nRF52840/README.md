# XIAO nRF52840 direct-SMD carrier

Standalone **22 × 38 mm, two-layer** KiCad project for the standard XIAO nRF52840. U1 solders directly on fourteen edge lands plus its underside battery pair, **19 BAT+ / 20 GND**. Use the matching `nrf52840` firmware build. The other MCU needs its own carrier.

- Editable [project](board-nRF52840.kicad_pro), [schematic](board-nRF52840.kicad_sch), [routed PCB](board-nRF52840.kicad_pcb) and [custom design rules](board-nRF52840.kicad_dru).
- Purchase from the [parts catalog](parts.json); assemble two front SMD placements (U1/SW1), six back SMD placements (U2, R1/R2, C1-C3), and manual through-hole J1. The local purchasing BOM export (`bom.csv`) is ignored by Git.
- Generate Gerbers, PTH/NPTH drill files and maps, and front/back SMD placement data using the commands below. Local fabrication packages, BOM exports and checksums under `fabrication/` are ignored by Git.
- Review contact positions, copper keepouts and probe access in [geometry](geometry.json). Local [footprints](Board.pretty/), [symbols](board.kicad_sym), [datasheets](datasheets/) and [STEP models](3dmodels/) accompany the project.

H1/H2 and H7/H8 are four 2.2 mm M2 mounting holes at (52,85), (68,85), (52,53), and (68,53) mm. The 16 × 32 mm corner support pattern surrounds the board center; both copper layers and their fills stay outside each mounting hole’s 2.45 mm screw-head clearance. H3–H6 provide mask-only NPTH access to module SWDIO/SWCLK/RESET/GND; backside TP1 is 3V3 reference and TP2 GND. These features add no purchased parts. J1 is **pin 1 GND, pin 2 VBAT**; verify battery plug polarity.

**SW1 factory reset:** hold the top-actuated button for 5 seconds to erase Matter/Thread user state and reboot ready for pairing with the same provisioned Matter credentials. SW1 connects D0 (P0.02) to GND and uses the firmware internal pull-up; its released contact is open. It sits above the module between the two USB-end mounting holes, outside the antenna keepout. The module RESET probe contact is separate and only resets the MCU. See [switch datasheet](datasheets/omron-b3u.pdf) and [geometry](geometry.json) for the footprint and placement.

The locally generated module STEP shows only the official bare PCB core, with a nominal 0.1 mm solder gap. Module electronics, antenna and USB solids are omitted. Regenerate this model after a fresh checkout using the [model provenance and reproduction notes](3dmodels/README.md).

Revision C reduces the carrier to 22 × 38 mm and centers U2/BMA400 at (60,69) mm on the back. U1 is rotated 90° at (59.5,69) mm on the front, centered along the board length while retaining side-edge USB access and the complete antenna and probe-contact keepouts. H7/H8 support the opposite end of the board from H1/H2, and all tracks avoid right-angle joins, including branches. The board passed KiCad 10.0.6 ERC and DRC with zero violations, zero unconnected items and zero schematic-parity issues on 2026-10-20.

Open the project in KiCad 10.0.6. The editable design has not been fabricated or assembled. The custom rules allow the intentional H3–H6 probe holes inside U1's front courtyard; other clearance checks remain active.

Use KiCad's schematic and PCB editors for design changes. The installed [KiCad CLI](https://docs.kicad.org/10.0/en/cli/cli.html) can check and export the current files. Run these commands from the repository root; fresh output goes to a separate temporary directory.

```sh
kicad_cli=/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli
xiao_board=hw/board-nRF52840
xiao_out=/tmp/xiao-nrf52840-export
mkdir -p "$xiao_out"
"$kicad_cli" sch erc --severity-all --exit-code-violations --output "$xiao_out/erc.rpt" "$xiao_board/board-nRF52840.kicad_sch"
"$kicad_cli" pcb drc --severity-all --schematic-parity --exit-code-violations --output "$xiao_out/drc.rpt" "$xiao_board/board-nRF52840.kicad_pcb"
```

Run the geometric and trace-angle audit from the repository root with `python3 scripts/check-pcb-layout.py hw/board-nRF52840/board-nRF52840.kicad_pcb`. It checks the centered BMA400, four mounting holes opposing each other around the center, and all perpendicular copper joins.

Review any ERC/DRC violations before exporting manufacturing files. In the same shell:

```sh
"$kicad_cli" sch export pdf --output "$xiao_out/schematic.pdf" "$xiao_board/board-nRF52840.kicad_sch"
"$kicad_cli" pcb export gerbers --layers "F.Cu,B.Cu,F.Mask,B.Mask,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,Edge.Cuts" --no-protel-ext --use-drill-file-origin --subtract-soldermask --output "$xiao_out/gerbers/" "$xiao_board/board-nRF52840.kicad_pcb"
"$kicad_cli" pcb export drill --format excellon --drill-origin plot --excellon-separate-th --generate-map --generate-report --output "$xiao_out/gerbers/" "$xiao_board/board-nRF52840.kicad_pcb"
"$kicad_cli" pcb export pos --format csv --side front --units mm --smd-only --exclude-fp-th --exclude-dnp --use-drill-file-origin --output "$xiao_out/front-smd.csv" "$xiao_board/board-nRF52840.kicad_pcb"
"$kicad_cli" pcb export pos --format csv --side back --units mm --smd-only --exclude-fp-th --exclude-dnp --use-drill-file-origin --output "$xiao_out/back-smd.csv" "$xiao_board/board-nRF52840.kicad_pcb"
```

After design changes, update the parts catalog, export a local purchasing BOM, package the fresh manufacturing files, and recompute the fabrication checksums.
