"""Centralized Blender event handlers with safe lifecycle management.

Key stability rules implemented here:
    1. Handlers NEVER call ``frame_set()`` – they only mark dirty.
    2. All handler bodies are wrapped in ``try/except`` so Blender won't
       silently unregister them on an unexpected exception.
    3. The debounce timer is the ONLY place that triggers evaluation.
    4. ``_on_frame_change`` skips when ``_is_evaluating`` is True to prevent
       the feedback loop: refresh → frame_set → frame_change → dirty → repeat.
    5. Object-level depsgraph updates are tracked to detect interactive
       transforms; the timer defers refresh until transforms settle.
"""

import time
import traceback

import bpy
from bpy.app.handlers import persistent

from . import trail_cache
from .preferences import get_prefs

_DEBOUNCE_SECS = 0.40
_TRANSFORM_QUIET_SECS = 0.60
_last_dirty_time: float = 0.0
_last_object_update_time: float = 0.0
_timer_registered: bool = False


# ── depsgraph update handler ─────────────────────────────────────────────────

@persistent
def _on_depsgraph_update(scene, depsgraph):
    """Mark dirty when an *Action* datablock is updated.  Also track
    Object-level updates so we can detect interactive transforms and
    defer trail refresh until they finish.
    """
    try:
        if not trail_cache.get_all():
            return
        if trail_cache._is_evaluating:
            return

        prefs = get_prefs()
        if prefs is not None and not getattr(prefs, "trail_auto_update", True):
            return

        has_action = False
        for update in depsgraph.updates:
            if isinstance(update.id, bpy.types.Object):
                global _last_object_update_time
                _last_object_update_time = time.monotonic()
            if isinstance(update.id, bpy.types.Action):
                has_action = True

        if has_action:
            _mark_dirty_now()
    except Exception:
        traceback.print_exc()


# ── frame change handler ─────────────────────────────────────────────────────

@persistent
def _on_frame_change(scene, depsgraph):
    """Mark trails dirty on frame change so the timer refreshes them.

    Crucially, skip if ``_is_evaluating`` is True – our own ``frame_set()``
    calls during trail evaluation trigger this handler, which would re-mark
    dirty and create an infinite refresh loop.
    """
    try:
        if trail_cache._is_evaluating:
            return
        if trail_cache.get_all():
            _mark_dirty_now()
    except Exception:
        traceback.print_exc()


# ── load-post handler ────────────────────────────────────────────────────────

@persistent
def _on_load_post(*_args):
    """Clear all trail data when a new file is loaded."""
    try:
        trail_cache.clear_all()
    except Exception:
        traceback.print_exc()


# ── debounce timer ───────────────────────────────────────────────────────────

def _auto_refresh_timer():
    """Periodic timer that checks the dirty flag and refreshes when due.

    Defers refresh while:
      - the edit operator is active (it manages its own refresh)
      - an interactive transform is in progress (recent Object updates)
      - the debounce interval hasn't elapsed since the last dirty mark

    Returns the next interval in seconds to keep the timer alive.
    """
    try:
        if not trail_cache.is_dirty():
            return 0.2

        if trail_cache.edit_state["active"]:
            return 0.2

        elapsed_since_dirty = time.monotonic() - _last_dirty_time
        if elapsed_since_dirty < _DEBOUNCE_SECS:
            return 0.1

        elapsed_since_obj = time.monotonic() - _last_object_update_time
        if elapsed_since_obj < _TRANSFORM_QUIET_SECS:
            return 0.15

        trail_cache.refresh_all()
    except Exception:
        traceback.print_exc()
    return 0.2


# ── internal helper ──────────────────────────────────────────────────────────

def _mark_dirty_now():
    global _last_dirty_time
    trail_cache.mark_dirty()
    _last_dirty_time = time.monotonic()


# ── registration ─────────────────────────────────────────────────────────────

def register_handlers():
    global _timer_registered

    _safe_append(bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update)
    _safe_append(bpy.app.handlers.frame_change_post, _on_frame_change)
    _safe_append(bpy.app.handlers.load_post, _on_load_post)

    if not _timer_registered:
        bpy.app.timers.register(_auto_refresh_timer, persistent=True)
        _timer_registered = True


def unregister_handlers():
    global _timer_registered

    _safe_remove(bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update)
    _safe_remove(bpy.app.handlers.frame_change_post, _on_frame_change)
    _safe_remove(bpy.app.handlers.load_post, _on_load_post)

    if _timer_registered:
        try:
            bpy.app.timers.unregister(_auto_refresh_timer)
        except Exception:
            pass
        _timer_registered = False


def _safe_append(handler_list, fn):
    if fn not in handler_list:
        handler_list.append(fn)


def _safe_remove(handler_list, fn):
    try:
        while fn in handler_list:
            handler_list.remove(fn)
    except Exception:
        pass
