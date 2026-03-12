"""GPU drawing for motion trails: 3D lines/points and 2D overlays."""

import math
import bpy
import gpu
import blf
import traceback
from gpu_extras.batch import batch_for_shader
from bpy_extras.view3d_utils import location_3d_to_region_2d

from . import trail_cache
from .preferences import get_prefs, DEFAULT_OPACITY_FALLOFF

# ── draw handle storage ─────────────────────────────────────────────────────

_handle_3d = None
_handle_2d = None

# ── colours ──────────────────────────────────────────────────────────────────

COLOR_PAST = (0.2, 0.6, 1.0)
COLOR_CURRENT = (1.0, 1.0, 0.0)
COLOR_FUTURE = (1.0, 0.3, 0.3)
COLOR_TRAIL_LINE = (0.7, 0.7, 0.7)

BRUSH_COLORS = {
    "MOVE": (0.2, 0.8, 1.0, 0.35),
    "SMOOTH": (0.2, 1.0, 0.4, 0.35),
}


# ── 3D drawing ───────────────────────────────────────────────────────────────

def _draw_3d_callback():
    try:
        _draw_3d_inner()
    except Exception:
        traceback.print_exc()


def _draw_3d_inner():
    cache = trail_cache.get_all()
    if not cache:
        return

    prefs = get_prefs()
    falloff_strength = getattr(prefs, "trail_opacity_falloff", DEFAULT_OPACITY_FALLOFF)
    point_size = getattr(prefs, "trail_point_size", 6.0)
    line_width = getattr(prefs, "trail_line_width", 2.0)

    scene = bpy.context.scene
    current_frame = scene.frame_current

    gpu.state.blend_set("ALPHA")
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")

    for trail in cache.values():
        if not trail.positions:
            continue

        max_dist = max(
            abs(trail.positions[0][0] - current_frame),
            abs(trail.positions[-1][0] - current_frame),
            1,
        )

        _draw_trail_lines(shader, trail, current_frame, max_dist, falloff_strength, line_width)
        _draw_keyframe_dots(shader, trail, current_frame, max_dist, falloff_strength, point_size)

    gpu.state.blend_set("NONE")


def _opacity_for_frame(frame, current_frame, max_dist, falloff_strength):
    dist = abs(frame - current_frame)
    t = dist / max_dist if max_dist else 0.0
    return max(1.0 - t * falloff_strength, 0.05)


def _draw_trail_lines(shader, trail, current_frame, max_dist, falloff_strength, line_width):
    """Draw trail as multiple bucketed segments for proper opacity fading."""
    gpu.state.line_width_set(line_width)

    n_buckets = 8
    buckets: list[list] = [[] for _ in range(n_buckets)]

    positions = trail.positions
    for i in range(len(positions) - 1):
        f0, p0 = positions[i]
        f1, p1 = positions[i + 1]
        avg_f = (f0 + f1) / 2.0
        alpha = _opacity_for_frame(avg_f, current_frame, max_dist, falloff_strength)
        bucket_idx = min(int(alpha * n_buckets), n_buckets - 1)
        buckets[bucket_idx].extend([p0, p1])

    for bi, verts in enumerate(buckets):
        if not verts:
            continue
        alpha = (bi + 0.5) / n_buckets
        batch = batch_for_shader(shader, "LINES", {"pos": verts})
        shader.bind()
        shader.uniform_float("color", (*COLOR_TRAIL_LINE, alpha))
        batch.draw(shader)

    gpu.state.line_width_set(1.0)


def _draw_keyframe_dots(shader, trail, current_frame, max_dist, falloff_strength, point_size):
    """Draw keyframe points with distance-based colour and opacity."""
    gpu.state.point_size_set(point_size)

    for f, pos in trail.positions:
        if f not in trail.keyframe_frames:
            continue
        alpha = _opacity_for_frame(f, current_frame, max_dist, falloff_strength)
        if f < current_frame:
            color = (*COLOR_PAST, alpha)
        elif f == current_frame:
            color = (*COLOR_CURRENT, alpha)
        else:
            color = (*COLOR_FUTURE, alpha)

        batch = batch_for_shader(shader, "POINTS", {"pos": [pos]})
        shader.bind()
        shader.uniform_float("color", color)
        batch.draw(shader)

    gpu.state.point_size_set(1.0)


# ── 2D drawing ───────────────────────────────────────────────────────────────

def _draw_2d_callback():
    try:
        _draw_2d_inner()
    except Exception:
        traceback.print_exc()


def _draw_2d_inner():
    es = trail_cache.edit_state
    cache = trail_cache.get_all()
    if not cache:
        return

    region = bpy.context.region
    rv3d = bpy.context.region_data
    if region is None or rv3d is None:
        return

    scene = bpy.context.scene
    current_frame = scene.frame_current

    _draw_frame_labels(cache, region, rv3d, current_frame)

    if es["active"]:
        _draw_falloff_circle(es)
        _draw_brush_info_under_cursor(es)


def _draw_frame_labels(cache, region, rv3d, current_frame):
    """Draw frame numbers at keyframe positions."""
    font_id = 0
    blf.size(font_id, 11)
    blf.color(font_id, 0.9, 0.9, 0.9, 0.8)

    for trail in cache.values():
        for f, pos in trail.positions:
            if f not in trail.keyframe_frames:
                continue
            co2d = location_3d_to_region_2d(region, rv3d, pos)
            if co2d is None:
                continue
            blf.position(font_id, co2d.x + 8, co2d.y + 8, 0)
            blf.draw(font_id, str(f))


def _draw_falloff_circle(es):
    """Draw a circle around the mouse for brush radius feedback."""
    mx, my = es["mouse_pos"]
    radius = es["radius"]
    mode = es["brush_mode"]
    color = BRUSH_COLORS.get(mode, (0.5, 0.5, 0.5, 0.35))

    segments = 48
    gpu.state.blend_set("ALPHA")
    gpu.state.line_width_set(2.0)

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts = []
    for i in range(segments):
        angle = 2.0 * math.pi * i / segments
        angle_next = 2.0 * math.pi * (i + 1) / segments
        x0 = mx + radius * math.cos(angle)
        y0 = my + radius * math.sin(angle)
        x1 = mx + radius * math.cos(angle_next)
        y1 = my + radius * math.sin(angle_next)
        verts.extend([(x0, y0), (x1, y1)])

    batch = batch_for_shader(shader, "LINES", {"pos": verts})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)

    gpu.state.line_width_set(1.0)
    gpu.state.blend_set("NONE")


def _draw_brush_info_under_cursor(es):
    """Show brush mode, falloff curve, and hotkey hints just below the brush circle."""
    mx, my = es["mouse_pos"]
    radius = es["radius"]
    mode = es["brush_mode"]
    falloff_mode = es.get("falloff_mode", "TEMPORAL")
    falloff_curve = es.get("falloff_curve", "SMOOTH")
    color = BRUSH_COLORS.get(mode, (0.5, 0.5, 0.5, 1.0))

    font_id = 0
    label_y = my - radius - 18

    blf.size(font_id, 14)
    blf.color(font_id, *color)
    mode_text = f"{mode}  [{falloff_curve.title()}]"
    blf.position(font_id, mx - _text_width(font_id, mode_text) / 2, label_y, 0)
    blf.draw(font_id, mode_text)

    blf.size(font_id, 11)
    blf.color(font_id, 0.75, 0.75, 0.75, 0.7)
    info_text = f"Falloff: {falloff_mode.title()}  |  S: brush  F: mode  O: curve"
    label_y -= 16
    blf.position(font_id, mx - _text_width(font_id, info_text) / 2, label_y, 0)
    blf.draw(font_id, info_text)


def _text_width(font_id, text):
    """Measure the pixel width of *text* at the current blf font size."""
    return blf.dimensions(font_id, text)[0]


# ── registration ─────────────────────────────────────────────────────────────

def register_draw_handlers():
    global _handle_3d, _handle_2d
    if _handle_3d is None:
        _handle_3d = bpy.types.SpaceView3D.draw_handler_add(
            _draw_3d_callback, (), "WINDOW", "POST_VIEW",
        )
    if _handle_2d is None:
        _handle_2d = bpy.types.SpaceView3D.draw_handler_add(
            _draw_2d_callback, (), "WINDOW", "POST_PIXEL",
        )


def unregister_draw_handlers():
    global _handle_3d, _handle_2d
    if _handle_3d is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle_3d, "WINDOW")
        _handle_3d = None
    if _handle_2d is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle_2d, "WINDOW")
        _handle_2d = None
