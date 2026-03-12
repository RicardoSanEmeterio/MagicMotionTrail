"""N-panel UI for MagicMotionTrail."""

import bpy
from . import trail_cache
from .preferences import ADDON_ID


class MMT_PT_main(bpy.types.Panel):
    bl_idname = "MMT_PT_main"
    bl_label = "Motion Trail"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Motion Trail"

    def draw(self, context):
        layout = self.layout
        obj = context.active_object

        if obj is None:
            layout.label(text="No active object")
            return

        bone_name = ""
        if obj.type == "ARMATURE" and obj.mode == "POSE" and context.active_pose_bone:
            bone_name = context.active_pose_bone.name

        col = layout.column(align=True)
        col.label(text=f"Target: {obj.name}", icon="OBJECT_DATA")
        if bone_name:
            col.label(text=f"Bone: {bone_name}", icon="BONE_DATA")

        key = trail_cache.make_key(obj, bone_name)
        has_trail = key in trail_cache.get_all()

        row = layout.row(align=True)
        if has_trail:
            row.operator("mmt.toggle_trail", text="Hide Trail", icon="HIDE_ON")
        else:
            row.operator("mmt.toggle_trail", text="Show Trail", icon="HIDE_OFF")

        row = layout.row(align=True)
        row.operator("mmt.refresh_trails", text="Refresh", icon="FILE_REFRESH")
        row.operator("mmt.clear_all_trails", text="Clear All", icon="X")

        if has_trail:
            layout.separator()
            layout.operator("mmt.edit_trail", text="Edit Trail Points", icon="EDITMODE_HLT")

        prefs = context.preferences.addons.get(ADDON_ID)
        if prefs:
            p = prefs.preferences
            box = layout.box()
            box.label(text="Settings", icon="PREFERENCES")
            row = box.row()
            row.prop(p, "trail_frames_before")
            row.prop(p, "trail_frames_after")
            row = box.row()
            row.prop(p, "trail_point_size")
            row.prop(p, "trail_line_width")
            box.prop(p, "trail_opacity_falloff")
            box.prop(p, "trail_auto_update")


_classes = (MMT_PT_main,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
