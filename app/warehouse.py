"""Canonical shelving layout for the parts warehouse(s) -- worked out
against a real CubiCasa building scan and the shelving units' actual
dimensions (25in deep x 6ft long x 6ft tall, 5 shelves each).

Rooms are keyed by an id (today, just "main") so a second storage room/
building can be added later without touching existing data: each room owns
its own aisle layout AND the geometry (wall outline, doors, shelf-bank
rectangles) used to render its floor plan -- see app/floorplan.py.

This module is the single source of truth for which Aisle/Bank/Shelf
combinations are valid in a given room, used both to drive the location
picker on the item form and to compose/parse the two location
representations stored on a catalog item:
  - location       -- human-readable, e.g. "Aisle 05, Bank C, Shelf 4"
  - location_code  -- machine-sortable, e.g. "05-C-4"

location_code always carries the room, e.g. "main-05-C-4" or
"annex-05-C-4" -- one uniform, unambiguous format regardless of how many
rooms exist.
"""

import re

DEFAULT_ROOM = "gm_down"

SHELVES_PER_BANK = 5  # every unit is 6ft tall with 5 shelves, regardless of room/aisle

# --- Room definitions ------------------------------------------------------
#
# "structures" is each physical shelving run in the room: which aisle
# name(s) label its face(s) (a wall-mounted run has one; a run you can
# approach from both sides has two, keyed by which side W/E/N/S they're
# named from), its footprint in meters (x0,x1,y0,y1), how many banks long
# it is, and which way the banks run (NS = north-south, lettered A at the
# north end; EW = east-west, lettered A at the west end). Bank counts here
# are the single source of truth -- the picker and the floor-plan renderer
# both derive from this, so they can never drift apart.
#
# "building" is the room's wall outline (a polygon, meters, walked
# clockwise from the NW corner) and its door openings, for the floor-plan
# renderer -- see app/floorplan.py.

ROOMS = {
    "gm_down": {
        "label": "GM-Down",
        "building": {
            # Walked clockwise from the NW corner; wall_labels[i] names the
            # edge from outline[i] to outline[i+1], for matching doors to
            # the wall they're cut into. A door's start/end is measured in
            # meters along that edge, from outline[i] (so along the room's
            # own x for a horizontal wall, y for a vertical one).
            "outline": [(0.0, 0.0), (4.02, 0.0), (4.02, 5.08), (6.78, 5.08), (6.78, 14.00), (0.0, 14.00)],
            "wall_labels": ["N", "innerE", "step", "E", "S", "W"],
            "doors": [
                {"wall": "N", "start": 1.0, "end": 2.6, "label": "Main entry"},
                {"wall": "W", "start": 3.8, "end": 5.0, "label": "Personnel"},
                {"wall": "innerE", "start": 3.4, "end": 4.6, "label": "Loading"},
            ],
        },
        "structures": [
            {"aisles": {"E": "01"}, "x": (0.0, 0.635), "y": (4.856, 14.0), "banks": 5, "dir": "NS"},
            {"aisles": {"S": "09"}, "x": (4.9512, 6.78), "y": (5.08, 5.715), "banks": 1, "dir": "EW"},
            {"aisles": {"N": "10"}, "x": (1.2936, 6.78), "y": (13.365, 14.0), "banks": 3, "dir": "EW"},
            # label_pos overrides the renderer's default "north of a NS
            # run" placement -- Aisle 08 sits almost flush under Aisle 09
            # (about 9in of clearance), too tight for a label above it.
            {"aisles": {"W": "08"}, "x": (6.145, 6.78), "y": (5.9482, 13.2634), "banks": 4, "dir": "NS",
             "label_pos": "W"},
            {"aisles": {"W": "02"}, "x": (1.3775, 2.0125), "y": (5.08, 12.3952), "banks": 4, "dir": "NS"},
            {"aisles": {"E": "03"}, "x": (2.0125, 2.6475), "y": (5.08, 12.3952), "banks": 4, "dir": "NS"},
            {"aisles": {"W": "04", "E": "05"}, "x": (3.39, 4.025), "y": (6.28, 11.7664), "banks": 3, "dir": "NS"},
            {"aisles": {"W": "06", "E": "07"}, "x": (4.7675, 5.4025), "y": (6.28, 11.7664), "banks": 3, "dir": "NS"},
        ],
    },
}

_CODE_RE = re.compile(r"^([a-z0-9_]+)-(\d{2})-([A-E])-([1-5])$")


def room_ids():
    return list(ROOMS.keys())


def room_options():
    """[{'id': 'main', 'label': 'Main Warehouse'}, ...] for a future room picker."""
    return [{"id": rid, "label": ROOMS[rid]["label"]} for rid in room_ids()]


def get_room(room=DEFAULT_ROOM):
    return ROOMS.get(room)


def _aisles_for_room(room):
    """Derive {aisle_number: {'banks': n, 'dir': 'NS'|'EW'}} from a room's
    structures, so aisle metadata always matches what's actually drawn."""
    info = get_room(room)
    if not info:
        return {}
    aisles = {}
    for st in info["structures"]:
        for aisle_number in st["aisles"].values():
            aisles[aisle_number] = {"banks": st["banks"], "dir": st["dir"]}
    return aisles


def aisle_numbers(room=DEFAULT_ROOM):
    """Aisle numbers in order for a room, e.g. ['01', '02', ..., '10']."""
    return sorted(_aisles_for_room(room).keys())


def banks_for_aisle(aisle, room=DEFAULT_ROOM):
    """Valid bank letters for an aisle, e.g. '02' -> ['A', 'B', 'C', 'D']."""
    info = _aisles_for_room(room).get(aisle)
    if not info:
        return []
    return [chr(ord("A") + i) for i in range(info["banks"])]


def shelves():
    return list(range(1, SHELVES_PER_BANK + 1))


def is_valid(aisle, bank, shelf, room=DEFAULT_ROOM):
    try:
        shelf = int(shelf)
    except (TypeError, ValueError):
        return False
    return room in ROOMS and bank in banks_for_aisle(aisle, room) and shelf in shelves()


def compose(aisle, bank, shelf, room=DEFAULT_ROOM):
    """(aisle, bank, shelf[, room]) -> (human_readable, code). Raises
    ValueError if the combination isn't a real spot in that room."""
    if not is_valid(aisle, bank, shelf, room):
        raise ValueError(f"Not a valid warehouse location: {room}:{aisle}-{bank}-{shelf}")
    shelf = int(shelf)
    code = f"{room}-{aisle}-{bank}-{shelf}"
    if room == DEFAULT_ROOM and len(ROOMS) == 1:
        # Only one room exists -- naming it in the human-readable string
        # would just be noise. Once a second room is registered, every
        # room (including this one) starts including its label.
        human = f"Aisle {aisle}, Bank {bank}, Shelf {shelf}"
    else:
        human = f"{ROOMS[room]['label']} — Aisle {aisle}, Bank {bank}, Shelf {shelf}"
    return human, code


def parse_code(code):
    """'main-05-C-4' -> ('main', '05', 'C', 4). Returns None if it isn't a
    well-formed, currently-valid warehouse location code (checked against
    that room's real layout, so a code from a since-changed layout safely
    stops resolving rather than highlighting the wrong shelf)."""
    if not code:
        return None
    m = _CODE_RE.match(code.strip())
    if not m:
        return None
    room, aisle, bank, shelf = m.group(1), m.group(2), m.group(3), int(m.group(4))
    if not is_valid(aisle, bank, shelf, room):
        return None
    return room, aisle, bank, shelf


def aisle_options(room=DEFAULT_ROOM):
    """[{'number': '01', 'banks': ['A', ..], 'dir': 'NS'}, ...] for templates."""
    aisles = _aisles_for_room(room)
    return [
        {"number": n, "banks": banks_for_aisle(n, room), "dir": aisles[n]["dir"]}
        for n in sorted(aisles.keys())
    ]
