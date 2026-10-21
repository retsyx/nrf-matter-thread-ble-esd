#!/usr/bin/env python3
"""Audit the carrier PCBs' mechanical placement and right-angle routing.

This reads KiCad S-expressions directly, so it needs only Python's standard
library. It supplements KiCad DRC; it does not replace clearance, connectivity,
or manufacturing checks. Run without arguments to check both carrier boards.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import sys


POSITION_TOLERANCE = 0.00001  # mm; KiCad stores positions to much finer precision.
ANGLE_TOLERANCE = math.radians(0.01)
MAX_SUPPORT_EDGE_INSET = 4.0  # mm; corner supports remain near every board edge.
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BOARDS = (
    ROOT / "hw/board-nRF52840/board-nRF52840.kicad_pcb",
    ROOT / "hw/board-nRF54L15/board-nRF54L15.kicad_pcb",
)
TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+')
Point = tuple[float, float]


def parse_sexpr(source: str) -> list:
    """Parse one KiCad expression without depending on a KiCad installation."""
    stack: list[list] = [[]]
    for match in TOKEN.finditer(source):
        token = match.group()
        if token == "(":
            node: list = []
            stack[-1].append(node)
            stack.append(node)
        elif token == ")":
            if len(stack) == 1:
                raise ValueError("unexpected closing parenthesis")
            stack.pop()
        else:
            stack[-1].append(json.loads(token) if token.startswith('"') else token)
    if len(stack) != 1 or len(stack[0]) != 1 or not isinstance(stack[0][0], list):
        raise ValueError("expected one complete S-expression")
    return stack[0][0]


def children(node: list, key: str) -> list[list]:
    return [item for item in node[1:] if isinstance(item, list) and item and item[0] == key]


def child(node: list, key: str) -> list:
    matches = children(node, key)
    return matches[0] if matches else []


def scalar(node: list, key: str, default: str = "") -> str:
    item = child(node, key)
    return item[1] if len(item) > 1 else default


def xy(node: list, key: str) -> Point:
    item = child(node, key)
    if len(item) < 3:
        raise ValueError(f"missing {key} coordinates in {node[0]}")
    return float(item[1]), float(item[2])


def property_value(footprint: list, name: str) -> str:
    for prop in children(footprint, "property"):
        if prop[1] == name:
            return prop[2]
    # KiCad versions before properties represented references and values as text.
    for text in children(footprint, "fp_text"):
        if text[1].casefold() == name.casefold():
            return text[2]
    return ""


def subtract(a: Point, b: Point) -> Point:
    return a[0] - b[0], a[1] - b[1]


def cross(a: Point, b: Point) -> float:
    return a[0] * b[1] - a[1] * b[0]


def convex_hull(points: list[Point]) -> list[Point]:
    """Return the support polygon in counterclockwise order."""
    ordered = sorted(set(points))
    if len(ordered) < 3:
        return ordered
    lower: list[Point] = []
    upper: list[Point] = []
    for chain, sequence in ((lower, ordered), (upper, reversed(ordered))):
        for point in sequence:
            while len(chain) >= 2 and cross(subtract(chain[-1], chain[-2]),
                                           subtract(point, chain[-2])) <= POSITION_TOLERANCE ** 2:
                chain.pop()
            chain.append(point)
    return lower[:-1] + upper[:-1]


def strictly_inside_polygon(point: Point, polygon: list[Point]) -> bool:
    """A centered sensor must have support on every side, not lie on an edge."""
    if len(polygon) < 3:
        return False
    for start, end in zip(polygon, polygon[1:] + polygon[:1]):
        edge = subtract(end, start)
        if cross(edge, subtract(point, start)) <= POSITION_TOLERANCE * math.hypot(*edge):
            return False
    return True


def arc_extrema(start: Point, mid: Point, end: Point) -> list[Point]:
    """Return endpoints and the axis extrema traversed by a three-point arc."""
    ax, ay = start
    bx, by = mid
    cx, cy = end
    determinant = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(determinant) < 1e-12:
        raise ValueError("degenerate Edge.Cuts arc")
    aa, bb, cc = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ox = (aa * (by - cy) + bb * (cy - ay) + cc * (ay - by)) / determinant
    oy = (aa * (cx - bx) + bb * (ax - cx) + cc * (bx - ax)) / determinant
    radius = math.hypot(ax - ox, ay - oy)
    first = math.atan2(ay - oy, ax - ox)
    middle = math.atan2(by - oy, bx - ox)
    last = math.atan2(cy - oy, cx - ox)
    full_turn = 2 * math.pi
    ccw_sweep = (last - first) % full_turn
    ccw = (middle - first) % full_turn <= ccw_sweep
    points = [start, mid, end]
    for angle in (0, math.pi / 2, math.pi, 3 * math.pi / 2):
        included = ((angle - first) % full_turn <= ccw_sweep + 1e-12) if ccw else (
            (first - angle) % full_turn <= (first - last) % full_turn + 1e-12
        )
        if included:
            points.append((ox + radius * math.cos(angle), oy + radius * math.sin(angle)))
    return points


def outline_bounds(board: list) -> tuple[float, float, float, float]:
    points: list[Point] = []
    for shape in board[1:]:
        if not isinstance(shape, list) or scalar(shape, "layer") != "Edge.Cuts":
            continue
        kind = shape[0]
        if kind in ("gr_line", "gr_rect"):
            points.extend((xy(shape, "start"), xy(shape, "end")))
        elif kind == "gr_arc":
            points.extend(arc_extrema(xy(shape, "start"), xy(shape, "mid"), xy(shape, "end")))
        elif kind == "gr_circle":
            center, end = xy(shape, "center"), xy(shape, "end")
            radius = math.dist(center, end)
            points.extend(((center[0] - radius, center[1] - radius),
                           (center[0] + radius, center[1] + radius)))
        elif kind == "gr_poly":
            points.extend((float(p[1]), float(p[2])) for p in children(child(shape, "pts"), "xy"))
        else:
            raise ValueError(f"unsupported Edge.Cuts geometry: {kind}")
    if not points:
        raise ValueError("no board-level Edge.Cuts geometry found")
    return min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)


@dataclass(frozen=True)
class Segment:
    start: Point
    end: Point
    layer: str
    net: str
    identifier: str

    @property
    def vector(self) -> Point:
        return subtract(self.end, self.start)


def intersection(a: Segment, b: Segment) -> Point | None:
    """Intersect centerlines, including endpoints and endpoint/interior joins."""
    av, bv = a.vector, b.vector
    alen, blen = math.hypot(*av), math.hypot(*bv)
    determinant = cross(av, bv)
    if not alen or not blen or abs(determinant) < 1e-12 * alen * blen:
        return None
    offset = subtract(b.start, a.start)
    t, u = cross(offset, bv) / determinant, cross(offset, av) / determinant
    at, bt = POSITION_TOLERANCE / alen, POSITION_TOLERANCE / blen
    if -at <= t <= 1 + at and -bt <= u <= 1 + bt:
        return a.start[0] + t * av[0], a.start[1] + t * av[1]
    return None


def right_angles(segments: list[Segment]) -> list[tuple[Point, Segment, Segment]]:
    """Include turns, T joins, and crossings, even when situated at a pad/via.

    Traces on different nets/layers cannot form a copper trace join and are
    deliberately excluded. Width-only overlap is left to KiCad DRC.
    """
    groups: dict[tuple[str, str], list[Segment]] = defaultdict(list)
    for segment in segments:
        groups[segment.layer, segment.net].append(segment)
    failures = []
    for group in groups.values():
        for i, a in enumerate(group):
            av = a.vector
            alen = math.hypot(*av)
            for b in group[i + 1:]:
                bv = b.vector
                length_product = alen * math.hypot(*bv)
                if not length_product:
                    continue
                dot = av[0] * bv[0] + av[1] * bv[1]
                if abs(dot) > math.sin(ANGLE_TOLERANCE) * length_product:
                    continue
                point = intersection(a, b)
                if point is not None:
                    failures.append((point, a, b))
    return failures


def format_point(point: Point) -> str:
    return f"({point[0]:.6g}, {point[1]:.6g}) mm"


def audit(path: Path) -> list[str]:
    board = parse_sexpr(path.read_text())
    if not board or board[0] != "kicad_pcb":
        raise ValueError("not a KiCad PCB")
    problems = []
    x0, y0, x1, y1 = outline_bounds(board)
    center = (x0 + x1) / 2, (y0 + y1) / 2
    print(f"{path.name}: outline {x1 - x0:.6g} × {y1 - y0:.6g} mm; center {format_point(center)}")
    footprints = children(board, "footprint")
    modules = [f for f in footprints if property_value(f, "Reference") == "U1"]
    if len(modules) != 1:
        problems.append(f"expected one U1 XIAO module footprint, found {len(modules)}")
    else:
        module = modules[0]
        module_position = xy(module, "at")
        module_at = child(module, "at")
        orientation = float(module_at[3]) if len(module_at) > 3 else 0.0
        # Both XIAO footprints have their PCB's local X center at zero. At 90°,
        # their board Y center is the footprint's Y position; USB faces left.
        orientation_error = abs((orientation - 90 + 180) % 360 - 180)
        if orientation_error > math.degrees(ANGLE_TOLERANCE) or scalar(module, "layer") != "F.Cu":
            problems.append("U1 must be on F.Cu at 90 degrees to keep the USB connector facing left")
        if abs(module_position[1] - center[1]) > POSITION_TOLERANCE:
            problems.append(f"U1 PCB center Y={module_position[1]:.6g} mm must align with "
                            f"board center Y={center[1]:.6g} mm")
    accelerometers = [f for f in footprints if property_value(f, "Reference") == "U2"]
    if len(accelerometers) != 1:
        problems.append(f"expected one U2 footprint, found {len(accelerometers)}")
    else:
        accelerometer = accelerometers[0]
        position = xy(accelerometer, "at")
        if property_value(accelerometer, "Value") != "BMA400":
            problems.append(f"U2 value is {property_value(accelerometer, 'Value')!r}, expected BMA400")
        if math.dist(position, center) > POSITION_TOLERANCE:
            problems.append(f"U2 is at {format_point(position)}, expected board center {format_point(center)}")
        else:
            print(f"  U2 {property_value(accelerometer, 'Value')} centered at {format_point(position)}")

    holes = [f for f in footprints if "mountinghole" in (f[1] + " " + property_value(f, "Value")).casefold()]
    if len(holes) != 4:
        problems.append(f"expected four mounting-hole footprints, found {len(holes)}")
    references = set()
    for hole in holes:
        reference = property_value(hole, "Reference")
        if reference in references:
            problems.append(f"duplicate mounting-hole reference {reference!r}")
        references.add(reference)
        pads = children(hole, "pad")
        drills = [child(p, "drill") for p in pads if len(p) > 2 and p[2] == "np_thru_hole"]
        valid_drill = len(drills) == 1 and len(drills[0]) == 2 and abs(float(drills[0][1]) - 2.2) <= POSITION_TOLERANCE
        if not valid_drill:
            problems.append(f"{reference}: expected one round 2.2 mm non-plated through-hole drill, found {drills!r}")
        print(f"  Mount {reference}: {format_point(xy(hole, 'at'))}; drills {drills!r}")

    if len(holes) == 4:
        mounting_centers = [xy(hole, "at") for hole in holes]
        support = convex_hull(mounting_centers)
        if len(support) != 4:
            problems.append("the four mounting centers must form a nondegenerate support quadrilateral")
        if not strictly_inside_polygon(center, support):
            problems.append(f"board/BMA400 center {format_point(center)} lies outside or on the edge "
                            "of the mounting support polygon; holes must oppose each other across the board")
        if len(accelerometers) == 1 and not strictly_inside_polygon(xy(accelerometers[0], "at"), support):
            if math.dist(xy(accelerometers[0], "at"), center) > POSITION_TOLERANCE:
                problems.append("the BMA400 lies outside or on the edge of the mounting support polygon")
        edge_insets = {
            "left": min(p[0] for p in mounting_centers) - x0,
            "right": x1 - max(p[0] for p in mounting_centers),
            "top": min(p[1] for p in mounting_centers) - y0,
            "bottom": y1 - max(p[1] for p in mounting_centers),
        }
        for edge, inset in edge_insets.items():
            if inset > MAX_SUPPORT_EDGE_INSET + POSITION_TOLERANCE:
                problems.append(f"nearest mounting center is {inset:.6g} mm from the {edge} board edge; "
                                f"opposing supports must lie within {MAX_SUPPORT_EDGE_INSET:.6g} mm "
                                "of every board edge")
            elif inset < -POSITION_TOLERANCE:
                problems.append(f"mounting support extends {-inset:.6g} mm beyond the {edge} board edge")

    # Current KiCad uses net names directly; older boards use numeric net IDs.
    copper_arcs = children(board, "arc")
    if copper_arcs:
        problems.append(f"{len(copper_arcs)} copper arc(s) need a tangent-junction audit; "
                        "this checker currently supports straight copper segments")
    net_names = {n[1]: n[2] for n in children(board, "net") if len(n) > 2}
    segments = [Segment(xy(s, "start"), xy(s, "end"), scalar(s, "layer"),
                        net_names.get(scalar(s, "net"), scalar(s, "net")),
                        scalar(s, "uuid", scalar(s, "tstamp", f"segment-{i}")))
                for i, s in enumerate(children(board, "segment"), 1)]
    bends = right_angles(segments)
    for point, a, b in bends:
        problems.append(f"right-angle copper join at {format_point(point)}, {a.layer}, net {a.net!r}; "
                        f"segments {a.identifier} and {b.identifier}")
    print(f"  {len(segments)} copper segments; {len(bends)} perpendicular same-net/layer joins "
          "(including pad/via junctions)")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("boards", nargs="*", type=Path, help="KiCad board paths; defaults to both carrier designs")
    args = parser.parse_args()
    failures = 0
    for path in args.boards or DEFAULT_BOARDS:
        try:
            problems = audit(path)
        except (OSError, ValueError, IndexError, TypeError) as exc:
            problems = [f"could not audit: {exc}"]
        for problem in problems:
            print(f"  FAIL: {problem}", file=sys.stderr)
        failures += len(problems)
        print(f"  {'FAIL' if problems else 'PASS'}: {path.name}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
