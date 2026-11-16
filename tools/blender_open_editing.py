"""Opens the editing session: loads tools/blender_export_edits.py into the Scripting tab's text editor.

Run: blender build/blender_scene/ts3_characters.blend --python tools/blender_open_editing.py
"""
import pathlib

import bpy

EXPORT = pathlib.Path(bpy.data.filepath).resolve().parents[2] / "tools" / "blender_export_edits.py"


def show_script():
    text = bpy.data.texts.get(EXPORT.name) or bpy.data.texts.load(str(EXPORT))
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "TEXT_EDITOR":
                area.spaces[0].text = text
    return None


bpy.app.timers.register(show_script, first_interval=1.0)
