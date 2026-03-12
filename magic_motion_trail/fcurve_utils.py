"""Blender 5.0 FCurve utilities.

Self-contained module with no imports from other addon modules.
Handles the layered animation system introduced in Blender 5.0.
"""

import bpy


def get_channelbag_for_object(obj):
    """Get the channelbag holding FCurves for *obj* in Blender 5.0.

    Returns None if the object has no animation data or no valid slot.
    """
    if obj is None or obj.animation_data is None:
        return None
    action = obj.animation_data.action
    if action is None:
        return None

    slot = obj.animation_data.action_slot
    if slot is None:
        return None

    try:
        from bpy_extras.anim_utils import action_get_channelbag_for_slot
        return action_get_channelbag_for_slot(action, slot)
    except ImportError:
        pass

    for layer in action.layers:
        for strip in layer.strips:
            for cb in strip.channelbags:
                if cb.slot_handle == slot.handle:
                    return cb
    return None


def get_location_fcurves(channelbag, bone_name=""):
    """Yield (fcurve, array_index) for location FCurves relevant to *bone_name*.

    If *bone_name* is empty, yields object-level ``location`` curves.
    """
    if channelbag is None:
        return

    for fc in channelbag.fcurves:
        if fc.hide or fc.lock:
            continue
        dp = fc.data_path
        if bone_name:
            if (f'["{bone_name}"]' in dp or f"['{bone_name}']" in dp) and "location" in dp:
                yield fc, fc.array_index
        else:
            if dp == "location":
                yield fc, fc.array_index
