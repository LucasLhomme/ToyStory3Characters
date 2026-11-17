"""High-quality showcase renders (Cycles) of the built T3 characters: one 3/4 shot each plus a group shot.

Run: blender -b build/blender_scene/ts3_characters.blend --python tools/render_showcase.py [-- --samples 256]
Writes renders/<char>.png and renders/group.png (1920x1080).
"""
import math
import pathlib
import sys

import bpy
from mathutils import Vector

ROOT = pathlib.Path(bpy.data.filepath).resolve().parents[2]
OUT = ROOT / "renders"
CHARS = ["t3_woody", "t3_jessie", "t3_buzz", "t3_zurg"]
TITLES = {"t3_woody": "woody", "t3_jessie": "jessie", "t3_buzz": "buzz", "t3_zurg": "zurg"}
ARGV = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
SAMPLES = int(ARGV[ARGV.index("--samples") + 1]) if "--samples" in ARGV else 256

scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.samples = SAMPLES
scene.cycles.use_denoising = True
prefs = bpy.context.preferences.addons["cycles"].preferences
for backend in ("OPTIX", "CUDA", "HIP", "ONEAPI"):
    try:
        prefs.compute_device_type = backend
        prefs.get_devices()
        if any(d.type == backend for d in prefs.devices):
            for d in prefs.devices:
                d.use = d.type == backend
            scene.cycles.device = "GPU"
            break
    except TypeError:
        continue
scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
# Standard keeps the texture colours as painted (AgX desaturated the lit areas)
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"

# Studio world: soft blue-grey ambient
world = bpy.data.worlds.new("Studio")
scene.world = world
world.use_nodes = True
wn = world.node_tree.nodes
bg = wn.get("Background") or wn.new("ShaderNodeBackground")
out_node = wn.get("World Output") or wn.new("ShaderNodeOutputWorld")
world.node_tree.links.new(bg.outputs["Background"], out_node.inputs["Surface"])
bg.inputs["Color"].default_value = (0.05, 0.06, 0.09, 1)
bg.inputs["Strength"].default_value = 0.35

# Curved backdrop (cyclorama) so the floor melts into the background
bpy.ops.mesh.primitive_plane_add(size=1)
cyc = bpy.context.active_object
cyc.name = "backdrop"
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.delete(type="VERT")
bpy.ops.object.mode_set(mode="OBJECT")
verts, faces, n = [], [], 24
profile = [(y, 0.0) for y in (-12, -2)] + [(4 * math.sin(a), 4 - 4 * math.cos(a))
                                           for a in [math.pi / 2 * i / n for i in range(1, n + 1)]]
profile = [(p[0] + 2.0, p[1]) for p in profile] + [(6.0, 10.0)]
for x in (-30, 30):
    verts += [(x, y, z) for y, z in profile]
m = len(profile)
faces = [(i, i + 1, m + i + 1, m + i) for i in range(m - 1)]
cyc.data.from_pydata(verts, [], faces)
for p in cyc.data.polygons:
    p.use_smooth = True
def principled(material):
    material.use_nodes = True
    nodes = material.node_tree.nodes
    p = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if p is None:
        p = nodes.new("ShaderNodeBsdfPrincipled")
        out = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None) or nodes.new("ShaderNodeOutputMaterial")
        material.node_tree.links.new(p.outputs["BSDF"], out.inputs["Surface"])
    return p


mat = bpy.data.materials.new("backdrop")
bsdf = principled(mat)
bsdf.inputs["Base Color"].default_value = (0.20, 0.27, 0.42, 1)
bsdf.inputs["Roughness"].default_value = 0.65
cyc.data.materials.append(mat)


def area(name, loc, energy, size, color=(1, 1, 1)):
    light = bpy.data.objects.new(name, bpy.data.lights.new(name, "AREA"))
    light.data.energy, light.data.size, light.data.color = energy, size, color
    light.location = loc
    scene.collection.objects.link(light)
    return light


def aim(obj, target):
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()


cam = bpy.data.objects.new("showcase_cam", bpy.data.cameras.new("showcase_cam"))
scene.collection.objects.link(cam)
scene.camera = cam
cam.data.lens = 70
lights = [area("key", Vector(), 900, 2.5, (1.0, 0.95, 0.88)), area("fill", Vector(), 300, 4, (0.8, 0.88, 1.0)),
          area("rim", Vector(), 1100, 2, (0.75, 0.85, 1.0))]
BASE_ENERGY = [l.data.energy for l in lights]

# Softer, slightly glossy character materials
for name in CHARS:
    obj = bpy.data.objects[name]
    for m in obj.data.materials:
        p = principled(m)
        p.inputs["Roughness"].default_value = 0.45
        p.inputs["Specular IOR Level"].default_value = 0.4
bpy.context.view_layer.update()


def bounds(objs):
    deps = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in objs:
        ev = o.evaluated_get(deps)
        mesh = ev.to_mesh()
        pts += [ev.matrix_world @ v.co for v in mesh.vertices]
        ev.to_mesh_clear()
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def shoot(visible, path, yaw_deg):
    for o in scene.objects:
        if o.type in ("MESH", "ARMATURE") and o.name != "backdrop":
            o.hide_render = not any(o.name.startswith(v) for v in visible)
    lo, hi = bounds([bpy.data.objects[v] for v in visible])
    centre = (lo + hi) / 2
    height = hi.z - lo.z
    width = hi.x - lo.x
    span = max(height * 1.5, width * 1.4 * 1080 / 1920)
    dist = span / (2 * math.tan(cam.data.angle_y / 2))
    yaw = math.radians(yaw_deg)
    cam.location = centre + Vector((math.sin(yaw) * dist, -math.cos(yaw) * dist, height * 0.12))
    aim(cam, centre)
    r = max(height, width)
    lights[0].location = centre + Vector((-2.5 * r, -2.6 * r, 1.6 * r))
    lights[1].location = centre + Vector((2.8 * r, -2.0 * r, 0.5 * r))
    lights[2].location = centre + Vector((1.2 * r, 2.5 * r, 1.8 * r))
    for light, base in zip(lights, BASE_ENERGY):
        aim(light, centre)
        light.data.energy = 0.6 * base * (r / 2.2) ** 2  # tuned on a 2.2 m figure, scaled with distance
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    print("showcase render", path.name)


OUT.mkdir(exist_ok=True)
for name in CHARS:
    shoot([name], OUT / f"{TITLES[name]}.png", 22)
shoot(CHARS, OUT / "group.png", 8)
