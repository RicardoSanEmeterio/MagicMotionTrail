"""Trail data cache with evaluation, in-place updates, and re-entrance guard.

This module owns the authoritative trail data.  All other modules read from
``get_all()`` and mutate through the public API only.
"""

import traceback
import bpy
from mathutils import Vector
from . import fcurve_utils
from .preferences import get_prefs, DEFAULT_FRAMES_BEFORE, DEFAULT_FRAMES_AFTER

# ── cache state ──────────────────────────────────────────────────────────────

_cache: dict = {}
_dirty: bool = False
_is_evaluating: bool = False

# Shared mutable state read by drawing.py for the edit-mode overlay.
edit_state = {
    "active": False,
    "brush_mode": "MOVE",
    "mouse_pos": (0, 0),
    "radius": 50.0,
    "falloff_mode": "TEMPORAL",
    "falloff_curve": "SMOOTH",
}


# ── TrailData ────────────────────────────────────────────────────────────────

class TrailData:
    """Evaluated world-space positions for one object / bone trail."""
    __slots__ = ("obj_name", "bone_name", "positions", "keyframe_frames")

    def __init__(self, obj_name: str, bone_name: str = ""):
        self.obj_name = obj_name
        self.bone_name = bone_name
        self.positions: list[tuple[int, Vector]] = []
        self.keyframe_frames: set[int] = set()


# ── public helpers ───────────────────────────────────────────────────────────

def make_key(obj, bone_name: str = "") -> tuple[str, str]:
    return (obj.name, bone_name)


def get_all() -> dict:
    return _cache


def is_dirty() -> bool:
    return _dirty


def mark_dirty():
    global _dirty
    _dirty = True


def clear_dirty():
    global _dirty
    _dirty = False


# ── evaluation ───────────────────────────────────────────────────────────────

def evaluate_trail(obj, bone_name: str = "") -> TrailData:
    """Build a TrailData by stepping through frames with ``scene.frame_set``.

    **Must never be called from inside a handler** – only from the debounce
    timer or from explicit user actions (refresh / toggle / drag-finish).
    """
    global _is_evaluating
    if _is_evaluating:
        return _cache.get(make_key(obj, bone_name), TrailData(obj.name, bone_name))

    _is_evaluating = True
    try:
        return _evaluate_trail_inner(obj, bone_name)
    finally:
        _is_evaluating = False


def _evaluate_trail_inner(obj, bone_name: str) -> TrailData:
    prefs = get_prefs()
    before = getattr(prefs, "trail_frames_before", DEFAULT_FRAMES_BEFORE)
    after = getattr(prefs, "trail_frames_after", DEFAULT_FRAMES_AFTER)

    scene = bpy.context.scene
    current = scene.frame_current
    start = current - before
    end = current + after

    trail = TrailData(obj.name, bone_name)

    channelbag = fcurve_utils.get_channelbag_for_object(obj)
    if channelbag is not None:
        for fc, _ in fcurve_utils.get_location_fcurves(channelbag, bone_name):
            for kp in fc.keyframe_points:
                fr = int(round(kp.co[0]))
                if start <= fr <= end:
                    trail.keyframe_frames.add(fr)

    saved_frame = current
    saved_sub = scene.frame_subframe
    try:
        for f in range(start, end + 1):
            scene.frame_set(f)
            if bone_name and obj.type == "ARMATURE":
                pb = obj.pose.bones.get(bone_name)
                if pb:
                    pos = obj.matrix_world @ pb.matrix.translation
                else:
                    pos = obj.matrix_world.translation.copy()
            else:
                pos = obj.matrix_world.translation.copy()
            trail.positions.append((f, Vector(pos)))
    finally:
        scene.frame_set(saved_frame, subframe=saved_sub)

    return trail


# ── cache operations ─────────────────────────────────────────────────────────

def toggle(obj, bone_name: str = "") -> bool:
    """Toggle a trail on/off.  Returns True if the trail is now visible."""
    key = make_key(obj, bone_name)
    if key in _cache:
        del _cache[key]
        return False
    _cache[key] = evaluate_trail(obj, bone_name)
    return True


def refresh_all():
    """Re-evaluate every cached trail.  Safe to call from the timer."""
    global _dirty
    _dirty = False

    stale = [k for k in _cache if bpy.data.objects.get(k[0]) is None]
    for k in stale:
        del _cache[k]

    for key in list(_cache.keys()):
        obj_name, bone_name = key
        obj = bpy.data.objects.get(obj_name)
        if obj is None:
            continue
        try:
            _cache[key] = evaluate_trail(obj, bone_name)
        except Exception:
            traceback.print_exc()

    _tag_3d_redraw()


def clear_all():
    _cache.clear()
    edit_state["active"] = False
    _tag_3d_redraw()


# ── in-place position updates (used during drag – no frame_set) ─────────────

def update_positions_inplace(trail_key, frame_deltas: dict[int, Vector]):
    """Shift cached positions for *trail_key* by the given per-frame deltas.

    *frame_deltas* maps ``frame_number -> world-space delta Vector``.
    This is a lightweight operation that avoids ``frame_set`` entirely.
    """
    trail = _cache.get(trail_key)
    if trail is None:
        return
    new_positions = []
    for f, pos in trail.positions:
        delta = frame_deltas.get(f)
        if delta is not None:
            new_positions.append((f, pos + delta))
        else:
            new_positions.append((f, pos))
    trail.positions = new_positions


# ── internal helpers ─────────────────────────────────────────────────────────

def _tag_3d_redraw():
    try:
        for area in bpy.context.screen.areas if bpy.context.screen else []:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    except Exception:
        pass
