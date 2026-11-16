"""Saves hand edits from ts3_characters.blend into edits/<char>.npz, which build_ts3_characters.py applies.

In Blender: Scripting tab > Open this file > Run Script (the .blend must be the one in build/blender_scene).
Or headless: blender -b build/blender_scene/ts3_characters.blend --python tools/blender_export_edits.py
Only moving vertices and painting weights survive: adding or deleting vertices is refused by the build.
"""
import pathlib

import bpy
import numpy as np

SCENE = pathlib.Path(bpy.data.filepath).resolve().parent
EDITS = SCENE.parents[1] / "edits"
CHARS = ["t3_woody", "t3_jessie", "t3_buzz", "t3_zurg"]


def export(name):
    src = np.load(SCENE / f"{name}.npz")
    names = [str(s) for s in src["bone_names"]]
    n_body = int(src["n_body"])
    obj = bpy.data.objects[name]
    verts = obj.data.vertices
    assert len(verts) == len(src["pos"]), f"{name}: vertex count changed ({len(verts)} vs {len(src['pos'])})"

    group_bone = {g.index: names.index(g.name) for g in obj.vertex_groups if g.name in names}
    pos = np.zeros((n_body, 3), np.float32)
    bidx = np.zeros((n_body, 4), np.uint8)
    bw = np.zeros((n_body, 4), np.uint8)
    unweighted = 0
    for vi in range(n_body):
        v = verts[vi]
        pos[vi] = (v.co.x, v.co.z, -v.co.y)  # Blender Z-up -> DI3 Y-up
        ws = sorted(((g.weight, group_bone[g.group]) for g in v.groups if g.group in group_bone and g.weight > 0),
                    reverse=True)[:4]
        if not ws:
            unweighted += 1
            ws = [(1.0, names.index("Hips") if "Hips" in names else 0)]
        total = sum(w for w, _ in ws)
        q = [int(round(255 * w / total)) for w, _ in ws]
        q[0] += 255 - sum(q)
        for k, ((_, b), qw) in enumerate(zip(ws, q)):
            bidx[vi, k], bw[vi, k] = b, qw
    EDITS.mkdir(exist_ok=True)
    np.savez(EDITS / f"{name}.npz", pos=pos, bidx=bidx, bw=bw, bone_names=np.array(names))
    moved = float(np.abs(pos - src["pos"][:n_body]).max())
    print(f"{name}: saved edits/{name}.npz (max vertex move {moved * 100:.1f} cm, {unweighted} unweighted -> Hips)")


for name in CHARS:
    if name in bpy.data.objects:
        export(name)
