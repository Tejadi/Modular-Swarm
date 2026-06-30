"""
OLYMPUS OS - Avionics Mount v5.0: Modular Snap-Fit System
============================================================
Tool-free modular avionics housing for Holybro X500 V2 drone.
Every component has a dedicated snap-fit holder with:
  - Snap ridges on cradle walls (catch component edges)
  - Thumb relief slots on outer walls (flex zone for easy release)
  - Alignment pegs/sockets between stacked bays
  - Slide-in rails for battery sled
  - Half-pipe arm cradles for ESCs with snap closure

Components:
  Lower Bay:  Pixhawk 2.4.8 (snap cradle, vibration-isolated)
  Upper Bay:  Jetson Orin Nano (snap cradle) + LoRa + ELRS (snap holders)
  Top Lid:    DroneCAN M9N GPS (bayonet mast) + RTK module (snap-in)
  Frame:      PDB 300A Side Entry (snap tray between plates)
              4x Readytosky 40A ESC (arm snap-on cradles)
              HRB 4S 5000mAh LiPo (slide-in battery sled)
  Camera:     AR0234 fixed nadir bracket

Print: PETG or Nylon PA12, 0.2mm layers, 50% infill, 4 perimeters.
Run:  ~/Downloads/FreeCAD*.AppImage freecadcmd hardware/olympus_mount.py
"""

import FreeCAD
import Part
import math
import os
from FreeCAD import Vector

# ============================================================
# CONFIGURATION
# ============================================================

# ── Enclosure ──
OUTER_L = 140.0
OUTER_W = 132.0
WALL = 5.0
FLOOR = 4.5
INNER_L = OUTER_L - 2 * WALL
INNER_W = OUTER_W - 2 * WALL
LO_CLR = 28.0
HI_CLR = 32.0
LO_H = FLOOR + LO_CLR
HI_H = FLOOR + HI_CLR
CX = OUTER_L / 2
CY = OUTER_W / 2

# ── Snap-Fit System ──
SNAP_D = 1.2           # snap ridge protrusion
SNAP_H = 2.0           # ridge height
SNAP_RL = 18.0         # ridge length
RELIEF_W = 14.0        # thumb relief width
RELIEF_DEPTH = 2.5     # relief cut depth
RELIEF_H = 16.0        # relief height
PEG_D = 2.0
PEG_H = 4.0

# ── Pixhawk 2.4.8 ──
PX4_L, PX4_W, PX4_H = 81.5, 50.0, 15.5
PX4_DAMP = 45.0
PX4_GAP = 2.0
PX4_CW = 3.0
PX4_CH = 14.0

# ── Jetson Orin Nano ──
JET_L, JET_W, JET_H = 100.0, 79.0, 21.0
JET_MOUNT_L, JET_MOUNT_W = 86.0, 58.0
JET_GAP = 1.5
JET_CW = 3.5
JET_CH = 20.0
JET_STANDOFF_H = 5.0

# ── LoRa / ELRS ──
LORA_L, LORA_W, LORA_H = 50.2, 25.5, 10.2
ELRS_SIZE = 10.0
ELRS_ANT = 45.0

# ── Holybro DroneCAN M9N GPS (54mm) ──
GPS_DIA = 54.0
GPS_THICK = 14.5
GPS_MAST_H = 85.0
GPS_TUBE_D = 12.0
GPS_BOLT_R = 20.0

# ── AR0234 Camera ──
CAM_L, CAM_W = 30.0, 30.0
CAM_BOLT = 24.4    # e-CAM25_CUONX mounting hole pitch
CAM_DROP = 50.0

# ── RTK Module ──
RTK_L, RTK_W, RTK_H = 18.0, 18.0, 8.0

# ── Holybro PDB 300A Side Entry ──
PDB_L, PDB_W, PDB_H = 80.0, 80.0, 20.0
PDB_BOLT_SPC = 70.0

# ── Readytosky 40A ESC ──
ESC_L, ESC_W, ESC_H = 68.0, 25.0, 8.7

# ── HRB 4S 5000mAh LiPo ──
BAT_L, BAT_W, BAT_H = 155.0, 48.0, 32.0

# ── Screws ──
M3_CLR = 3.4
M3_TAP = 2.5
M2_5_CLR = 2.8
M2_CLR = 2.4

# ── Bolt patterns ──
A_INSET = 10.0
ABOLTS = [(A_INSET, A_INSET), (OUTER_L - A_INSET, A_INSET),
          (OUTER_L - A_INSET, OUTER_W - A_INSET), (A_INSET, OUTER_W - A_INSET)]

PEG_INSET = 20.0
PEGS = [(PEG_INSET, WALL / 2), (OUTER_L - PEG_INSET, WALL / 2),
        (PEG_INSET, OUTER_W - WALL / 2), (OUTER_L - PEG_INSET, OUTER_W - WALL / 2)]

FBOLTS = [(CX + dx, CY + dy) for dx in (-55, 55) for dy in (-55, 55)]

CAM_BOLT_HALF = 20.0
CAMBOLTS = [(CX + dx, CY + dy)
            for dx in (-CAM_BOLT_HALF, CAM_BOLT_HALF)
            for dy in (-CAM_BOLT_HALF, CAM_BOLT_HALF)]

# ── Vents ──
VENT_L, VENT_H, VENT_N, VENT_GAP = 40.0, 3.5, 3, 3.0

# ── Holybro X500 V2 Frame ──
FRM_W = 185.0
FRM_T = 2.0
FRM_GAP = 28.0
FRM_ARM_L = 120.0
FRM_ARM_D = 16.0
FRM_MOTOR_D = 28.0
FRM_MOTOR_H = 8.0
FRM_CENTER_CUT = 80.0
MOUNT_Z = FRM_T + FRM_GAP + FRM_T


# ============================================================
# HELPERS
# ============================================================

def viz(obj, rgb=(0.7, 0.7, 0.7), alpha=0):
    try:
        if obj.ViewObject:
            obj.ViewObject.ShapeColor = rgb
            if alpha:
                obj.ViewObject.Transparency = alpha
    except Exception:
        pass


def drill(shape, pts, dia, z0, z1):
    for x, y in pts:
        h = Part.makeCylinder(dia / 2, abs(z1 - z0) + 2)
        h.translate(Vector(x, y, min(z0, z1) - 1))
        shape = shape.cut(h)
    return shape


def shell(L, W, H, wall, floor):
    outer = Part.makeBox(L, W, H)
    inner = Part.makeBox(L - 2 * wall, W - 2 * wall, H + 1)
    inner.translate(Vector(wall, wall, floor))
    return outer.cut(inner)


def vent_slots(shape, axis, pos, n, slot_l, slot_h, bay_clr, floor_z):
    total_h = n * slot_h + (n - 1) * VENT_GAP
    z0 = floor_z + (bay_clr - total_h) / 2
    for i in range(n):
        z = z0 + i * (slot_h + VENT_GAP)
        if axis == 'x':
            s = Part.makeBox(WALL + 2, slot_l, slot_h)
            s.translate(Vector(pos, CY - slot_l / 2, z))
        else:
            s = Part.makeBox(slot_l, WALL + 2, slot_h)
            s.translate(Vector(CX - slot_l / 2, pos, z))
        shape = shape.cut(s)
    return shape


def add_snap_ridges(shape, ox, oy, ol, ow, wall_t, snap_z):
    """Add 4 snap ridges on inner faces of a rectangular cradle."""
    rl = min(SNAP_RL, ol - 2 * wall_t - 4)
    cx_local = ox + ol / 2
    cy_local = oy + ow / 2
    # Front wall (inner face at oy + wall_t, protrudes +Y)
    r = Part.makeBox(rl, SNAP_D, SNAP_H)
    r.translate(Vector(cx_local - rl / 2, oy + wall_t, snap_z))
    shape = shape.fuse(r)
    # Back wall (inner face at oy + ow - wall_t, protrudes -Y)
    r = Part.makeBox(rl, SNAP_D, SNAP_H)
    r.translate(Vector(cx_local - rl / 2, oy + ow - wall_t - SNAP_D, snap_z))
    shape = shape.fuse(r)
    # Left wall
    rl2 = min(SNAP_RL, ow - 2 * wall_t - 4)
    r = Part.makeBox(SNAP_D, rl2, SNAP_H)
    r.translate(Vector(ox + wall_t, cy_local - rl2 / 2, snap_z))
    shape = shape.fuse(r)
    # Right wall
    r = Part.makeBox(SNAP_D, rl2, SNAP_H)
    r.translate(Vector(ox + ol - wall_t - SNAP_D, cy_local - rl2 / 2, snap_z))
    shape = shape.fuse(r)
    return shape


def add_thumb_reliefs(shape, ox, oy, ol, ow, wall_t, relief_z):
    """Cut 4 thumb reliefs on outer faces of a rectangular cradle."""
    cx_local = ox + ol / 2
    cy_local = oy + ow / 2
    rw = min(RELIEF_W, ol - 10)
    rw2 = min(RELIEF_W, ow - 10)
    # Front outer
    rel = Part.makeBox(rw, RELIEF_DEPTH, RELIEF_H)
    rel.translate(Vector(cx_local - rw / 2, oy, relief_z))
    shape = shape.cut(rel)
    # Back outer
    rel = Part.makeBox(rw, RELIEF_DEPTH, RELIEF_H)
    rel.translate(Vector(cx_local - rw / 2, oy + ow - RELIEF_DEPTH, relief_z))
    shape = shape.cut(rel)
    # Left outer
    rel = Part.makeBox(RELIEF_DEPTH, rw2, RELIEF_H)
    rel.translate(Vector(ox, cy_local - rw2 / 2, relief_z))
    shape = shape.cut(rel)
    # Right outer
    rel = Part.makeBox(RELIEF_DEPTH, rw2, RELIEF_H)
    rel.translate(Vector(ox + ol - RELIEF_DEPTH, cy_local - rw2 / 2, relief_z))
    shape = shape.cut(rel)
    return shape


def add_pegs(shape, z_top):
    for px, py in PEGS:
        peg = Part.makeCylinder(PEG_D / 2, PEG_H)
        peg.translate(Vector(px, py, z_top))
        shape = shape.fuse(peg)
    return shape


def add_sockets(shape, z_bot):
    for px, py in PEGS:
        sock = Part.makeCylinder(PEG_D / 2 + 0.15, PEG_H + 1)
        sock.translate(Vector(px, py, z_bot - 1))
        shape = shape.cut(sock)
    return shape


# ============================================================
# LOWER BAY: Pixhawk Snap Cradle
# ============================================================

def create_lower_bay():
    s = shell(OUTER_L, OUTER_W, LO_H, WALL, FLOOR)
    s = drill(s, ABOLTS, M3_CLR, 0, LO_H)
    s = drill(s, FBOLTS, M3_CLR, 0, FLOOR)
    s = drill(s, CAMBOLTS, M3_CLR, 0, FLOOR)
    s = add_pegs(s, LO_H)

    # ── Pixhawk Snap Cradle ──
    cil = PX4_L + 2 * PX4_GAP
    ciw = PX4_W + 2 * PX4_GAP
    col = cil + 2 * PX4_CW
    cow = ciw + 2 * PX4_CW
    ox = CX - col / 2
    oy = CY - cow / 2

    c_out = Part.makeBox(col, cow, PX4_CH)
    c_out.translate(Vector(ox, oy, FLOOR))
    c_in = Part.makeBox(cil, ciw, PX4_CH + 1)
    c_in.translate(Vector(ox + PX4_CW, oy + PX4_CW, FLOOR))
    cradle = c_out.cut(c_in)

    # Vibration grommet posts
    hp = PX4_DAMP / 2
    for dx, dy in [(-hp, -hp), (hp, -hp), (hp, hp), (-hp, hp)]:
        gx, gy = CX + dx, CY + dy
        post = Part.makeCylinder(4.5, 5.0)
        post.translate(Vector(gx, gy, FLOOR))
        cradle = cradle.fuse(post)
        hole = Part.makeCylinder(M3_CLR / 2, FLOOR + 7)
        hole.translate(Vector(gx, gy, -1))
        cradle = cradle.cut(hole)

    # Snap ridges + thumb reliefs
    snap_z = FLOOR + PX4_CH - 4
    cradle = add_snap_ridges(cradle, ox, oy, col, cow, PX4_CW, snap_z)
    cradle = add_thumb_reliefs(cradle, ox, oy, col, cow, PX4_CW,
                               FLOOR + PX4_CH - RELIEF_H)
    s = s.fuse(cradle)

    # Vents
    for axis, pos in [('y', -1), ('y', OUTER_W - WALL - 1),
                      ('x', -1), ('x', OUTER_L - WALL - 1)]:
        s = vent_slots(s, axis, pos, VENT_N, VENT_L, VENT_H, LO_CLR, FLOOR)

    # Cable cutouts
    for yp in [-1, OUTER_W - WALL - 1]:
        cut = Part.makeBox(30, WALL + 2, 12)
        cut.translate(Vector(CX - 15, yp, FLOOR + 5))
        s = s.cut(cut)
    pwr = Part.makeBox(WALL + 2, 18, 8)
    pwr.translate(Vector(-1, CY - 9, FLOOR + 8))
    s = s.cut(pwr)

    return s


# ============================================================
# UPPER BAY: Jetson + Radio Snap Cradles
# ============================================================

def create_upper_bay():
    s = shell(OUTER_L, OUTER_W, HI_H, WALL, FLOOR)
    s = drill(s, ABOLTS, M3_CLR, 0, HI_H)
    s = add_sockets(s, 0)
    s = add_pegs(s, HI_H)

    # Floor vent holes
    for i in range(5):
        for j in range(3):
            v = Part.makeCylinder(2.5, FLOOR + 2)
            v.translate(Vector(CX - 30 + i * 15, CY - 15 + j * 15, -1))
            s = s.cut(v)

    # ── Jetson Snap Cradle ──
    cil = JET_L + 2 * JET_GAP
    ciw = JET_W + 2 * JET_GAP
    col = cil + 2 * JET_CW
    cow = ciw + 2 * JET_CW
    jx = CX - col / 2
    jy = CY - cow / 2 + 5

    c_out = Part.makeBox(col, cow, JET_CH)
    c_out.translate(Vector(jx, jy, FLOOR))
    c_in = Part.makeBox(cil, ciw, JET_CH + 1)
    c_in.translate(Vector(jx + JET_CW, jy + JET_CW, FLOOR))
    cradle = c_out.cut(c_in)

    # USB/IO cutout
    usb_cut = Part.makeBox(45, JET_CW + 2, JET_CH - 4)
    usb_cut.translate(Vector(CX - 22.5, jy + cow - JET_CW - 1, FLOOR + 4))
    cradle = cradle.cut(usb_cut)

    # Jetson standoffs
    for dx, dy in [(-JET_MOUNT_L / 2, -JET_MOUNT_W / 2),
                   (JET_MOUNT_L / 2, -JET_MOUNT_W / 2),
                   (JET_MOUNT_L / 2, JET_MOUNT_W / 2),
                   (-JET_MOUNT_L / 2, JET_MOUNT_W / 2)]:
        sx, sy = CX + dx, CY + 5 + dy
        post = Part.makeCylinder(2.5, JET_STANDOFF_H)
        post.translate(Vector(sx, sy, FLOOR))
        cradle = cradle.fuse(post)
        hole = Part.makeCylinder(M2_5_CLR / 2, FLOOR + JET_STANDOFF_H + 2)
        hole.translate(Vector(sx, sy, -1))
        cradle = cradle.cut(hole)

    # Snap ridges + thumb reliefs on Jetson cradle
    snap_z = FLOOR + JET_CH - 4
    cradle = add_snap_ridges(cradle, jx, jy, col, cow, JET_CW, snap_z)
    cradle = add_thumb_reliefs(cradle, jx, jy, col, cow, JET_CW,
                               FLOOR + JET_CH - RELIEF_H)
    s = s.fuse(cradle)

    # ── LoRa Snap Holder ──
    lx = WALL + 4
    ly = OUTER_W - WALL - LORA_W - 8
    lcw = 3.0
    lora_shapes = [
        (lx - lcw, ly - 3, FLOOR, lcw, LORA_W + 6, LORA_H + 4),
        (lx + LORA_L, ly - 3, FLOOR, lcw, LORA_W + 6, LORA_H + 4),
        (lx - lcw - 1, ly - 3 - lcw, FLOOR, LORA_L + 2 * lcw + 2, lcw, LORA_H + 4),
    ]
    for args in lora_shapes:
        w = Part.makeBox(args[3], args[4], args[5])
        w.translate(Vector(args[0], args[1], args[2]))
        s = s.fuse(w)

    # LoRa snap ridges (2 on side walls)
    for wx in [lx - lcw, lx + LORA_L]:
        r = Part.makeBox(SNAP_D, 10, SNAP_H)
        lface = wx + lcw if wx < lx else wx - SNAP_D
        r.translate(Vector(lface, ly + LORA_W / 2 - 5, FLOOR + LORA_H))
        s = s.fuse(r)

    # LoRa antenna pass-through
    ant = Part.makeBox(8, WALL + 2, 8)
    ant.translate(Vector(lx + LORA_L / 2 - 4, OUTER_W - WALL - 1, FLOOR + LORA_H / 2))
    s = s.cut(ant)

    # ── ELRS Snap Pocket ──
    ex = OUTER_L - WALL - ELRS_SIZE - 10
    ey = OUTER_W - WALL - ELRS_SIZE - 10
    ep_out = Part.makeBox(ELRS_SIZE + 6, ELRS_SIZE + 6, 8)
    ep_out.translate(Vector(ex - 3, ey - 3, FLOOR))
    ep_in = Part.makeBox(ELRS_SIZE + 1, ELRS_SIZE + 1, 9)
    ep_in.translate(Vector(ex - 0.5, ey - 0.5, FLOOR + 2))
    pocket = ep_out.cut(ep_in)

    # ELRS snap ridges (2, opposing)
    for face_y in [ey - 3, ey + ELRS_SIZE + 3 - SNAP_D]:
        r = Part.makeBox(ELRS_SIZE, SNAP_D, SNAP_H)
        r.translate(Vector(ex - 0.5, face_y, FLOOR + 6))
        pocket = pocket.fuse(r)
    s = s.fuse(pocket)

    # ELRS antenna slot
    eslot = Part.makeBox(WALL + 2, 5, ELRS_ANT)
    eslot.translate(Vector(OUTER_L - WALL - 1, ey + ELRS_SIZE / 2 - 2.5, FLOOR + 2))
    s = s.cut(eslot)

    # Vents
    for axis, pos in [('y', -1), ('y', OUTER_W - WALL - 1),
                      ('x', -1), ('x', OUTER_L - WALL - 1)]:
        s = vent_slots(s, axis, pos, VENT_N, VENT_L, VENT_H, HI_CLR, FLOOR)

    # Cable cutouts
    usb_enc = Part.makeBox(50, WALL + 2, 16)
    usb_enc.translate(Vector(CX - 25, -1, FLOOR + JET_STANDOFF_H))
    s = s.cut(usb_enc)
    csi = Part.makeBox(25, WALL + 2, 6)
    csi.translate(Vector(CX - 12.5, OUTER_W - WALL - 1, FLOOR + 3))
    s = s.cut(csi)

    return s


# ============================================================
# TOP LID: GPS boss + RTK snap slot + nav slot
# ============================================================

def create_top_lid():
    lid = Part.makeBox(OUTER_L, OUTER_W, FLOOR)
    lid = drill(lid, ABOLTS, M3_CLR, 0, FLOOR)
    lid = add_sockets(lid, 0)

    # GPS mast boss (updated for 54mm DroneCAN M9N)
    boss = Part.makeCylinder(GPS_TUBE_D / 2 + 4, 8)
    boss.translate(Vector(CX, CY, FLOOR))
    bore = Part.makeCylinder(GPS_TUBE_D / 2 + 0.15, FLOOR + 10)
    bore.translate(Vector(CX, CY, -1))
    lid = lid.fuse(boss).cut(bore)

    # Nav slot 1 (RTK module) — snap-in frame with ridges
    rtk_sx, rtk_sy = CX - 25, CY + 30
    slot1 = Part.makeBox(20, 20, FLOOR + 2)
    slot1.translate(Vector(rtk_sx - 10, rtk_sy - 10, -1))
    lid = lid.cut(slot1)
    frame1_o = Part.makeBox(24, 24, 5)
    frame1_o.translate(Vector(rtk_sx - 12, rtk_sy - 12, FLOOR))
    frame1_i = Part.makeBox(20, 20, 6)
    frame1_i.translate(Vector(rtk_sx - 10, rtk_sy - 10, FLOOR - 0.5))
    slot_frame = frame1_o.cut(frame1_i)
    # Snap ridges inside slot frame
    for dy in [-12, 12 - SNAP_D]:
        r = Part.makeBox(10, SNAP_D, SNAP_H)
        r.translate(Vector(rtk_sx - 5, rtk_sy + dy, FLOOR + 2))
        slot_frame = slot_frame.fuse(r)
    lid = lid.fuse(slot_frame)

    # Nav slot 2 (open, magnetometer/barometer) — same snap frame
    ns2_sx, ns2_sy = CX + 25, CY + 30
    slot2 = Part.makeBox(20, 20, FLOOR + 2)
    slot2.translate(Vector(ns2_sx - 10, ns2_sy - 10, -1))
    lid = lid.cut(slot2)
    frame2_o = Part.makeBox(24, 24, 5)
    frame2_o.translate(Vector(ns2_sx - 12, ns2_sy - 12, FLOOR))
    frame2_i = Part.makeBox(20, 20, 6)
    frame2_i.translate(Vector(ns2_sx - 10, ns2_sy - 10, FLOOR - 0.5))
    slot2_frame = frame2_o.cut(frame2_i)
    for dy in [-12, 12 - SNAP_D]:
        r = Part.makeBox(10, SNAP_D, SNAP_H)
        r.translate(Vector(ns2_sx - 5, ns2_sy + dy, FLOOR + 2))
        slot2_frame = slot2_frame.fuse(r)
    lid = lid.fuse(slot2_frame)

    # Antenna cable pass-throughs
    for off in [-40, 40]:
        ch = Part.makeCylinder(4, FLOOR + 2)
        ch.translate(Vector(CX + off, OUTER_W - 15, -1))
        lid = lid.cut(ch)

    # Alignment lip (stacking registration)
    lip_h, lip_t = 3.0, 2.0
    lip_o = Part.makeBox(OUTER_L - 2 * (WALL - lip_t),
                         OUTER_W - 2 * (WALL - lip_t), lip_h)
    lip_o.translate(Vector(WALL - lip_t, WALL - lip_t, -lip_h))
    lip_i = Part.makeBox(INNER_L, INNER_W, lip_h + 2)
    lip_i.translate(Vector(WALL, WALL, -lip_h - 1))
    lid = lid.fuse(lip_o.cut(lip_i))

    return lid


# ============================================================
# GPS MAST (updated for DroneCAN M9N 54mm)
# ============================================================

def create_gps_mast():
    tube = Part.makeCylinder(GPS_TUBE_D / 2, GPS_MAST_H)
    bore = Part.makeCylinder(GPS_TUBE_D / 2 - 2, GPS_MAST_H - 3)
    bore.translate(Vector(0, 0, -1))
    mast = tube.cut(bore)

    # Reinforcement collar
    collar = Part.makeCylinder(GPS_TUBE_D / 2 + 3, 15)
    collar_b = Part.makeCylinder(GPS_TUBE_D / 2, 16)
    collar_b.translate(Vector(0, 0, -0.5))
    mast = mast.fuse(collar.cut(collar_b))

    # M9N mounting plate (54mm + margin)
    plate_r = GPS_DIA / 2 + 3
    plate = Part.makeCylinder(plate_r, 3)
    plate.translate(Vector(0, 0, GPS_MAST_H))
    mast = mast.fuse(plate)

    # Bayonet twist-lock tabs (3 at 120 degrees)
    for a in [0, 120, 240]:
        rad = math.radians(a)
        tx = (GPS_DIA / 2 + 1) * math.cos(rad)
        ty = (GPS_DIA / 2 + 1) * math.sin(rad)
        # L-shaped tab: radial arm + circumferential catch
        arm = Part.makeBox(4, 8, 3)
        arm.translate(Vector(-2, -4, 0))
        arm.rotate(Vector(0, 0, 0), Vector(0, 0, 1), math.degrees(rad))
        arm.translate(Vector(tx, ty, GPS_MAST_H + 3))
        mast = mast.fuse(arm)

    # GPS bolt holes (3 at 120 degrees)
    for a in [0, 120, 240]:
        rad = math.radians(a)
        h = Part.makeCylinder(M3_CLR / 2, 5)
        h.translate(Vector(GPS_BOLT_R * math.cos(rad),
                           GPS_BOLT_R * math.sin(rad), GPS_MAST_H - 1))
        mast = mast.cut(h)

    # Cable channel
    ch = Part.makeCylinder(2.5, GPS_MAST_H + 5)
    ch.translate(Vector(0, 0, -1))
    mast = mast.cut(ch)

    groove = Part.makeBox(4, 3, GPS_MAST_H - 3)
    groove.translate(Vector(-2, GPS_TUBE_D / 2 - 2, 0))
    mast = mast.cut(groove)

    return mast


def create_dronecan_m9n():
    """Visual representation of DroneCAN M9N GPS module (54mm dia)."""
    body = Part.makeCylinder(GPS_DIA / 2, GPS_THICK)
    # Ceramic patch antenna on top
    ant = Part.makeBox(25, 25, 4)
    ant.translate(Vector(-12.5, -12.5, GPS_THICK))
    # GHR connector
    conn = Part.makeBox(8, 5, 4)
    conn.translate(Vector(-4, GPS_DIA / 2 - 5, -4))
    return body.fuse(ant).fuse(conn)


# ============================================================
# RTK MODULE (snap-in for nav slot)
# ============================================================

def create_rtk_module():
    pcb = Part.makeBox(RTK_L, RTK_W, 1.6)
    comp = Part.makeBox(RTK_L - 4, RTK_W - 4, RTK_H - 1.6)
    comp.translate(Vector(2, 2, 1.6))
    sma = Part.makeCylinder(3, 6)
    sma.translate(Vector(RTK_L / 2, RTK_W - 2, 1.6))
    return pcb.fuse(comp).fuse(sma)


# ============================================================
# CAMERA MOUNT: Fixed nadir with snap-lock tabs
# ============================================================

def create_camera_mount():
    arm_gap = 70.0
    arm_w = 10.0

    parts = []
    for sign in [-1, 1]:
        y = CY + sign * arm_gap / 2 + (0 if sign > 0 else -arm_w)
        plate = Part.makeBox(50.0, arm_w, CAM_DROP + FLOOR)
        plate.translate(Vector(CX - 25, y, -CAM_DROP))
        parts.append(plate)

    mount = parts[0].fuse(parts[1])

    # Top mounting flange with snap-lock tabs
    flange_l = 60.0
    flange_w = arm_gap + 2 * arm_w + 10
    flange = Part.makeBox(flange_l, flange_w, 3)
    flange.translate(Vector(CX - flange_l / 2, CY - flange_w / 2, 0))
    mount = mount.fuse(flange)

    # Snap-lock tabs on flange (4 corners, deflect to insert, catch on bay floor)
    tab_l, tab_w, tab_h = 8, 4, 5
    for dx in [-flange_l / 2 + 6, flange_l / 2 - 6 - tab_l]:
        for dy in [-flange_w / 2 + 6, flange_w / 2 - 6 - tab_w]:
            tab = Part.makeBox(tab_l, tab_w, tab_h)
            tab.translate(Vector(CX + dx, CY + dy, 3))
            # Hook bump on the tab
            hook = Part.makeBox(tab_l, tab_w, 1.5)
            hook.translate(Vector(CX + dx, CY + dy - 1, 3 + tab_h - 1.5))
            mount = mount.fuse(tab).fuse(hook)

    # Flange bolt holes
    mount = drill(mount, CAMBOLTS, M3_CLR, -1, 4)

    # Cross braces
    for bz in [-CAM_DROP + 5, -CAM_DROP / 2]:
        brace = Part.makeBox(5, arm_gap + 2 * arm_w, 5)
        brace.translate(Vector(CX - 2.5, CY - arm_gap / 2 - arm_w, bz))
        mount = mount.fuse(brace)

    # Camera plate (nadir)
    cp = Part.makeBox(CAM_L + 10, CAM_W + 10, 3)
    cp.translate(Vector(CX - (CAM_L + 10) / 2, CY - (CAM_W + 10) / 2, -CAM_DROP))
    mount = mount.fuse(cp)

    # Camera bolt holes
    hp = CAM_BOLT / 2
    for dx, dy in [(-hp, -hp), (hp, -hp), (hp, hp), (-hp, hp)]:
        h = Part.makeCylinder(M2_CLR / 2, 5)
        h.translate(Vector(CX + dx, CY + dy, -CAM_DROP - 1))
        mount = mount.cut(h)

    # Lens window
    lens = Part.makeCylinder(10, 5)
    lens.translate(Vector(CX, CY, -CAM_DROP - 1))
    mount = mount.cut(lens)

    # CSI cable channel
    cable_ch = Part.makeBox(12, 3, CAM_DROP + FLOOR + 2)
    cable_ch.translate(Vector(CX - 6, CY + arm_gap / 2 + arm_w - 3, -CAM_DROP - 1))
    mount = mount.cut(cable_ch)

    return mount


# ============================================================
# PDB TRAY: Snap-in for Holybro PDB 300A Side Entry
# ============================================================

def create_pdb_tray():
    """80x80mm PDB snap tray. Sits between frame plates."""
    tw = 3.0       # tray wall
    tf = 2.5       # tray floor
    gap = 1.0      # clearance around PDB
    til = PDB_L + 2 * gap
    tiw = PDB_W + 2 * gap
    tol = til + 2 * tw
    tow = tiw + 2 * tw
    th = tf + PDB_H + 2   # a bit taller than PDB

    outer = Part.makeBox(tol, tow, th)
    inner = Part.makeBox(til, tiw, th + 1)
    inner.translate(Vector(tw, tw, tf))
    tray = outer.cut(inner)

    # M3 standoff posts at 70mm pattern
    pdb_cx, pdb_cy = tol / 2, tow / 2
    hp = PDB_BOLT_SPC / 2
    bolt_pts = [(pdb_cx + dx, pdb_cy + dy)
                for dx in (-hp, hp) for dy in (-hp, hp)]
    for bx, by in bolt_pts:
        post = Part.makeCylinder(3, 3)
        post.translate(Vector(bx, by, tf))
        tray = tray.fuse(post)
        hole = Part.makeCylinder(M3_CLR / 2, tf + 5)
        hole.translate(Vector(bx, by, -1))
        tray = tray.cut(hole)

    # Snap ridges + thumb reliefs
    snap_z = tf + PDB_H - 3
    tray = add_snap_ridges(tray, 0, 0, tol, tow, tw, snap_z)
    tray = add_thumb_reliefs(tray, 0, 0, tol, tow, tw, tf + PDB_H - RELIEF_H)

    # XT90 side entry cutouts (5 on one side)
    for i in range(5):
        c = Part.makeBox(tw + 2, 12, 14)
        c.translate(Vector(-1, tw + gap + 4 + i * 15, tf + 3))
        tray = tray.cut(c)
    # XT30 cutouts (2 on opposite side)
    for i in range(2):
        c = Part.makeBox(tw + 2, 8, 10)
        c.translate(Vector(tol - tw - 1, tw + gap + 20 + i * 25, tf + 3))
        tray = tray.cut(c)

    return tray


# ============================================================
# BATTERY SLED: Slide-in for HRB 4S 5000mAh LiPo
# ============================================================

def create_battery_sled():
    """Slide-in battery tray for 155x48x32mm LiPo.
    Tight fit (0.5mm gap), slides in from one end under frame bottom plate."""
    sw = 3.0       # sled wall thickness
    sf = 3.0       # sled floor
    gap = 0.5      # tight clearance (was 1.5 — too loose for flight)
    sil = BAT_L + 2 * gap
    siw = BAT_W + 2 * gap
    sol = sil + 2 * sw
    sow = siw + 2 * sw
    sh = sf + BAT_H + 2

    # Main tray (open top)
    outer = Part.makeBox(sol, sow, sh)
    inner = Part.makeBox(sil, siw, sh + 1)
    inner.translate(Vector(sw, sw, sf))
    sled = outer.cut(inner)

    # Snap ridges on long walls (3 per side, tighter retention)
    snap_z = sf + BAT_H - 3
    for wx in [sw, sol - sw - SNAP_D]:
        for yoff in [sow * 0.25, sow * 0.5, sow * 0.75]:
            r = Part.makeBox(SNAP_D, 12, SNAP_H)
            r.translate(Vector(wx, yoff - 6, snap_z))
            sled = sled.fuse(r)

    # Mid-height ridges too (prevent lateral rattle)
    mid_z = sf + BAT_H / 2
    for wx in [sw, sol - sw - SNAP_D]:
        for yoff in [sow * 0.35, sow * 0.65]:
            r = Part.makeBox(SNAP_D, 15, SNAP_H)
            r.translate(Vector(wx, yoff - 7.5, mid_z))
            sled = sled.fuse(r)

    # Thumb reliefs on long walls
    for wx in [0, sol - RELIEF_DEPTH]:
        rel = Part.makeBox(RELIEF_DEPTH, 20, RELIEF_H)
        rel.translate(Vector(wx, sow / 2 - 10, sf + BAT_H - RELIEF_H))
        sled = sled.cut(rel)

    # Velcro strap slots (2 slots across the bottom)
    for xoff in [sol * 0.3, sol * 0.7]:
        slot = Part.makeBox(20, sow + 2, 3)
        slot.translate(Vector(xoff - 10, -1, 0))
        sled = sled.cut(slot)

    # Slide rails on top edges (L-shaped lips that engage frame channels)
    rail_h = 4.0
    rail_d = 5.0
    for ypos in [0, sow - rail_d]:
        rail = Part.makeBox(sol, rail_d, rail_h)
        rail.translate(Vector(0, ypos, sh))
        sled = sled.fuse(rail)

    # End stop / snap catch at sled end (spans sled width, 3mm deep)
    catch_w = sow - 2 * sw
    catch = Part.makeBox(3, catch_w, 6)
    catch.translate(Vector(sol, sw, sh - 6))
    hook = Part.makeBox(3, catch_w, SNAP_H)
    hook.translate(Vector(sol, sw, sh - SNAP_H))
    sled = sled.fuse(catch).fuse(hook)

    # Front lip stop (prevents over-insertion, spans sled width)
    front_lip = Part.makeBox(3, sow, 4)
    front_lip.translate(Vector(-3, 0, sh - 4))
    sled = sled.fuse(front_lip)

    return sled


# ============================================================
# ESC CRADLE: Snap-on arm mount for Readytosky 40A ESC
# ============================================================

def create_esc_cradle():
    """Half-pipe cradle that snaps onto 16mm arm tube.
    ESC platform extends from one side. Print 4 of these."""
    arm_r = FRM_ARM_D / 2        # 8mm
    cradle_r = arm_r + 3.0       # 11mm outer
    cradle_len = 35.0            # length along arm

    # Full cylinder (will cut to half-pipe)
    outer = Part.makeCylinder(cradle_r, cradle_len,
                              Vector(0, 0, 0), Vector(1, 0, 0))
    inner = Part.makeCylinder(arm_r + 0.3, cradle_len + 2,
                              Vector(-1, 0, 0), Vector(1, 0, 0))
    pipe = outer.cut(inner)

    # Cut to ~270 degrees (leave a gap at top for snap closure)
    cut_w = arm_r  # width of opening at top
    top_cut = Part.makeBox(cradle_len + 2, cut_w * 2, cradle_r + 1)
    top_cut.translate(Vector(-1, -cut_w, 0))
    pipe = pipe.cut(top_cut)

    # Snap closure tabs (2 flexible arms that bridge the gap)
    for xoff in [5, cradle_len - 10]:
        # Left tab
        tab = Part.makeBox(5, 2, cradle_r + 2)
        tab.translate(Vector(xoff, -cut_w, 0))
        hook_l = Part.makeBox(5, 3, 2)
        hook_l.translate(Vector(xoff, -cut_w - 1, cradle_r))
        pipe = pipe.fuse(tab).fuse(hook_l)
        # Right tab
        tab = Part.makeBox(5, 2, cradle_r + 2)
        tab.translate(Vector(xoff, cut_w - 2, 0))
        hook_r = Part.makeBox(5, 3, 2)
        hook_r.translate(Vector(xoff, cut_w - 2, cradle_r))
        pipe = pipe.fuse(tab).fuse(hook_r)

    # ESC platform (hangs directly UNDER the arm, centered)
    plat_l = ESC_L + 4
    plat_w = ESC_W + 4
    esc_wall_h = ESC_H + 2
    # Platform floor below the pipe
    plat = Part.makeBox(plat_l, plat_w, 2)
    plat.translate(Vector(cradle_len / 2 - plat_l / 2,
                          -plat_w / 2, -cradle_r - esc_wall_h - 2))
    pipe = pipe.fuse(plat)

    # ESC snap walls (extend up from platform to pipe bottom)
    for yoff in [-plat_w / 2, plat_w / 2 - 2]:
        w = Part.makeBox(plat_l - 10, 2, esc_wall_h)
        w.translate(Vector(cradle_len / 2 - plat_l / 2 + 5, yoff,
                           -cradle_r - esc_wall_h))
        # Snap ridge on inner face
        r = Part.makeBox(plat_l - 20, SNAP_D, SNAP_H)
        inner_y = yoff + 2 if yoff == -plat_w / 2 else yoff - SNAP_D
        r.translate(Vector(cradle_len / 2 - plat_l / 2 + 10, inner_y,
                           -cradle_r - 3))
        pipe = pipe.fuse(w).fuse(r)

    return pipe


# ============================================================
# HOLYBRO X500 V2 FRAME (with battery sled channels)
# ============================================================

def create_x500_frame():
    fcx, fcy = FRM_W / 2, FRM_W / 2

    # Bottom plate
    bp = Part.makeBox(FRM_W, FRM_W, FRM_T)
    bp_cut = Part.makeBox(FRM_CENTER_CUT, FRM_CENTER_CUT, FRM_T + 2)
    bp_cut.translate(Vector(fcx - FRM_CENTER_CUT / 2,
                            fcy - FRM_CENTER_CUT / 2, -1))
    bp = bp.cut(bp_cut)

    # Top plate
    tp = Part.makeBox(FRM_W, FRM_W, FRM_T)
    tp.translate(Vector(0, 0, FRM_T + FRM_GAP))
    tp_cut = Part.makeBox(FRM_CENTER_CUT, FRM_CENTER_CUT, FRM_T + 2)
    tp_cut.translate(Vector(fcx - FRM_CENTER_CUT / 2,
                            fcy - FRM_CENTER_CUT / 2,
                            FRM_T + FRM_GAP - 1))
    tp = tp.cut(tp_cut)

    frame = bp.fuse(tp)

    # Corner standoffs
    so_inset = 12.0
    for sx, sy in [(so_inset, so_inset), (FRM_W - so_inset, so_inset),
                   (FRM_W - so_inset, FRM_W - so_inset),
                   (so_inset, FRM_W - so_inset)]:
        so = Part.makeCylinder(4, FRM_GAP)
        so.translate(Vector(sx, sy, FRM_T))
        frame = frame.fuse(so)

    # 4 arm tubes (diagonal) — arms start INSIDE plate area, clamped between plates
    arm_z = FRM_T + FRM_GAP / 2
    arm_inset = 30.0  # arm starts 30mm inside from plate corner
    clamp_l = 40.0    # clamp block length along arm
    clamp_w = 24.0    # clamp block width (perpendicular to arm)
    corners = [(FRM_W, FRM_W, 1, 1), (0, FRM_W, -1, 1),
               (0, 0, -1, -1), (FRM_W, 0, 1, -1)]
    for cx, cy, dx, dy in corners:
        arm_dir = Vector(dx, dy, 0)
        arm_dir.normalize()
        # Arm starts inside the plate, extends outward
        arm_start_x = cx - dx * arm_inset / math.sqrt(2)
        arm_start_y = cy - dy * arm_inset / math.sqrt(2)
        arm_total = FRM_ARM_L + arm_inset
        arm = Part.makeCylinder(FRM_ARM_D / 2, arm_total,
                                Vector(arm_start_x, arm_start_y, arm_z),
                                arm_dir)
        frame = frame.fuse(arm)

        # Clamp block where arm passes between plates (aluminum sandwich)
        clamp = Part.makeBox(clamp_l, clamp_w, FRM_GAP + 2 * FRM_T)
        clamp.translate(Vector(-clamp_l / 2, -clamp_w / 2, 0))
        # Rotate to align with arm diagonal
        angle = math.degrees(math.atan2(dy, dx))
        clamp.rotate(Vector(0, 0, 0), Vector(0, 0, 1), angle)
        # Position at plate corner where arm exits
        clamp.translate(Vector(cx - dx * 5 / math.sqrt(2),
                               cy - dy * 5 / math.sqrt(2), 0))
        # Cut arm bore through the clamp
        bore = Part.makeCylinder(FRM_ARM_D / 2 + 0.5, clamp_l + 2,
                                 Vector(arm_start_x, arm_start_y, arm_z),
                                 arm_dir)
        clamp = clamp.cut(bore)
        frame = frame.fuse(clamp)

        # Clamp bolt holes (2 per clamp, M3)
        for bolt_off in [-8, 8]:
            perp = Vector(-dy, dx, 0)
            perp.normalize()
            bx = cx - dx * 5 / math.sqrt(2) + perp.x * bolt_off
            by = cy - dy * 5 / math.sqrt(2) + perp.y * bolt_off
            bh = Part.makeCylinder(M3_CLR / 2, FRM_GAP + 2 * FRM_T + 2)
            bh.translate(Vector(bx, by, -1))
            frame = frame.cut(bh)

        # Motor mount at arm tip
        mx = cx + dx * FRM_ARM_L / math.sqrt(2)
        my = cy + dy * FRM_ARM_L / math.sqrt(2)
        motor = Part.makeCylinder(FRM_MOTOR_D / 2, FRM_MOTOR_H)
        motor.translate(Vector(mx, my, arm_z - FRM_MOTOR_H / 2))
        frame = frame.fuse(motor)

    # (No custom landing gear — X500 V2 ships with its own landing gear kit)

    # Mount standoff bolts (visible M3 posts connecting frame top plate to mount)
    mount_ox = (FRM_W - OUTER_L) / 2
    mount_oy = (FRM_W - OUTER_W) / 2
    for bx, by in FBOLTS:
        # Bolt post visible above top plate (shows physical connection)
        post = Part.makeCylinder(3.5, 5)
        post.translate(Vector(mount_ox + bx, mount_oy + by,
                              FRM_T + FRM_GAP + FRM_T))
        frame = frame.fuse(post)

    # Mount bolt holes in top plate
    mount_ox = (FRM_W - OUTER_L) / 2
    mount_oy = (FRM_W - OUTER_W) / 2
    for bx, by in FBOLTS:
        h = Part.makeCylinder(M3_CLR / 2, FRM_T + 2)
        h.translate(Vector(mount_ox + bx, mount_oy + by,
                           FRM_T + FRM_GAP - 1))
        frame = frame.cut(h)

    # Battery sled support: cross-support rails bridging center cutout
    # Battery is centered on frame (Y direction). Sled sits under bottom plate.
    # Two X-direction rails at sled edges + two Y-direction cross beams for rigidity.
    rail_d = 5.0      # rail width
    rail_h = 4.0      # rail drop below bottom plate
    sled_ow = BAT_W + 2 * 0.5 + 2 * 3.0  # sled outer width = 55mm
    bat_y_center = FRM_W / 2              # centered on frame
    bat_y_start = bat_y_center - sled_ow / 2

    # Two long X-rails (full frame width) at sled Y edges — sled slides between them
    for side in [0, 1]:
        ypos = bat_y_start + (0 if side == 0 else sled_ow - rail_d)
        rail = Part.makeBox(FRM_W, rail_d, rail_h)
        rail.translate(Vector(0, ypos, -rail_h))
        frame = frame.fuse(rail)

    # Two Y-direction cross beams bridging the center cutout (structural support)
    # These sit under the bottom plate and span from solid plate to solid plate
    cutout_start = fcx - FRM_CENTER_CUT / 2  # X = 52.5
    cutout_end = fcx + FRM_CENTER_CUT / 2    # X = 132.5
    beam_w = 6.0
    for bx in [cutout_start + 15, cutout_end - 15]:  # two beams inside cutout area
        beam = Part.makeBox(beam_w, sled_ow + 2 * rail_d, rail_h)
        beam.translate(Vector(bx - beam_w / 2, bat_y_start - rail_d, -rail_h))
        frame = frame.fuse(beam)

    return frame


# ============================================================
# ASSEMBLY
# ============================================================

def assemble():
    doc = FreeCAD.ActiveDocument
    if doc is None:
        doc = FreeCAD.newDocument("OlympusMount_v5")
    else:
        for obj in doc.Objects:
            doc.removeObject(obj.Name)

    # Create all geometry
    frame = create_x500_frame()
    lower = create_lower_bay()
    upper = create_upper_bay()
    lid = create_top_lid()
    mast = create_gps_mast()
    cam = create_camera_mount()
    rtk = create_rtk_module()
    m9n = create_dronecan_m9n()
    pdb = create_pdb_tray()
    bat = create_battery_sled()
    esc = create_esc_cradle()

    # ── Position everything relative to frame origin ──
    mount_ox = (FRM_W - OUTER_L) / 2
    mount_oy = (FRM_W - OUTER_W) / 2

    lower.translate(Vector(mount_ox, mount_oy, MOUNT_Z))
    upper.translate(Vector(mount_ox, mount_oy, MOUNT_Z + LO_H))

    lid_z = MOUNT_Z + LO_H + HI_H
    lid.translate(Vector(mount_ox, mount_oy, lid_z))

    mast.translate(Vector(mount_ox + CX, mount_oy + CY, lid_z + FLOOR))

    cam.translate(Vector(mount_ox, mount_oy, MOUNT_Z))

    # RTK in nav slot 1
    rtk_sx, rtk_sy = CX - 25, CY + 30
    rtk.translate(Vector(mount_ox + rtk_sx - RTK_L / 2,
                         mount_oy + rtk_sy - RTK_W / 2,
                         lid_z + FLOOR + 3))

    # DroneCAN M9N on GPS mast plate
    m9n.translate(Vector(mount_ox + CX,
                         mount_oy + CY,
                         lid_z + FLOOR + GPS_MAST_H + 3))

    # PDB between frame plates (centered)
    pdb_tray_l = PDB_L + 2 * 1.0 + 2 * 3.0
    pdb_tray_w = PDB_W + 2 * 1.0 + 2 * 3.0
    pdb.translate(Vector(FRM_W / 2 - pdb_tray_l / 2,
                         FRM_W / 2 - pdb_tray_w / 2,
                         FRM_T))

    # Battery sled under bottom plate — CENTERED for proper CoM
    # Cross-support rails bridge the center cutout to support the sled
    bat_sled_l = BAT_L + 2 * 0.5 + 2 * 3.0   # updated gap=0.5
    bat_sled_w = BAT_W + 2 * 0.5 + 2 * 3.0
    bat_sled_h = 3.0 + BAT_H + 2
    bat_y = FRM_W / 2 - bat_sled_w / 2  # centered on frame
    bat.translate(Vector(FRM_W / 2 - bat_sled_l / 2,
                         bat_y,
                         -bat_sled_h))

    # ESC cradles on arms (4 copies, rotated to match arm directions)
    arm_z = FRM_T + FRM_GAP / 2
    esc_arm_offset = 35.0  # distance along arm from plate corner
    esc_items = []
    arm_configs = [
        (FRM_W, FRM_W, 1, 1, 45),
        (0, FRM_W, -1, 1, 135),
        (0, 0, -1, -1, 225),
        (FRM_W, 0, 1, -1, 315),
    ]
    for i, (acx, acy, dx, dy, angle) in enumerate(arm_configs):
        e = create_esc_cradle()
        e.rotate(Vector(0, 0, 0), Vector(0, 0, 1), angle)
        ex = acx + dx * esc_arm_offset / math.sqrt(2)
        ey = acy + dy * esc_arm_offset / math.sqrt(2)
        e.translate(Vector(ex, ey, arm_z))
        esc_items.append((f"ESC_Cradle_{['NE','NW','SW','SE'][i]}", e))

    # ── Add to document ──
    items = [
        ("X500V2_Frame", frame, (0.15, 0.15, 0.18), 30),
        ("LowerBay_Pixhawk", lower, (0.22, 0.38, 0.72), 0),
        ("UpperBay_Electronics", upper, (0.28, 0.62, 0.30), 0),
        ("TopLid", lid, (0.80, 0.80, 0.25), 0),
        ("GPS_Mast", mast, (0.60, 0.60, 0.62), 0),
        ("DroneCAN_M9N", m9n, (0.10, 0.50, 0.10), 0),
        ("CameraMount_AR0234", cam, (0.72, 0.30, 0.18), 0),
        ("RTK_Module", rtk, (0.10, 0.55, 0.10), 0),
        ("PDB_Tray", pdb, (0.70, 0.15, 0.15), 0),
        ("Battery_Sled", bat, (0.85, 0.55, 0.10), 0),
    ]
    for name, shape, color, alpha in items:
        obj = doc.addObject("Part::Feature", name)
        obj.Shape = shape
        viz(obj, color, alpha)

    for name, shape in esc_items:
        obj = doc.addObject("Part::Feature", name)
        obj.Shape = shape
        viz(obj, (0.80, 0.70, 0.15), 0)

    doc.recompute()

    total_h = lid_z + FLOOR + GPS_MAST_H + 3 + GPS_THICK
    print("=" * 60)
    print("OLYMPUS MOUNT v5.0 - MODULAR SNAP-FIT SYSTEM")
    print("=" * 60)
    print(f"Frame:        Holybro X500 V2 ({FRM_W}x{FRM_W}mm, 500mm wheelbase)")
    print(f"Lower Bay:    {OUTER_L}x{OUTER_W}x{LO_H}mm  Pixhawk snap cradle")
    print(f"Upper Bay:    {OUTER_L}x{OUTER_W}x{HI_H}mm  Jetson + LoRa + ELRS")
    print(f"Top Lid:      {OUTER_L}x{OUTER_W}x{FLOOR}mm  GPS + RTK + nav slot")
    print(f"GPS Mast:     {GPS_TUBE_D}mm tube x {GPS_MAST_H}mm, DroneCAN M9N ({GPS_DIA}mm)")
    print(f"Camera:       AR0234 nadir ({CAM_DROP}mm drop)")
    print(f"PDB:          Holybro 300A Side Entry ({PDB_L}x{PDB_W}mm) snap tray")
    print(f"Battery:      HRB 4S 5000mAh ({BAT_L}x{BAT_W}x{BAT_H}mm) slide-in sled")
    print(f"ESCs:         4x Readytosky 40A ({ESC_L}x{ESC_W}mm) arm cradles")
    print(f"RTK:          {RTK_L}x{RTK_W}x{RTK_H}mm snap-in nav slot")
    print(f"Snap-Fit:     ridges {SNAP_D}mm + thumb reliefs on all cradles")
    print(f"Total height: ~{total_h:.0f}mm (frame base to GPS top)")
    print(f"Parts:        14 (6 printable + 4 ESC cradles + 4 visual)")

    return doc


def export_files(doc):
    xdir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir()
        else os.getcwd(),
        "exports"
    )
    os.makedirs(xdir, exist_ok=True)

    for obj in doc.Objects:
        if hasattr(obj, 'Shape') and obj.Shape.Solids:
            path = os.path.join(xdir, f"{obj.Name}.stl")
            obj.Shape.exportStl(path)
            print(f"  STL: {path}")

    shapes = [o.Shape for o in doc.Objects
              if hasattr(o, 'Shape') and o.Shape.Solids]
    if shapes:
        compound = Part.makeCompound(shapes)
        step_path = os.path.join(xdir, "OlympusMount_v5_Assembly.step")
        compound.exportStep(step_path)
        print(f"  STEP: {step_path}")

    fcstd = os.path.join(xdir, "OlympusMount_v5.FCStd")
    doc.saveAs(fcstd)
    print(f"  FCStd: {fcstd}")


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__" or True:
    doc = assemble()
    export_files(doc)

    try:
        import FreeCADGui
        FreeCADGui.activeDocument().activeView().viewIsometric()
        FreeCADGui.SendMsgToActiveView("ViewFit")
    except Exception:
        pass
