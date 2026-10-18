# Seeed Studio XIAO nRF52840 bare PCB model

The module STEP is a local generated export and is ignored by Git. The other
component STEP files in this directory are vendor library assets kept with the design.

`XIAO_nRF52840_bare_vendor_PCB.step` is a native KiCad STEP export of the official standard-module PCB linked in `../geometry.json`. The actual vendor outline and drilled holes are preserved; it includes **no component, antenna, USB, switch or shield solids**. No Sense assembly is substituted.

KiCad exports the dielectric core as 1.51 mm, excluding outer copper and mask. The vendor PCB stack is nominally 1.6 mm. The carrier footprint positions the board body at a nominal 0.1 mm solder gap. This visual gap is not a qualified reflow stand-off or a physical flatness measurement.

Reproduction uses installed KiCad 10.0.6: rotate all source PCB footprints, tracks, drawings and zones by `pcbnew.EDA_ANGLE(90, pcbnew.DEGREES_T)` around `module.source_header_origin_mm`, clear footprint 3D models, save a temporary copy, then export with `kicad-cli pcb export step --board-only --user-origin <originX>x<originY>mm --output <file> <temporary-PCB>`. This normalizes USB upward in PCB coordinates. The STEP origin is the physical header-grid center. Source and model SHA-256 hashes are recorded in `../geometry.json`.

`SW_SPST_B3U-1000P.step` is copied unchanged from the installed KiCad 10.0.6
`Button_Switch_SMD.3dshapes` library; the matching local footprint comes from
`Button_Switch_SMD.pretty`. This is a KiCad library model, not a measured
assembly model. Its SHA-256 is recorded in `../geometry.json` under
`factory_reset_button.model`; it uses zero offset/rotation and unit scale.
The switch land pattern was checked against the manufacturer B3U datasheet
A162-E1-07 page 2, saved at `../datasheets/omron-b3u.pdf`. The local footprint is adapted to
the manufacturer nominal dimensions: two 0.8 × 1.7 mm SMD lands centered
at x = ±1.7 mm (4.2 mm outer span and 2.6 mm inner gap).
