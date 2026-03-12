"""Interactive modal operator for editing motion trail keyframe points.

Two brush modes:
    MOVE   – drag keyframe points with proportional falloff.
    SMOOTH – blend keyframe values toward their neighbor average.

Falloff interpolation curves (cycle with O key):
    SMOOTH, SPHERE, ROOT, SHARP, LINEAR, CONSTANT
    — matching Blender's proportional editing curves.

Critical stability rules respected here:
    * ``frame_set()`` is NEVER called during a drag stroke.
    * Cached positions are updated in-place with simple vector math.
    * Full re-evaluation only happens on stroke release.
    * All mutable state is initialised inside ``invoke()``.
"""

import math
import traceback

import bpy
from mathutils import Vector
from bpy_extras.view3d_utils import location_3d_to_region_2d

from . import trail_cache
from . import fcurve_utils

_MAX_UNDO = 50

FALLOFF_CURVES = ["SMOOTH", "SPHERE", "ROOT", "SHARP", "LINEAR", "CONSTANT"]


class MMT_OT_edit_trail(bpy.types.Operator):
    bl_idname = "mmt.edit_trail"
    bl_label = "Edit Trail Points"
    bl_description = "Interactively edit motion trail keyframe positions"
    bl_options = {"REGISTER"}

    # ── invoke ───────────────────────────────────────────────────────────

    def invoke(self, context, event):
        cache = trail_cache.get_all()
        if not cache:
            self.report({"WARNING"}, "No active trails")
            return {"CANCELLED"}

        self._brush_mode = "MOVE"
        self._falloff_mode = "TEMPORAL"
        self._falloff_curve = "SMOOTH"
        self._radius = 50.0
        self._dragging = False
        self._drag_start = Vector((0, 0))
        self._drag_frame = -1
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._smooth_strength = 0.15

        trail_cache.edit_state["active"] = True
        trail_cache.edit_state["brush_mode"] = self._brush_mode
        trail_cache.edit_state["radius"] = self._radius
        trail_cache.edit_state["falloff_mode"] = self._falloff_mode
        trail_cache.edit_state["falloff_curve"] = self._falloff_curve
        trail_cache.edit_state["mouse_pos"] = (event.mouse_region_x, event.mouse_region_y)

        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {"RUNNING_MODAL"}

    # ── modal ────────────────────────────────────────────────────────────

    def modal(self, context, event):
        try:
            return self._modal_inner(context, event)
        except Exception:
            traceback.print_exc()
            self._finish(context)
            return {"CANCELLED"}

    def _modal_inner(self, context, event):
        trail_cache.edit_state["mouse_pos"] = (event.mouse_region_x, event.mouse_region_y)
        context.area.tag_redraw()

        # ── Scroll wheel → radius ────────────────────────────────────
        if event.type == "WHEELUPMOUSE":
            self._radius = min(self._radius + 8, 400)
            trail_cache.edit_state["radius"] = self._radius
            return {"RUNNING_MODAL"}
        if event.type == "WHEELDOWNMOUSE":
            self._radius = max(self._radius - 8, 10)
            trail_cache.edit_state["radius"] = self._radius
            return {"RUNNING_MODAL"}

        # ── S → toggle brush mode ────────────────────────────────────
        if event.type == "S" and event.value == "PRESS" and not self._dragging:
            self._brush_mode = "SMOOTH" if self._brush_mode == "MOVE" else "MOVE"
            trail_cache.edit_state["brush_mode"] = self._brush_mode
            return {"RUNNING_MODAL"}

        # ── F → cycle falloff mode ───────────────────────────────────
        if event.type == "F" and event.value == "PRESS" and not self._dragging:
            modes = ["TEMPORAL", "SPATIAL", "HYBRID"]
            idx = (modes.index(self._falloff_mode) + 1) % len(modes)
            self._falloff_mode = modes[idx]
            trail_cache.edit_state["falloff_mode"] = self._falloff_mode
            return {"RUNNING_MODAL"}

        # ── O → cycle falloff interpolation curve ────────────────────
        if event.type == "O" and event.value == "PRESS" and not self._dragging:
            idx = (FALLOFF_CURVES.index(self._falloff_curve) + 1) % len(FALLOFF_CURVES)
            self._falloff_curve = FALLOFF_CURVES[idx]
            trail_cache.edit_state["falloff_curve"] = self._falloff_curve
            return {"RUNNING_MODAL"}

        # ── Ctrl+Z / Ctrl+Shift+Z → undo / redo ─────────────────────
        if event.type == "Z" and event.value == "PRESS" and event.ctrl:
            if event.shift:
                self._do_redo(context)
            else:
                self._do_undo(context)
            return {"RUNNING_MODAL"}

        # ── LMB press → begin stroke ─────────────────────────────────
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            self._begin_stroke(context, event)
            return {"RUNNING_MODAL"}

        # ── Mouse move during drag ───────────────────────────────────
        if event.type == "MOUSEMOVE" and self._dragging:
            self._continue_stroke(context, event)
            return {"RUNNING_MODAL"}

        # ── LMB release → end stroke ─────────────────────────────────
        if event.type == "LEFTMOUSE" and event.value == "RELEASE" and self._dragging:
            self._end_stroke(context)
            return {"RUNNING_MODAL"}

        # ── RMB / ESC → exit edit mode ───────────────────────────────
        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            if self._dragging:
                self._cancel_stroke(context)
            self._finish(context)
            return {"FINISHED"}

        return {"PASS_THROUGH"}

    # ── stroke logic ─────────────────────────────────────────────────────

    def _begin_stroke(self, context, event):
        self._push_undo()
        self._dragging = True
        self._drag_start = Vector((event.mouse_region_x, event.mouse_region_y))

        if self._brush_mode == "MOVE":
            self._drag_frame = self._find_closest_key_frame(context, event)

    def _continue_stroke(self, context, event):
        if self._brush_mode == "MOVE":
            self._apply_move(context, event)
        else:
            self._apply_smooth(context, event)

    def _end_stroke(self, context):
        self._dragging = False
        self._drag_frame = -1
        trail_cache.refresh_all()
        context.area.tag_redraw()

    def _cancel_stroke(self, context):
        self._dragging = False
        self._drag_frame = -1
        self._do_undo(context)

    # ── MOVE brush ───────────────────────────────────────────────────────

    def _apply_move(self, context, event):
        """Move keyframe points by the mouse delta, with proportional falloff."""
        if self._drag_frame < 0:
            return

        region = context.region
        rv3d = context.region_data
        if region is None or rv3d is None:
            return

        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        delta_2d = mouse - self._drag_start
        self._drag_start = mouse

        cache = trail_cache.get_all()
        for trail_key, trail in cache.items():
            obj = bpy.data.objects.get(trail.obj_name)
            if obj is None:
                continue
            channelbag = fcurve_utils.get_channelbag_for_object(obj)
            if channelbag is None:
                continue

            frame_deltas: dict[int, Vector] = {}
            center_2d = None

            for f, pos in trail.positions:
                if f not in trail.keyframe_frames:
                    continue
                co2d = location_3d_to_region_2d(region, rv3d, pos)
                if co2d is None:
                    continue
                if f == self._drag_frame:
                    center_2d = co2d

            if center_2d is None:
                continue

            for f, pos in trail.positions:
                if f not in trail.keyframe_frames:
                    continue
                co2d = location_3d_to_region_2d(region, rv3d, pos)
                if co2d is None:
                    continue
                weight = self._falloff_weight(f, co2d, center_2d)
                if weight <= 0:
                    continue

                world_delta = self._screen_delta_to_world(
                    context, pos, delta_2d * weight,
                )
                self._apply_fcurve_delta(channelbag, trail.bone_name, f, world_delta)
                frame_deltas[f] = world_delta

            trail_cache.update_positions_inplace(trail_key, frame_deltas)

        context.area.tag_redraw()

    # ── SMOOTH brush ─────────────────────────────────────────────────────

    def _apply_smooth(self, context, event):
        """Blend keyframes toward the average of their temporal neighbors."""
        region = context.region
        rv3d = context.region_data
        if region is None or rv3d is None:
            return

        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        cache = trail_cache.get_all()

        for trail_key, trail in cache.items():
            obj = bpy.data.objects.get(trail.obj_name)
            if obj is None:
                continue
            channelbag = fcurve_utils.get_channelbag_for_object(obj)
            if channelbag is None:
                continue

            fcurves = list(fcurve_utils.get_location_fcurves(channelbag, trail.bone_name))
            if not fcurves:
                continue

            frame_deltas: dict[int, Vector] = {}

            for f, pos in trail.positions:
                if f not in trail.keyframe_frames:
                    continue
                co2d = location_3d_to_region_2d(region, rv3d, pos)
                if co2d is None:
                    continue
                dist = (co2d - mouse).length
                if dist > self._radius:
                    continue

                t = dist / self._radius if self._radius > 0 else 1.0
                raw_falloff = self._apply_curve(1.0 - t)
                weight = raw_falloff * self._smooth_strength

                if weight <= 0:
                    continue

                delta = Vector((0.0, 0.0, 0.0))
                for fc, axis_idx in fcurves:
                    kp = _find_keypoint_at_frame(fc, f)
                    if kp is None:
                        continue
                    current_val = kp.co[1]
                    left_val = fc.evaluate(f - 1)
                    right_val = fc.evaluate(f + 1)
                    avg = (left_val + right_val) * 0.5
                    new_val = current_val + (avg - current_val) * weight
                    kp.co[1] = new_val
                    kp.handle_left[1] += (new_val - current_val)
                    kp.handle_right[1] += (new_val - current_val)
                    delta[axis_idx] = new_val - current_val

                if delta.length > 0:
                    world_delta = self._local_delta_to_world(obj, trail.bone_name, delta)
                    frame_deltas[f] = world_delta

            if frame_deltas:
                trail_cache.update_positions_inplace(trail_key, frame_deltas)

        context.area.tag_redraw()

    # ── falloff ──────────────────────────────────────────────────────────

    def _apply_curve(self, f):
        """Apply the active interpolation curve to a 0-1 value.

        ``f`` = 1.0 at the center, 0.0 at the edge — matching Blender's
        proportional editing convention.
        """
        curve = self._falloff_curve
        if f <= 0:
            return 0.0
        if f >= 1:
            return 1.0
        if curve == "SMOOTH":
            return 3.0 * f * f - 2.0 * f * f * f
        if curve == "SPHERE":
            return math.sqrt(f * (2.0 - f))
        if curve == "ROOT":
            return math.sqrt(f)
        if curve == "SHARP":
            return f * f * f
        if curve == "LINEAR":
            return f
        if curve == "CONSTANT":
            return 1.0
        return f

    def _falloff_weight(self, frame, point_2d, center_2d):
        """Compute a 0-1 weight using the active falloff mode + curve."""
        if self._falloff_mode == "TEMPORAL":
            return self._temporal_falloff(frame)
        elif self._falloff_mode == "SPATIAL":
            return self._spatial_falloff(point_2d, center_2d)
        else:
            t = self._temporal_falloff(frame)
            s = self._spatial_falloff(point_2d, center_2d)
            return t * s

    def _temporal_falloff(self, frame):
        dist = abs(frame - self._drag_frame)
        key_radius = self._radius / 5.0
        if key_radius < 1:
            key_radius = 1
        if dist > key_radius:
            return 0.0
        t = dist / key_radius
        return self._apply_curve(1.0 - t)

    def _spatial_falloff(self, point_2d, center_2d):
        dist = (point_2d - center_2d).length
        if dist > self._radius:
            return 0.0
        t = dist / self._radius
        return self._apply_curve(1.0 - t)

    # ── helpers ──────────────────────────────────────────────────────────

    def _find_closest_key_frame(self, context, event):
        """Find the keyframe frame number closest to the mouse click."""
        region = context.region
        rv3d = context.region_data
        if region is None or rv3d is None:
            return -1

        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        best_frame = -1
        best_dist = float("inf")

        for trail in trail_cache.get_all().values():
            for f, pos in trail.positions:
                if f not in trail.keyframe_frames:
                    continue
                co2d = location_3d_to_region_2d(region, rv3d, pos)
                if co2d is None:
                    continue
                d = (co2d - mouse).length
                if d < best_dist and d < self._radius:
                    best_dist = d
                    best_frame = f

        return best_frame

    def _screen_delta_to_world(self, context, origin_3d, delta_2d):
        """Convert a 2D screen-space delta into a 3D world-space delta."""
        from bpy_extras.view3d_utils import region_2d_to_location_3d

        region = context.region
        rv3d = context.region_data
        origin_2d = location_3d_to_region_2d(region, rv3d, origin_3d)
        if origin_2d is None:
            return Vector((0, 0, 0))

        dest_2d = origin_2d + delta_2d
        dest_3d = region_2d_to_location_3d(region, rv3d, dest_2d, origin_3d)
        return dest_3d - origin_3d

    def _local_delta_to_world(self, obj, bone_name, local_delta):
        """Convert a local-space FCurve delta into world-space for cache update."""
        if bone_name and obj.type == "ARMATURE":
            pb = obj.pose.bones.get(bone_name)
            if pb:
                mat = obj.matrix_world @ pb.bone.matrix_local
                return (mat.to_3x3() @ local_delta)
        return obj.matrix_world.to_3x3() @ local_delta

    def _apply_fcurve_delta(self, channelbag, bone_name, frame, world_delta):
        """Apply a world-space delta back to location FCurves at *frame*."""
        obj_name = None
        for trail in trail_cache.get_all().values():
            obj_name = trail.obj_name
            break
        if obj_name is None:
            return

        obj = bpy.data.objects.get(obj_name)
        if obj is None:
            return

        if bone_name and obj.type == "ARMATURE":
            pb = obj.pose.bones.get(bone_name)
            if pb:
                mat = obj.matrix_world @ pb.bone.matrix_local
                local_delta = mat.to_3x3().inverted_safe() @ world_delta
            else:
                local_delta = obj.matrix_world.to_3x3().inverted_safe() @ world_delta
        else:
            local_delta = obj.matrix_world.to_3x3().inverted_safe() @ world_delta

        for fc, axis_idx in fcurve_utils.get_location_fcurves(channelbag, bone_name):
            kp = _find_keypoint_at_frame(fc, frame)
            if kp is not None:
                d = local_delta[axis_idx]
                kp.co[1] += d
                kp.handle_left[1] += d
                kp.handle_right[1] += d

    # ── undo / redo ──────────────────────────────────────────────────────

    def _push_undo(self):
        snapshot = self._snapshot_fcurves()
        if snapshot:
            self._undo_stack.append(snapshot)
            if len(self._undo_stack) > _MAX_UNDO:
                self._undo_stack.pop(0)
            self._redo_stack.clear()

    def _do_undo(self, context):
        if not self._undo_stack:
            return
        self._redo_stack.append(self._snapshot_fcurves())
        snap = self._undo_stack.pop()
        self._restore_fcurves(snap)
        trail_cache.refresh_all()
        context.area.tag_redraw()

    def _do_redo(self, context):
        if not self._redo_stack:
            return
        self._undo_stack.append(self._snapshot_fcurves())
        snap = self._redo_stack.pop()
        self._restore_fcurves(snap)
        trail_cache.refresh_all()
        context.area.tag_redraw()

    def _snapshot_fcurves(self) -> dict:
        """Save current keyframe values for all trailed FCurves."""
        snapshot = {}
        for trail_key, trail in trail_cache.get_all().items():
            obj = bpy.data.objects.get(trail.obj_name)
            if obj is None:
                continue
            cb = fcurve_utils.get_channelbag_for_object(obj)
            if cb is None:
                continue
            for fc, _ in fcurve_utils.get_location_fcurves(cb, trail.bone_name):
                fc_id = (trail.obj_name, fc.data_path, fc.array_index)
                vals = []
                for kp in fc.keyframe_points:
                    vals.append((
                        kp.co[0], kp.co[1],
                        kp.handle_left[0], kp.handle_left[1],
                        kp.handle_right[0], kp.handle_right[1],
                    ))
                snapshot[fc_id] = vals
        return snapshot

    def _restore_fcurves(self, snapshot: dict):
        for fc_id, vals in snapshot.items():
            obj_name, data_path, array_index = fc_id
            obj = bpy.data.objects.get(obj_name)
            if obj is None:
                continue
            cb = fcurve_utils.get_channelbag_for_object(obj)
            if cb is None:
                continue
            for fc in cb.fcurves:
                if fc.data_path == data_path and fc.array_index == array_index:
                    for i, kp in enumerate(fc.keyframe_points):
                        if i >= len(vals):
                            break
                        co_x, co_y, hl_x, hl_y, hr_x, hr_y = vals[i]
                        kp.co[0] = co_x
                        kp.co[1] = co_y
                        kp.handle_left[0] = hl_x
                        kp.handle_left[1] = hl_y
                        kp.handle_right[0] = hr_x
                        kp.handle_right[1] = hr_y
                    fc.update()
                    break

    # ── exit ─────────────────────────────────────────────────────────────

    def _finish(self, context):
        trail_cache.edit_state["active"] = False
        trail_cache.refresh_all()
        try:
            context.area.tag_redraw()
        except Exception:
            pass


# ── helpers ──────────────────────────────────────────────────────────────────

def _find_keypoint_at_frame(fc, frame, tolerance=0.5):
    """Return the ``KeyframePoint`` closest to *frame*, or None."""
    for kp in fc.keyframe_points:
        if abs(kp.co[0] - frame) <= tolerance:
            return kp
    return None


_classes = (MMT_OT_edit_trail,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
