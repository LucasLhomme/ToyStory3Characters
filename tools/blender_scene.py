"""Blender scene of the built T3 characters: mesh + DI3 armature + skin weights + atlas, side by side.

Run: blender --python tools/blender_scene.py (after tools/export_blender_data.py). Saves build/blender_scene/ts3_characters.blend.
"""
import pathlib

import bpy
import numpy as np
from mathutils import Matrix, Vector

SCENE = pathlib.Path(__file__).resolve().parents[1] / "build" / "blender_scene"
CHARS = ["t3_woody", "t3_jessie", "t3_buzz", "t3_zurg"]
# DI3 is Y-up; Blender is Z-up: (x, y, z) -> (x, -z, y)
AXIS = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))


def to_bl(p):
    return Vector((p[0], -p[2], p[1]))


def build(name, x):
    d = np.load(SCENE / f"{name}.npz")
    names = [str(s) for s in d["bone_names"]]

    arm_data = bpy.data.armatures.new(f"{name}_DI3")
    arm = bpy.data.objects.new(f"{name}_DI3", arm_data)
    bpy.context.scene.collection.objects.link(arm)
    arm.location.x = x
    arm.show_in_front = True
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    heads = [to_bl(w[3, :3]) for w in d["bone_world"]]
    edit = []
    for i, bn in enumerate(names):
        eb = arm_data.edit_bones.new(bn)
        eb.head = heads[i]
        kids = [heads[j] for j, p in enumerate(d["bone_parent"]) if p == i]
        tail = sum(kids, Vector()) / len(kids) if kids else heads[i] + Vector((0, 0, 0.05))
        eb.tail = tail if (tail - heads[i]).length > 0.01 else heads[i] + Vector((0, 0, 0.05))
        rot = (AXIS @ Matrix(d["bone_world"][i].T.tolist())).to_3x3()
        eb.align_roll(rot.col[2])
        edit.append(eb)
    for i, p in enumerate(d["bone_parent"]):
        if p >= 0:
            edit[i].parent = edit[p]
    bpy.ops.object.mode_set(mode="OBJECT")

    pos = d["pos"]
    verts = [to_bl(p) for p in pos]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], d["tris"].tolist())
    uv = d["uv"].copy()
    uv[:, 1] = 1.0 - uv[:, 1]
    layer = mesh.uv_layers.new(name="UVMap")
    layer.data.foreach_set("uv", uv[d["tris"].reshape(-1)].reshape(-1))
    for poly in mesh.polygons:
        poly.use_smooth = True
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = arm

    groups = [obj.vertex_groups.new(name=bn) for bn in names]
    for vi, (row, w) in enumerate(zip(d["bidx"], d["bw"])):
        for bi, wt in zip(row, w):
            if wt:
                groups[bi].add([vi], float(wt) / 255.0, "ADD")
    mod = obj.modifiers.new("DI3 skeleton", "ARMATURE")
    mod.object = arm

    mat = bpy.data.materials.new(f"{name}_atlas")
    mat.use_nodes = True
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(str(SCENE / f"{name}_atlas.png"))
    mat.node_tree.links.new(tex.outputs["Color"], mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
    obj.data.materials.append(mat)


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for k, name in enumerate(CHARS):
        build(name, (k - 1.5) * 1.6)
    for area in bpy.context.screen.areas if bpy.context.screen else []:
        if area.type == "VIEW_3D":
            area.spaces[0].shading.type = "MATERIAL"
    bpy.ops.wm.save_as_mainfile(filepath=str(SCENE / "ts3_characters.blend"))


main()
