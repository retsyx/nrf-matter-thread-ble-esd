# XIAO nRF54L15 direct-SMD carrier

Standalone **22 × 38 mm, two-layer** KiCad project for the standard XIAO nRF54L15. U1 solders directly on fourteen edge lands plus its underside battery pair, **31 BAT+ / 32 GND**. Use the matching `nrf54l15` firmware build. The other MCU needs its own carrier.

- Editable [project](board-nRF54L15.kicad_pro), [schematic](board-nRF54L15.kicad_sch), [routed PCB](board-nRF54L15.kicad_pcb) and [custom design rules](board-nRF54L15.kicad_dru).
- Purchase from the [parts catalog](parts.json); assemble two front SMD placements (U1/SW1), six back SMD placements (U2/R1/R2/C1–C3), and manual through-hole J1. The local purchasing BOM export (`bom.csv`) is ignored by Git.
- Generate Gerbers, PTH/NPTH drill files and maps, and front and back SMD placement data using the commands below. Local fabrication packages, BOM exports and checksums under `fabrication/` are ignored by Git.
- Review contact positions, copper keepouts and probe access in [geometry](geometry.json). Local [footprints](Board.pretty/), [symbols](board.kicad_sym), [datasheets](datasheets/) and [STEP models](3dmodels/) accompany the project.

H1/H2/H7/H8 are 2.2 mm M2 mounting holes. H1/H2 sit at (52,85)/(68,85) mm; the added H7/H8 pair sits at the opposite end, (52,53)/(68,53) mm. The four corners provide a 16 × 32 mm mounting span, supporting both board ends; 2.45 mm screw-head clearances are reserved on both copper layers. H3–H6 provide mask-only NPTH access to module SWDIO/SWCLK/RESET/GND; backside TP1 is 3V3 reference and TP2 GND. These features add no purchased parts. J1 is **pin 1 GND, pin 2 VBAT**; verify battery plug polarity.

**SW1 factory reset:** hold the top-actuated button for 5 seconds to erase Matter/Thread user state and reboot ready for pairing with the same provisioned Matter credentials. SW1 connects D0 (P1.04) to GND and uses the firmware internal pull-up; its released contact is open. It sits in the upper-center carrier area at (60,55.5) mm, between H7/H8 and above U1, outside the antenna keepout. The module RESET probe contact is separate and only resets the MCU. See [switch datasheet](datasheets/omron-b3u.pdf) and [geometry](geometry.json) for the footprint and placement.

The locally generated module STEP shows only the official bare PCB core, with a nominal 0.1 mm solder gap. Module electronics, antenna and USB solids are omitted. Regenerate this model after a fresh checkout using the [model provenance and reproduction notes](3dmodels/README.md).

Revision C reduces the carrier from 30 × 42 mm to 22 × 38 mm, about 34% less area. U2 BMA400 sits at (60,69) mm, the carrier geometric center. U1 moves to (59.5,69) mm and rotates 90° to put USB at the left edge; its Y position is centered along the axis perpendicular to USB approach. U2 and its passives move to the back beneath U1, clear of the antenna and probe access. H7/H8 add mounting holes at the opposite end from H1/H2. The rerouted copper has no right-angle joins, including T joins and joins at pads or vias. Probe holes and all module keepouts move with U1; the BMA400 central keepout protects both copper layers. KiCad 10.0.6 DRC with schematic parity and the independent [layout audit](../../scripts/check-pcb-layout.py) pass with zero violations, zero unconnected items and zero schematic-parity issues on 2026-10-20.

Open the project in KiCad 10.0.6. The editable design has not been fabricated or assembled. The custom rules allow the intentional H3–H6 probe holes inside U1's front courtyard; other clearance checks remain active.

Use KiCad's schematic and PCB editors for design changes. The installed [KiCad CLI](https://docs.kicad.org/10.0/en/cli/cli.html) can check and export the current files. Run these commands from the repository root; fresh output goes to a separate temporary directory.

```sh
kicad_cli=/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli
xiao_board=hw/board-nRF54L15
xiao_out=/tmp/xiao-nrf54l15-export
mkdir -p "$xiao_out"
"$kicad_cli" sch erc --severity-all --exit-code-violations --output "$xiao_out/erc.rpt" "$xiao_board/board-nRF54L15.kicad_sch"
"$kicad_cli" pcb drc --severity-all --schematic-parity --exit-code-violations --output "$xiao_out/drc.rpt" "$xiao_board/board-nRF54L15.kicad_pcb"
python3 scripts/check-pcb-layout.py "$xiao_board/board-nRF54L15.kicad_pcb"
```

Review any ERC/DRC violations before exporting manufacturing files. In the same shell:

```sh
"$kicad_cli" sch export pdf --output "$xiao_out/schematic.pdf" "$xiao_board/board-nRF54L15.kicad_sch"
"$kicad_cli" pcb export gerbers --layers "F.Cu,B.Cu,F.Mask,B.Mask,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,Edge.Cuts" --no-protel-ext --use-drill-file-origin --subtract-soldermask --output "$xiao_out/gerbers/" "$xiao_board/board-nRF54L15.kicad_pcb"
"$kicad_cli" pcb export drill --format excellon --drill-origin plot --excellon-separate-th --generate-map --generate-report --output "$xiao_out/gerbers/" "$xiao_board/board-nRF54L15.kicad_pcb"
"$kicad_cli" pcb export pos --format csv --side front --units mm --smd-only --exclude-fp-th --exclude-dnp --use-drill-file-origin --output "$xiao_out/front-smd.csv" "$xiao_board/board-nRF54L15.kicad_pcb"
"$kicad_cli" pcb export pos --format csv --side back --units mm --smd-only --exclude-fp-th --exclude-dnp --use-drill-file-origin --output "$xiao_out/back-smd.csv" "$xiao_board/board-nRF54L15.kicad_pcb"
```

After design changes, update the parts catalog, export a local purchasing BOM, package the fresh manufacturing files, and recompute the fabrication checksums.
