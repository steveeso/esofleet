"""Renders a room's floor plan (and a shelving unit's side elevation) as
inline SVG, with one bank/shelf optionally highlighted -- used on an item's
detail page to show where it lives. Pure string building against the
layout data in app/warehouse.py: no image libraries, no filesystem or
network access, nothing to cache -- generating one of these costs
microseconds, about the same as rendering any other template fragment.

Colors and type are the app's own CSS custom properties (--color-*,
--font-ui, defined in static/css/style.css), so the SVG follows the page's
palette and typeface rather than carrying its own.

Design intent: every bank/aisle in the room is drawn, but only one is ever
the reason someone opened this view. Everything that isn't it is pushed
down in size, weight and opacity so it reads as context/structure rather
than competing information -- the eye should land on the highlighted bank
in well under a second, not have to search a grid of equally-loud labels.
"""

from . import warehouse

# meters -> px, and canvas margin around the building outline
_SCALE = 46
_MARGIN = 32

_MUTED_OPACITY = "0.55"


def _project(building, scale=_SCALE, margin=_MARGIN):
    """Returns (to_px(x, y), width, height) for a room's outline, with the
    outline's own min x/y mapped to (margin, margin)."""
    xs = [p[0] for p in building["outline"]]
    ys = [p[1] for p in building["outline"]]
    x0, y0 = min(xs), min(ys)
    width = (max(xs) - x0) * scale + margin * 2
    height = (max(ys) - y0) * scale + margin * 2

    def to_px(x, y):
        return margin + (x - x0) * scale, margin + (y - y0) * scale

    return to_px, width, height


def _wall_segments(building):
    """The building's wall outline as drawable line segments, with each
    door's span cut out as a gap. Door start/end are absolute coordinates
    (room x for a horizontal wall, room y for a vertical one), not measured
    relative to the edge's own walk direction, so this works regardless of
    which way the outline winds."""
    outline = building["outline"]
    labels = building["wall_labels"]
    doors = building["doors"]
    n = len(outline)
    segments = []

    for i in range(n):
        p0, p1 = outline[i], outline[(i + 1) % n]
        label = labels[i]
        horizontal = p0[1] == p1[1]
        if horizontal:
            fixed = p0[1]
            lo, hi = sorted((p0[0], p1[0]))
        else:
            fixed = p0[0]
            lo, hi = sorted((p0[1], p1[1]))

        gaps = sorted((d["start"], d["end"]) for d in doors if d["wall"] == label)
        cur = lo
        for gstart, gend in gaps:
            gstart, gend = max(gstart, lo), min(gend, hi)
            if gstart > cur:
                segments.append(_span(horizontal, fixed, cur, gstart))
            cur = max(cur, gend)
        if cur < hi:
            segments.append(_span(horizontal, fixed, cur, hi))

    return segments


def _span(horizontal, fixed, a, b):
    if horizontal:
        return (a, fixed), (b, fixed)
    return (fixed, a), (fixed, b)


def _door_gaps(building):
    """Door spans as ((x1,y1),(x2,y2)) midline segments, for the dashed
    'opening' marker and label -- not the wall geometry itself."""
    outline = building["outline"]
    labels = building["wall_labels"]
    by_label = {}
    for i in range(len(outline)):
        p0, p1 = outline[i], outline[(i + 1) % len(outline)]
        by_label[labels[i]] = (p0, p1)

    gaps = []
    for d in building["doors"]:
        p0, p1 = by_label[d["wall"]]
        horizontal = p0[1] == p1[1]
        gaps.append((_span(horizontal, p0[1] if horizontal else p0[0], d["start"], d["end"]), d["label"]))
    return gaps


def render_floor_plan(room=warehouse.DEFAULT_ROOM, highlight=None, uid="a"):
    """highlight: an (aisle, bank) tuple to pick out, or None. Returns a
    standalone <svg>...</svg> string, safe to drop straight into a
    template with |safe (it's built entirely from this module's own
    trusted layout data, never from user input).

    uid: a short, page-unique suffix for this call's internal <defs> ids.
    A page that embeds the same rendered diagram more than once (e.g. a
    small thumbnail plus a larger copy in an expand-on-click dialog) must
    give each render its own uid, or their <filter id="..."> definitions
    collide as duplicate ids in the same HTML document."""
    info = warehouse.get_room(room)
    if not info:
        return ""
    building = info["building"]
    to_px, width, height = _project(building)
    target_aisle, target_bank = highlight if highlight else (None, None)
    shadow_id = f"locHitShadow-{uid}"

    parts = [f'<svg viewBox="0 0 {width:.0f} {height:.0f}" width="{width:.0f}" height="{height:.0f}" '
             f'xmlns="http://www.w3.org/2000/svg" '
             f'style="width: 100%; height: auto; display: block; font-family: var(--font-ui);" '
             f'role="img" aria-label="Floor plan of {info["label"]}'
             f'{f", Aisle {target_aisle} Bank {target_bank} highlighted" if target_aisle else ""}.">']

    parts.append(f'<defs><filter id="{shadow_id}" x="-60%" y="-60%" width="220%" height="220%">'
                 f'<feDropShadow dx="0" dy="0" stdDeviation="1.4" flood-color="var(--color-amber)" flood-opacity="0.55"/>'
                 f'</filter></defs>')

    parts.append('<rect x="0" y="0" width="100%" height="100%" fill="var(--color-panel)"/>')

    # floor fill (the outline polygon)
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (to_px(*p) for p in building["outline"]))
    parts.append(f'<polygon points="{pts}" fill="var(--color-bg)" stroke="none"/>')

    # shelving structures
    letters = "ABCDE"
    stripe = 5  # px -- fixed thickness for the facing-edge highlight bar
    for st in info["structures"]:
        (x0, y0), (x1, y1) = to_px(st["x"][0], st["y"][0]), to_px(st["x"][1], st["y"][1])
        w, h = x1 - x0, y1 - y0
        n = st["banks"]
        # Which face of this structure (if any) is the one being pointed
        # to -- a wall-mounted run only has one, but a run named from both
        # sides (e.g. Aisle 04/05 sharing one physical row) needs to know
        # *which* side's aisle number matched, so only that edge lights up
        # rather than the whole cell -- otherwise there's no way to tell
        # whether to approach from Aisle 04's side or Aisle 05's.
        hit_side = next((side for side, a in st["aisles"].items() if a == target_aisle), None)

        if st["dir"] == "NS":
            step = h / n
            for i in range(n):
                cy0 = y0 + i * step
                hit = hit_side is not None and letters[i] == target_bank
                if hit:
                    parts.append(f'<rect x="{x0:.1f}" y="{cy0:.1f}" width="{w:.1f}" height="{step:.1f}" rx="2" '
                                 f'fill="var(--color-amber-bg)" stroke="var(--color-amber)" stroke-width="1"/>')
                    sx = x0 if hit_side == "W" else x1 - stripe
                    parts.append(f'<rect x="{sx:.1f}" y="{cy0:.1f}" width="{stripe}" height="{step:.1f}" rx="1.5" '
                                 f'fill="var(--color-amber)" filter="url(#{shadow_id})"/>')
                else:
                    parts.append(f'<rect x="{x0:.1f}" y="{cy0:.1f}" width="{w:.1f}" height="{step:.1f}" rx="2" '
                                 f'fill="var(--color-border)" fill-opacity="0.6" '
                                 f'stroke="var(--color-ink-muted)" stroke-opacity="0.35" stroke-width="1"/>')
                fs = 15 if hit else min(9, step * 0.4)
                tcolor = "var(--color-ink)" if hit else "var(--color-ink-muted)"
                opacity = "1" if hit else _MUTED_OPACITY
                parts.append(f'<text x="{x0 + w / 2:.1f}" y="{cy0 + step / 2 + fs * 0.35:.1f}" '
                             f'font-size="{fs:.1f}" fill="{tcolor}" fill-opacity="{opacity}" text-anchor="middle" '
                             f'font-weight="{"700" if hit else "400"}">{letters[i]}</text>')
        else:
            step = w / n
            for i in range(n):
                cx0 = x0 + i * step
                hit = hit_side is not None and letters[i] == target_bank
                if hit:
                    parts.append(f'<rect x="{cx0:.1f}" y="{y0:.1f}" width="{step:.1f}" height="{h:.1f}" rx="2" '
                                 f'fill="var(--color-amber-bg)" stroke="var(--color-amber)" stroke-width="1"/>')
                    sy = y0 if hit_side == "N" else y1 - stripe
                    parts.append(f'<rect x="{cx0:.1f}" y="{sy:.1f}" width="{step:.1f}" height="{stripe}" rx="1.5" '
                                 f'fill="var(--color-amber)" filter="url(#{shadow_id})"/>')
                else:
                    parts.append(f'<rect x="{cx0:.1f}" y="{y0:.1f}" width="{step:.1f}" height="{h:.1f}" rx="2" '
                                 f'fill="var(--color-border)" fill-opacity="0.6" '
                                 f'stroke="var(--color-ink-muted)" stroke-opacity="0.35" stroke-width="1"/>')
                fs = 15 if hit else min(9, step * 0.4, h * 0.4)
                tcolor = "var(--color-ink)" if hit else "var(--color-ink-muted)"
                opacity = "1" if hit else _MUTED_OPACITY
                parts.append(f'<text x="{cx0 + step / 2:.1f}" y="{y0 + h / 2 + fs * 0.35:.1f}" '
                             f'font-size="{fs:.1f}" fill="{tcolor}" fill-opacity="{opacity}" text-anchor="middle" '
                             f'font-weight="{"700" if hit else "400"}">{letters[i]}</text>')

        # Aisle-number labels: plain horizontal text (rotated text crammed
        # into a narrow gap reads as noise), placed north of a north-south
        # run or west of an east-west one by default. The matching aisle
        # gets a rounded "chip" behind it so it reads as a callout rather
        # than one more line in a grid of gray labels; everything else
        # stays small and faint.
        def _label(aisle_num, x, y, anchor):
            label_hit = aisle_num == target_aisle
            if label_hit:
                cw, ch = 20, 15
                cx = x - cw / 2 if anchor == "middle" else (x if anchor == "start" else x - cw)
                parts.append(f'<rect x="{cx:.1f}" y="{y - ch + 4:.1f}" width="{cw}" height="{ch}" rx="4" '
                             f'fill="var(--color-amber-bg)" stroke="var(--color-amber)" stroke-width="1"/>')
                parts.append(f'<text x="{(cx + cw / 2):.1f}" y="{y:.1f}" font-size="12" fill="var(--color-amber)" '
                             f'font-weight="700" text-anchor="middle">{aisle_num}</text>')
            else:
                parts.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="9.5" fill="var(--color-ink-muted)" '
                             f'fill-opacity="{_MUTED_OPACITY}" font-weight="400" text-anchor="{anchor}">{aisle_num}</text>')

        # Default label placement is north of a north-south run or west of
        # an east-west one; a structure can override this (label_pos) when
        # its real-world neighbors leave no room -- see Aisle 08.
        pad = 11
        label_pos = st.get("label_pos", "N" if st["dir"] == "NS" else "W")

        if label_pos in ("N", "S"):
            ly = (y0 - pad) if label_pos == "N" else (y1 + pad + 10)
            if len(st["aisles"]) == 1:
                (_, aisle_num), = st["aisles"].items()
                _label(aisle_num, (x0 + x1) / 2, ly, "middle")
            else:
                cx, gap = (x0 + x1) / 2, 5
                if "W" in st["aisles"]:
                    _label(st["aisles"]["W"], cx - gap, ly, "end")
                if "E" in st["aisles"]:
                    _label(st["aisles"]["E"], cx + gap, ly, "start")
        else:
            lx = (x0 - pad) if label_pos == "W" else (x1 + pad)
            anchor = "end" if label_pos == "W" else "start"
            if len(st["aisles"]) == 1:
                (_, aisle_num), = st["aisles"].items()
                fs = 10
                _label(aisle_num, lx, (y0 + y1) / 2 + fs * 0.35, anchor)
            else:
                cy = (y0 + y1) / 2
                if "N" in st["aisles"]:
                    _label(st["aisles"]["N"], lx, cy - 4, anchor)
                if "S" in st["aisles"]:
                    _label(st["aisles"]["S"], lx, cy + 12, anchor)

    # door openings (dashed marker across the gap)
    for (a, b), label in _door_gaps(building):
        (ax, ay), (bx, by) = to_px(*a), to_px(*b)
        parts.append(f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" '
                     f'stroke="var(--color-primary)" stroke-opacity="0.6" stroke-width="1.5" stroke-dasharray="3 3"/>')

    # walls, drawn over the door gaps/floor fill
    for (ax, ay), (bx, by) in _wall_segments(building):
        (px0, py0), (px1, py1) = to_px(ax, ay), to_px(bx, by)
        parts.append(f'<line x1="{px0:.1f}" y1="{py0:.1f}" x2="{px1:.1f}" y2="{py1:.1f}" '
                     f'stroke="var(--color-ink)" stroke-width="3" stroke-linecap="square"/>')

    parts.append("</svg>")
    return "".join(parts)


def render_shelf_elevation(shelf, num_shelves=warehouse.DEFAULT_SHELVES_PER_BANK, uid="a"):
    """Front (face-on) elevation of a boltless shelving unit -- steel frame
    posts, top/bottom rails, and num_shelves particleboard-style shelf
    boards -- with the compartment above the target shelf's board tinted
    and that board highlighted. shelf 1 is the bottom board, shelf
    num_shelves the top one, matching how the physical units are numbered.
    Not room/aisle-specific beyond the shelf count -- every unit is built
    the same way structurally -- so this takes num_shelves rather than a
    room or aisle (callers look that count up per-aisle via
    warehouse.shelf_count()).

    uid: see render_floor_plan's docstring -- give each embed of this on
    the same page its own uid so their <defs> ids don't collide."""
    shelf = int(shelf)
    num_shelves = max(1, int(num_shelves))
    width, height, margin = 170, 230, 14
    post_w, rail_h = 7, 7
    shadow_id = f"locHitShadow-{uid}"

    frame_left, frame_right = margin, width - margin
    frame_top, frame_bottom = margin, height - margin
    usable_top, usable_bottom = frame_top + rail_h, frame_bottom - rail_h

    # board_y[k] (k = 1..num_shelves) is that shelf's board, shelf 1 at
    # usable_bottom (the floor of the storage area) and shelf num_shelves
    # at usable_top -- evenly spaced between the two.
    if num_shelves == 1:
        board_y = {1: (usable_top + usable_bottom) / 2}
    else:
        step = (usable_bottom - usable_top) / (num_shelves - 1)
        board_y = {k: usable_bottom - (k - 1) * step for k in range(1, num_shelves + 1)}

    parts = [f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
             f'xmlns="http://www.w3.org/2000/svg" '
             f'style="width: 100%; height: auto; display: block; font-family: var(--font-ui);" '
             f'role="img" aria-label="Front view of a {num_shelves}-shelf shelving unit, '
             f'Shelf {shelf} of {num_shelves} highlighted, shelf 1 at the floor.">']
    parts.append(f'<defs><filter id="{shadow_id}" x="-60%" y="-60%" width="220%" height="220%">'
                 f'<feDropShadow dx="0" dy="0" stdDeviation="1.4" flood-color="var(--color-amber)" flood-opacity="0.55"/>'
                 f'</filter></defs>')
    parts.append('<rect x="0" y="0" width="100%" height="100%" fill="var(--color-panel)"/>')

    # highlighted compartment: the open space above the target board, up
    # to the next board (or the top rail, for the topmost shelf) -- this
    # is where items on that shelf actually sit, not the board itself.
    if shelf in board_y:
        comp_bottom = board_y[shelf]
        comp_top = board_y.get(shelf + 1, usable_top)
        parts.append(f'<rect x="{frame_left:.1f}" y="{comp_top:.1f}" width="{frame_right - frame_left:.1f}" '
                     f'height="{comp_bottom - comp_top:.1f}" fill="var(--color-amber-bg)"/>')

    # frame: posts + top/bottom rails (drawn as one steel-toned group)
    parts.append('<g fill="var(--color-ink-muted)">')
    parts.append(f'<rect x="{frame_left:.1f}" y="{frame_top:.1f}" width="{post_w}" height="{frame_bottom - frame_top:.1f}"/>')
    parts.append(f'<rect x="{frame_right - post_w:.1f}" y="{frame_top:.1f}" width="{post_w}" height="{frame_bottom - frame_top:.1f}"/>')
    parts.append(f'<rect x="{frame_left:.1f}" y="{frame_top:.1f}" width="{frame_right - frame_left:.1f}" height="{rail_h}"/>')
    parts.append(f'<rect x="{frame_left:.1f}" y="{frame_bottom - rail_h:.1f}" width="{frame_right - frame_left:.1f}" height="{rail_h}"/>')
    parts.append('</g>')

    # punched-slot texture on the posts, purely decorative
    parts.append('<g fill="var(--color-panel)" fill-opacity="0.6">')
    y = usable_top + 6
    while y < usable_bottom - 6:
        parts.append(f'<circle cx="{frame_left + post_w / 2:.1f}" cy="{y:.1f}" r="1.1"/>')
        parts.append(f'<circle cx="{frame_right - post_w / 2:.1f}" cy="{y:.1f}" r="1.1"/>')
        y += 9
    parts.append('</g>')

    # shelf boards + labels
    for k in range(1, num_shelves + 1):
        y = board_y[k]
        hit = k == shelf
        board_h = 5
        if hit:
            parts.append(f'<rect x="{frame_left:.1f}" y="{y - board_h / 2:.1f}" width="{frame_right - frame_left:.1f}" '
                         f'height="{board_h}" rx="1" fill="var(--color-amber)" filter="url(#{shadow_id})"/>')
        else:
            parts.append(f'<rect x="{frame_left:.1f}" y="{y - board_h / 2:.1f}" width="{frame_right - frame_left:.1f}" '
                         f'height="{board_h}" rx="1" fill="var(--color-border)"/>')

        comp_bottom, comp_top = y, board_y.get(k + 1, usable_top)
        label_y = (comp_top + comp_bottom) / 2 + 4
        fs = 14 if hit else 11
        color = "var(--color-ink)" if hit else "var(--color-ink-muted)"
        opacity = "1" if hit else _MUTED_OPACITY
        parts.append(f'<text x="{width / 2}" y="{label_y:.1f}" font-size="{fs}" fill="{color}" '
                     f'fill-opacity="{opacity}" text-anchor="middle" font-weight="{"700" if hit else "400"}">'
                     f'Shelf {k}</text>')

    parts.append("</svg>")
    return "".join(parts)
