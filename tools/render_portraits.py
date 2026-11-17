"""Cycles portraits on transparency for the character grid: build/icons/<char>_bust.png and <char>_head.png (512 px).

Run: blender -b build/blender_scene/ts3_characters.blend --python tools/render_portraits.py
"""
import math
import pathlib

import bpy
from mathutils import Vector

OUT = pathlib.Path(bpy.data.filepath).resolve().parents[1] / "icons"
CHARS = ["t3_woody", "t3_jessie", "t3_buzz", "t3_zurg"]
# Part of the figure's height framed from the top: bust down to the belt, head down to the chin.
FRAMES = {"bust": 0.55, "head": 0.30}

scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.samples = 96
scene.cycles.use_denoising = True
scene.render.film_transparent = True
scene.render.resolution_x = scene.render.resolution_y = 512
# Standard keeps the texture colours as painted, like the game's own icons (AgX washed them out)
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
world = bpy.data.worlds.new("Portrait")
scene.world = world
world.use_nodes = True
wn = world.node_tree.nodes
bg = wn.get("Background") or wn.new("ShaderNodeBackground")
out_node = wn.get("World Output") or wn.new("ShaderNodeOutputWorld")
world.node_tree.links.new(bg.outputs["Background"], out_node.inputs["Surface"])
bg.inputs["Color"].default_value = (0.25, 0.3, 0.4, 1)
bg.inputs["Strength"].default_value = 0.3

cam = bpy.data.objects.new("portrait_cam", bpy.data.cameras.new("portrait_cam"))
scene.collection.objects.link(cam)
scene.camera = cam
cam.data.type = "ORTHO"


def area(name, energy, size, color):
    light = bpy.data.objects.new(name, bpy.data.lights.new(name, "AREA"))
    light.data.energy, light.data.size, light.data.color = energy, size, color
    scene.collection.objects.link(light)
    return light


key, fill, rim = (area("key", 1, 2.5, (1.0, 0.95, 0.88)), area("fill", 1, 4, (0.8, 0.88, 1.0)),
                  area("rim", 1, 2, (0.75, 0.85, 1.0)))


def aim(obj, target):
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()


OUT.mkdir(parents=True, exist_ok=True)
for name in CHARS:
    for o in scene.objects:
        if o.type in ("MESH", "ARMATURE"):
            o.hide_render = not o.name.startswith(name)
    obj = bpy.data.objects[name]
    pts = [obj.matrix_world @ v.co for v in obj.data.vertices]
    top = max(p.z for p in pts)
    bottom = min(p.z for p in pts)
    cx = sum(p.x for p in pts) / len(pts)
    cy = sum(p.y for p in pts) / len(pts)
    height = top - bottom
    for frame, part in FRAMES.items():
        size = height * part
        centre = Vector((cx, cy, top - size / 2))
        yaw = math.radians(18)
        cam.location = centre + Vector((math.sin(yaw) * 6, -math.cos(yaw) * 6, 0.3 * size))
        aim(cam, centre)
        cam.data.ortho_scale = size * 1.05
        # Lights sit at a distance proportional to the frame, so a head gets the same exposure as a bust
        for light, offset, energy in ((key, (-3, -3, 2), 260), (fill, (3, -2, 0.5), 90), (rim, (1.5, 3, 2), 320)):
            light.location = centre + Vector(offset) * size
            light.data.energy = energy * size * size
            light.data.size = 1.2 * size
            aim(light, centre)
        scene.render.filepath = str(OUT / f"{name}_{frame}.png")
        bpy.ops.render.render(write_still=True)
        print("portrait", name, frame)
