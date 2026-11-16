"""Dumps each built T3 character (mesh, skin weights, DI3 skeleton, atlas) into build/blender_scene/ for blender_scene.py."""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_ts3_characters as b

OUT = b.BUILD / "blender_scene"


# Returns, for each skin influence, the index of its closest ancestor that is also an influence (-1 for roots).
def influence_parents(base_name: str, bone_names: list[str]) -> list[int]:
    xml = b.RAW / "di3" / base_name.lower() / f"{base_name.lower()}.xml"
    bases = [(e.findtext("Name"), int(e.findtext("ParentRef")))
             for e in ET.parse(xml).getroot().iter("Basis") if e.find("ParentRef") is not None]
    by_name = {n: i for i, (n, _) in enumerate(bases)}
    out = []
    for bn in bone_names:
        ref = bases[by_name[bn]][1] if bn in by_name else 0
        parent = -1
        while ref > 0:
            pname = bases[ref - 1][0]
            if pname in bone_names:
                parent = bone_names.index(pname)
                break
            ref = bases[ref - 1][1]
        if parent < 0 and bn != "Hips":
            parent = name_parent(bn, bone_names)
        out.append(parent)
    return out


# Basis is the control rig, so twist (_OL), second-phalanx, toe and lid joints are missing from it: guess by name.
def name_parent(bn: str, bone_names: list[str]) -> int:
    side = bn.rsplit("_", 1)[-1] if bn.endswith(("_Left", "_Right")) else ""
    cands = [bn.replace("_OL", ""), bn.replace("2_", "1_"), f"Elbow_{side}", f"Ball_{side}", f"Ankle_{side}",
             f"Ankle_Break_{side}"] if side else []
    if "lid" in bn.lower():
        cands.append("Head")
    if bn == "Spine1":
        cands.append("Hips")
    for c in cands:
        if c != bn and c in bone_names:
            return bone_names.index(c)
    return -1


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for spec in b.CHARACTERS:
        lname = spec["name"].lower()
        base = b.load_di3_base(spec.get("model_base", spec["base"]))
        cdir = b.MOD_OUT / "characters" / lname
        vbuf = (cdir / f"{lname}_0.vbuf").read_bytes()
        n = len(vbuf) // 40
        s0 = np.frombuffer(vbuf[:n * 16], np.uint8).reshape(n, 16)
        s1 = np.frombuffer(vbuf[n * 16:], np.uint8).reshape(n, 24)
        pos = s0[:, :12].copy().view("<f4").reshape(n, 3)
        uv = s0[:, 12:16].copy().view("<f2").reshape(n, 2).astype(np.float32)
        tris = np.frombuffer((cdir / f"{lname}_0.ibuf").read_bytes(), "<u2").astype(np.int32).reshape(-1, 3)
        area = np.linalg.norm(np.cross(pos[tris[:, 1]] - pos[tris[:, 0]], pos[tris[:, 2]] - pos[tris[:, 0]]), axis=1)
        Image.open(b.MOD_OUT / "textures" / f"{spec['diff_hash']}.tbody").convert("RGB").save(OUT / f"{lname}_atlas.png")
        n_body = n - 3 * (len(base["prims"]) - 1)
        np.savez(OUT / f"{lname}.npz", pos=pos, uv=uv, tris=tris[area > 1e-12], n_body=n_body,
                 bidx=s1[:, :4], bw=s1[:, 4:8], bone_world=base["bone_world"],
                 bone_names=np.array(base["bone_names"]),
                 bone_parent=np.array(influence_parents(spec.get("model_base", spec["base"]), base["bone_names"])))
        print(f"{lname}: {n} verts, {len(base['bone_names'])} bones")


if __name__ == "__main__":
    main()
