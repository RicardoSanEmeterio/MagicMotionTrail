bl_info = {
    "name": "MagicMotionTrail",
    "author": "MagicMotionTrail Team",
    "version": (1, 0, 0),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > Motion Trail",
    "description": "Interactive motion trails with editable keyframe points and smooth brush",
    "warning": "",
    "doc_url": "",
    "category": "Animation",
}

ADDON_ID = "magic_motion_trail"

_modules = []


def register():
    from . import preferences
    from . import operators
    from . import edit_operator
    from . import drawing
    from . import handlers
    from . import ui

    _modules.clear()
    _modules.extend([preferences, operators, edit_operator, ui])

    for mod in _modules:
        mod.register()

    drawing.register_draw_handlers()
    handlers.register_handlers()


def unregister():
    from . import drawing
    from . import handlers

    handlers.unregister_handlers()
    drawing.unregister_draw_handlers()

    for mod in reversed(_modules):
        mod.unregister()
    _modules.clear()


if __name__ == "__main__":
    register()
