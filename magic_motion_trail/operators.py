"""Simple trail operators: toggle, refresh, clear."""

import bpy
import traceback
from . import trail_cache


class MMT_OT_toggle(bpy.types.Operator):
    bl_idname = "mmt.toggle_trail"
    bl_label = "Toggle Motion Trail"
    bl_description = "Show or hide the motion trail for the active object / bone"

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        obj = context.active_object
        bone_name = ""
        if obj.type == "ARMATURE" and obj.mode == "POSE" and context.active_pose_bone:
            bone_name = context.active_pose_bone.name
        try:
            visible = trail_cache.toggle(obj, bone_name)
            self.report({"INFO"}, "Trail visible" if visible else "Trail hidden")
        except Exception as e:
            self.report({"ERROR"}, str(e))
            traceback.print_exc()
            return {"CANCELLED"}
        context.area.tag_redraw()
        return {"FINISHED"}


class MMT_OT_refresh(bpy.types.Operator):
    bl_idname = "mmt.refresh_trails"
    bl_label = "Refresh All Trails"
    bl_description = "Re-evaluate all active motion trails"

    def execute(self, context):
        trail_cache.refresh_all()
        return {"FINISHED"}


class MMT_OT_clear_all(bpy.types.Operator):
    bl_idname = "mmt.clear_all_trails"
    bl_label = "Clear All Trails"
    bl_description = "Remove all active motion trails"

    def execute(self, context):
        trail_cache.clear_all()
        return {"FINISHED"}


_classes = (MMT_OT_toggle, MMT_OT_refresh, MMT_OT_clear_all)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
