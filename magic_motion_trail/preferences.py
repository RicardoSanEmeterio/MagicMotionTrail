import bpy
from bpy.props import FloatProperty, IntProperty, BoolProperty

ADDON_ID = "magic_motion_trail"

DEFAULT_FRAMES_BEFORE = 25
DEFAULT_FRAMES_AFTER = 25
DEFAULT_POINT_SIZE = 6.0
DEFAULT_LINE_WIDTH = 2.0
DEFAULT_OPACITY_FALLOFF = 0.7


class MMT_Preferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    trail_frames_before: IntProperty(
        name="Frames Before",
        description="Number of frames to show before the current frame",
        default=DEFAULT_FRAMES_BEFORE,
        min=1, max=200,
    )
    trail_frames_after: IntProperty(
        name="Frames After",
        description="Number of frames to show after the current frame",
        default=DEFAULT_FRAMES_AFTER,
        min=1, max=200,
    )
    trail_point_size: FloatProperty(
        name="Point Size",
        default=DEFAULT_POINT_SIZE,
        min=2.0, max=20.0,
    )
    trail_line_width: FloatProperty(
        name="Line Width",
        default=DEFAULT_LINE_WIDTH,
        min=1.0, max=10.0,
    )
    trail_opacity_falloff: FloatProperty(
        name="Fade",
        description="How much the trail fades at the edges "
                    "(0 = no fade, 1 = fully transparent at edges)",
        default=DEFAULT_OPACITY_FALLOFF,
        min=0.0, max=1.0,
        subtype='FACTOR',
    )
    trail_auto_update: BoolProperty(
        name="Auto Update",
        description="Automatically refresh trails when animation data changes",
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.prop(self, "trail_frames_before")
        row.prop(self, "trail_frames_after")
        row = layout.row()
        row.prop(self, "trail_point_size")
        row.prop(self, "trail_line_width")
        layout.prop(self, "trail_opacity_falloff")
        layout.prop(self, "trail_auto_update")


def get_prefs():
    """Safely retrieve addon preferences with fallback defaults."""
    try:
        addon = bpy.context.preferences.addons.get(ADDON_ID)
        if addon:
            return addon.preferences
    except Exception:
        pass
    return None


_classes = (MMT_Preferences,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
