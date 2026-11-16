"""Renders each built T3 character front-on, transparent background, into build/icons/<char>_body.png.

Run: blender -b build/blender_scene/ts3_characters.blend --python tools/render_icons.py
"""
import math
import pathlib

import bpy
from mathutils import Vector

OUT = pathlib.Path(bpy.data.filepath).resolve().parents[1] / "icons"
CHARS = ["t3_woody", "t3_jessie", "t3_buzz", "t3_zurg"]

scene = bpy.context.scene
scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "TEXTURE"
scene.display.shading.show_cavity = True
scene.render.film_transparent = True
scene.render.resolution_x = scene.render.resolution_y = 1024
cam = bpy.data.objects.new("icon_cam", bpy.data.cameras.new("icon_cam"))
scene.collection.objects.link(cam)
scene.camera = cam
cam.data.type = "ORTHO"
OUT.mkdir(parents=True, exist_ok=True)

for name in CHARS:
    for o in scene.objects:
        if o.type in ("MESH", "ARMATURE"):
            o.hide_render = not o.name.startswith(name)
    obj = bpy.data.objects[name]
    pts = [obj.matrix_world @ v.co for v in obj.data.vertices]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    centre = (lo + hi) / 2
    # 3/4 front view, slightly from above, like the shipped figure pictures
    yaw = math.radians(20)
    cam.location = centre + Vector((math.sin(yaw) * 6, -math.cos(yaw) * 6, 0.6))
    cam.rotation_euler = (math.radians(84), 0, yaw)
    cam.data.ortho_scale = max(hi.z - lo.z, hi.x - lo.x) * 1.1
    scene.render.filepath = str(OUT / f"{name}_body.png")
    bpy.ops.render.render(write_still=True)
    print("icon render", name)
