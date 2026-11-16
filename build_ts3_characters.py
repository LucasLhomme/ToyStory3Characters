"""build_ts3_characters.py - Import Woody, Jessie, Buzz, and Zurg from Toy Story 3 into Disney Infinity 3.0.

Builds a standalone CrabeLoader mod with 4 new playable clone actors:
  - T3_Woody  (cloned from TS_Woody)
  - T3_Jessie (cloned from TS_Jessie)
  - T3_Buzz   (cloned from TS_Buzz)
  - T3_Zurg   (cloned from TS_Buzz)
"""
import argparse
import io
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.spatial import cKDTree

MOD_ROOT = Path(__file__).resolve().parent
DIM2 = MOD_ROOT.parent
TOOLS = DIM2 / "CrabeLoader" / "tools"
QUICKBMS = TOOLS / "QuickBMS" / "quickbms.exe"
BMS_SCRIPT = TOOLS / "QuickBMS" / "disney_infinity_new.bms"
REENCRYPTER = TOOLS / ".ZIP Reencrypter" / "offsetting.exe"
ZIP_KEY = "681BBEEA63160188F9B7945104A51499"
BLENDER = next(
    (p for p in (
        Path(r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"),
        Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"),
    ) if p.exists()),
    Path("blender"),
)
HEAT_SCRIPT = TOOLS / "sora_work" / "pipeline" / "blender_heat_weights.py"
C2DITOOLS_SRC = TOOLS / "Cars2TheVideoGameModding" / "src"

TS3_GAME = Path(r"D:\Jeu\Crack\Toy Story 3 Complete Edition")
DI3_GAME = Path(r"D:\SteamLibrary\steamapps\common\Disney Infinity 3.0 Gold Edition")

BUILD = MOD_ROOT / "build"
RAW = BUILD / "raw"
PREVIEWS = BUILD / "previews"
MOD_OUT = BUILD / "mod"

ATLAS_SIZE = 1024
ATLAS_PAD = 8
FLAT_NORMAL_RGBA = (255, 127, 140, 127)
SIDES = (("Left", 1.0), ("Right", -1.0))

# Character specs: (new_name, base_name, ts3_folder, ts3_zip, skip_mats, mat_guid, mat_hash, diff_hash, norm_hash)
CHARACTERS = [
    {
        "name": "T3_Woody",
        "base": "TS_Woody",
        "ts3_dir": "woody",
        "ts3_zip": "woody.zip",
        "skip_mats": set(),
        "mat_guid": b"b3000001-0001-4000-8000-000000000001",
        "mat_hash": bytes.fromhex("b3000001"),
        "diff_hash": "b300000000000001",
        "norm_hash": "b300000000000002",
        "prog_tree": "IN3_TS_Woody",
        "scale_to_base": True,
    },
    {
        "name": "T3_Jessie",
        "base": "TS_Jessie",
        "ts3_dir": "jessie",
        "ts3_zip": "jessie.zip",
        "skip_mats": set(),
        "mat_guid": b"b3000002-0002-4000-8000-000000000002",
        "mat_hash": bytes.fromhex("b3000002"),
        "diff_hash": "b300000000000003",
        "norm_hash": "b300000000000004",
        "prog_tree": "IN3_TS_Jessie",
        "scale_to_base": True,
    },
    {
        "name": "T3_Buzz",
        "base": "TS_Buzz",
        "ts3_dir": "buzzlightyear",
        "ts3_zip": "buzzlightyear.zip",
        "skip_mats": {"Dome3", "wings_n_such_Buzz_bindpose2_JetPack_Flame4", "wings_n_such_Buzz_bindpose2_JetPack_Flame3"},
        "mat_guid": b"b3000003-0003-4000-8000-000000000003",
        "mat_hash": bytes.fromhex("b3000003"),
        "diff_hash": "b300000000000005",
        "norm_hash": "b300000000000006",
        "prog_tree": "IN3_TS_Buzz",
        "scale_to_base": True,
    },
    {
        "name": "T3_Zurg",
        "base": "TS_Buzz",
        "ts3_dir": "zurgavatar",
        "ts3_zip": "zurgavatar.zip",
        "skip_mats": {"Smokey_plastic"},
        "mat_guid": b"b3000004-0004-4000-8000-000000000004",
        "mat_hash": bytes.fromhex("b3000004"),
        "diff_hash": "b300000000000007",
        "norm_hash": "b300000000000008",
        "prog_tree": "IN3_TS_Buzz",
        "scale_to_base": False,  # Keep Zurg taller than Buzz
    },
]


def run_c2di_dec(oct_path: Path, xml_path: Path, tex_dir: Path | None = None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(C2DITOOLS_SRC)
    cmd = [sys.executable, "-m", "c2ditools", "scene_dec", str(oct_path), str(xml_path)]
    if tex_dir is not None:
        tex_dir.mkdir(parents=True, exist_ok=True)
        cmd += ["-t", str(tex_dir)]
    subprocess.run(cmd, env=env, check=True, capture_output=True)


def quickbms_extract(archive: Path, pattern: str, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    selector = [] if pattern == "*" else ["-f", pattern]
    subprocess.run([str(QUICKBMS), "-Y", *selector, str(BMS_SCRIPT), str(archive), str(out)],
                   check=True, capture_output=True)


def load_ts3_character(spec):
    """Extracts TS3 character zip, decodes .oct, and returns (positions, normals, uvs, faces, atlas_img)."""
    ts3_name = spec["ts3_dir"]
    work = RAW / "ts3" / ts3_name
    work.mkdir(parents=True, exist_ok=True)
    zp = TS3_GAME / "assets" / "characters" / ts3_name / spec["ts3_zip"]
    with zipfile.ZipFile(zp) as z:
        for ext in (".oct", "_0.vbuf", "_0.ibuf"):
            z.extract(f"{ts3_name}{ext}", work)
    tex_dir = work / "textures"
    xml_path = work / f"{ts3_name}.xml"
    run_c2di_dec(work / f"{ts3_name}.oct", xml_path, tex_dir)

    root = ET.parse(xml_path).getroot()
    vbuf = (work / f"{ts3_name}_0.vbuf").read_bytes()
    ibuf = (work / f"{ts3_name}_0.ibuf").read_bytes()

    texs = [t.findtext("Name") for t in root.findall("./TexturePool/*")]
    mats = []
    for m in root.findall("./MaterialPool/*"):
        trefs = [int(t.text) for t in m.findall(".//TextureReference")]
        mats.append((m.findtext("Name"), [texs[i] for i in trefs if i < len(texs)]))

    vstreams = root.findall("./VertexStreamPool/*")
    istreams = root.findall("./IndexStreamPool/*")

    # Collect primitives
    prims = []
    used_tex_names = []
    for el in root.iter():
        mr = el.find("MaterialReference")
        vsr = el.find("VertexStreamReferences")
        isr = el.find("IndexStreamReference")
        if mr is None or vsr is None or isr is None:
            continue
        mat_id = int(mr.text)
        mat_name, mat_texs = mats[mat_id]
        if mat_name in spec["skip_mats"] or not mat_texs:
            continue
        tex_name = mat_texs[0]
        if not (tex_dir / f"{tex_name}.dds").exists():
            continue
        vs_ids = [int(e.text) for e in vsr.findall("entry")]
        is_id = int(isr.text)
        prims.append((vs_ids, is_id, tex_name))
        if tex_name not in used_tex_names:
            used_tex_names.append(tex_name)

    # Build grid atlas for all used diffuse textures
    n_tex = len(used_tex_names)
    cols = math.ceil(math.sqrt(n_tex))
    rows = math.ceil(n_tex / cols)
    cell_w = ATLAS_SIZE // cols
    cell_h = ATLAS_SIZE // rows
    atlas = Image.new("RGB", (ATLAS_SIZE, ATLAS_SIZE), (0, 0, 0))
    rects = {}
    for idx_t, tname in enumerate(used_tex_names):
        r, c = divmod(idx_t, cols)
        rx, ry = c * cell_w, r * cell_h
        rects[tname] = (rx, ry, cell_w, cell_h)
        img = Image.open(tex_dir / f"{tname}.dds").convert("RGB")
        tile = np.array(img.resize((cell_w - 2 * ATLAS_PAD, cell_h - 2 * ATLAS_PAD), Image.LANCZOS))
        padded = np.pad(tile, ((ATLAS_PAD, ATLAS_PAD), (ATLAS_PAD, ATLAS_PAD), (0, 0)), mode="edge")
        atlas.paste(Image.fromarray(padded), (rx, ry))

    all_pos, all_nrm, all_uv, all_faces = [], [], [], []
    v_base = 0
    for vs_ids, is_id, tex_name in prims:
        vs0 = vstreams[vs_ids[0]]
        nv = int(vs0.findtext("Length"))
        w0 = int(vs0.findtext("Width"))
        off0 = int(vs0.findtext("VertexBufferOffset") or 0)
        raw0 = np.frombuffer(vbuf[off0:off0 + nv * w0], np.uint8).reshape(nv, w0)
        pos = raw0[:, :12].copy().view("<f4").reshape(nv, 3)
        uv_raw = raw0[:, 20:24].copy().view("<f2").reshape(nv, 2).astype(float)
        # Handle wrapped UVs cleanly
        uv = np.where((uv_raw < -0.01) | (uv_raw > 1.01), np.mod(uv_raw, 1.0), np.clip(uv_raw, 0.0, 1.0))
        rx, ry, rw, rh = rects[tex_name]
        au = (rx + ATLAS_PAD + uv[:, 0] * (rw - 2 * ATLAS_PAD)) / ATLAS_SIZE
        av = (ry + ATLAS_PAD + uv[:, 1] * (rh - 2 * ATLAS_PAD)) / ATLAS_SIZE

        # Normal from VS[1] if present (Normal at offset 8 as float16[4])
        if len(vs_ids) > 1:
            vs1 = vstreams[vs_ids[1]]
            w1 = int(vs1.findtext("Width"))
            off1 = int(vs1.findtext("VertexBufferOffset") or 0)
            raw1 = np.frombuffer(vbuf[off1:off1 + nv * w1], np.uint8).reshape(nv, w1)
            if w1 == 24:
                nrm = raw1[:, 8:14].copy().view("<f2").reshape(nv, 3).astype(float)
            else:
                nrm = raw1[:, :6].copy().view("<f2").reshape(nv, 3).astype(float)
        else:
            nrm = np.tile([0.0, 0.0, 1.0], (nv, 1))

        is_el = istreams[is_id]
        ni = int(is_el.findtext("Length"))
        ioff = int(is_el.findtext("IndexBufferOffset") or 0)
        tris = np.frombuffer(ibuf[ioff:ioff + ni * 2], "<u2").reshape(-1, 3).astype(int)

        # Compact only referenced vertices in this primitive
        used_v, inv = np.unique(tris, return_inverse=True)
        tris_local = inv.reshape(-1, 3) + v_base
        all_pos.append(pos[used_v])
        all_nrm.append(nrm[used_v])
        all_uv.append(np.stack([au[used_v], av[used_v]], 1))
        all_faces.append(tris_local)
        v_base += len(used_v)

    return np.vstack(all_pos), np.vstack(all_nrm), np.vstack(all_uv), np.vstack(all_faces), atlas


def load_di3_base(base_name: str):
    """Extracts DI3 base character and parses skeleton, primitives, and winding."""
    lbase = base_name.lower()
    work = RAW / "di3" / lbase
    if not (work / f"{lbase}.xml").exists():
        z = DI3_GAME / "assets" / "characters" / lbase / f"{lbase}.zip"
        for pat in (f"*{lbase}.oct", f"*{lbase}.mtb", f"*{lbase}.bent", f"*{lbase}_0.vbuf", f"*{lbase}_0.ibuf", f"*{lbase}.animtreeoverrides"):
            quickbms_extract(z, pat, work)
        quickbms_extract(DI3_GAME / "assets" / "characters" / "characters.zip", f"*{lbase}.dnax", work / "dnax")
        oct_file = next(work.rglob(f"{lbase}.oct"))
        run_c2di_dec(oct_file, work / f"{lbase}.xml")

    oct_file = next(work.rglob(f"{lbase}.oct"))
    mtb_file = next(work.rglob(f"{lbase}.mtb"))
    bent_file = next(work.rglob(f"{lbase}.bent"))
    vbuf_file = next(work.rglob(f"{lbase}_0.vbuf"))
    ibuf_file = next(work.rglob(f"{lbase}_0.ibuf"))
    anim_file = next(work.rglob(f"{lbase}.animtreeoverrides"))
    dnax_file = next((work / "dnax").rglob(f"{lbase}.dnax"))

    xml_text = (work / f"{lbase}.xml").read_text(errors="ignore")
    found = re.findall(
        r'<Name type="string">([^<]+)</Name>\s*<BindPoseSkinLocalToWorldMatrixInverseData type="float_list">(.*?)</BindPoseSkinLocalToWorldMatrixInverseData>',
        xml_text, re.S)
    names, world = [], []
    for bname, body in found:
        inv = np.array([float(v) for v in re.findall(r"<entry>([^<]+)</entry>", body)]).reshape(4, 4)
        names.append(bname)
        world.append(np.linalg.inv(inv))

    root = ET.fromstring(xml_text)
    prims = []
    for el in root.iter("Primitive"):
        prims.append({
            "mat": el.findtext("MaterialName"),
            "idata": [int(e.text) for e in el.findall("./Idata/entry")],
            "vdata": [int(e.text) for e in el.findall("./Vdata/entry")],
        })
    bbox = [float(e.text) for e in root.find(".//BoundingBox")]

    # Compute winding sign of Prim[0]
    vbuf = vbuf_file.read_bytes()
    ibuf = ibuf_file.read_bytes()
    p0 = prims[0]
    nv0, s0_0, s1_0 = p0["vdata"][1], p0["vdata"][3], p0["vdata"][6]
    ni0 = p0["idata"][3]
    pos0 = np.frombuffer(vbuf[s0_0:s0_0 + nv0 * 16], np.uint8).reshape(nv0, 16)[:, :12].copy().view("<f4").reshape(nv0, 3)
    nrm0 = np.frombuffer(vbuf[s1_0:s1_0 + nv0 * 24], np.uint8).reshape(nv0, 24)[:, 8:14].copy().view("<f2").astype(float)
    tri0 = np.frombuffer(ibuf[:ni0 * 2], "<u2").reshape(-1, 3).astype(int)
    fn0 = np.cross(pos0[tri0[:, 1]] - pos0[tri0[:, 0]], pos0[tri0[:, 2]] - pos0[tri0[:, 0]])
    winding = float(np.sign(np.median((fn0 * nrm0[tri0].mean(1)).sum(1))))

    return {
        "oct": oct_file.read_bytes(),
        "mtb": mtb_file.read_bytes(),
        "bent": bent_file.read_bytes(),
        "vbuf_len": len(vbuf),
        "ibuf_len": len(ibuf),
        "anim": anim_file.read_bytes(),
        "dnax": dnax_file.read_bytes(),
        "bone_names": names,
        "bone_world": np.array(world),
        "prims": prims,
        "bbox": bbox,
        "winding": winding,
    }


def pick_bone(base_pos: dict[str, np.ndarray], *candidates: str) -> str:
    for c in candidates:
        if c in base_pos:
            return c
    raise KeyError(candidates)


def make_tpose_anchors(positions: np.ndarray, base_pos: dict[str, np.ndarray], scale: float):
    """Builds T-pose joint anchors directly from the TS3 mesh geometry."""
    p = positions * scale
    bmin, bmax = p.min(0), p.max(0)
    h = bmax[1] - bmin[1]

    # Measure arm Y height from vertices around x in [0.45, 0.85]
    arm_pts = p[(np.abs(p[:, 0]) > 0.45 * scale) & (np.abs(p[:, 0]) < 0.85 * scale)]
    arm_y = float(np.median(arm_pts[:, 1])) if len(arm_pts) else h * 0.72
    arm_z = float(np.median(arm_pts[:, 2])) if len(arm_pts) else 0.0

    # Measure leg X/Z around y in [0.15*h, 0.40*h]
    leg_pts = p[(p[:, 1] > 0.15 * h) & (p[:, 1] < 0.40 * h) & (np.abs(p[:, 0]) > 0.02)]
    leg_x = float(np.median(np.abs(leg_pts[:, 0]))) if len(leg_pts) else 0.10 * scale
    leg_z = float(np.median(leg_pts[:, 2])) if len(leg_pts) else 0.0

    # Measure hand tip X (ignoring Zurg's extended gun on -X by using +X side)
    pos_x_max = float(p[:, 0].max())
    hand_tip_x = max(pos_x_max * 0.95, 0.55 * scale)
    shoulder_x = max(leg_x * 1.4, 0.16 * scale)
    elbow_x = (shoulder_x + hand_tip_x * 0.72) * 0.5
    hand_x = hand_tip_x * 0.72

    # Crotch / hips height
    hips_y = h * 0.48
    neck_y = min(arm_y + 0.06 * scale, h * 0.80)
    head_y = h * 0.85
    ankle_y = float(base_pos[pick_bone(base_pos, "Ankle_Left", "Ankle_Break_Left")][1])

    a = {
        "Hips": np.array([0.0, hips_y, leg_z]),
        "HipsLow": np.array([0.0, hips_y * 0.85, leg_z]),
        "Neck": np.array([0.0, neck_y, arm_z]),
        "Head": np.array([0.0, head_y, arm_z]),
        "HeadTop": np.array([0.0, bmax[1] + 0.05, arm_z]),
    }
    lh, ln = base_pos["Hips"][1], base_pos["Neck"][1]
    for sp in ("Spine2", "Spine3"):
        t = (base_pos[sp][1] - lh) / (ln - lh)
        a[sp] = a["Hips"] + t * (a["Neck"] - a["Hips"])

    for side, sx in SIDES:
        a[f"Clavicle_{side}"] = a["Spine3"] + np.array([sx * 0.04 * scale, 0.02 * scale, 0.0])
        a[f"ClavicleHeat_{side}"] = a["Spine3"] + np.array([sx * 0.03 * scale, 0.02 * scale, 0.0])
        a[f"Shoulder_{side}"] = np.array([sx * shoulder_x, arm_y, arm_z])
        a[f"Elbow_{side}"] = np.array([sx * elbow_x, arm_y, arm_z])
        a[f"Hand_{side}"] = np.array([sx * hand_x, arm_y, arm_z])
        a[f"HandTip_{side}"] = np.array([sx * hand_tip_x, arm_y, arm_z])
        a[f"Thigh_{side}"] = np.array([sx * leg_x, hips_y * 0.94, leg_z])
        a[f"Knee_{side}"] = np.array([sx * leg_x, hips_y * 0.52, leg_z])
        a[f"AnkleTrue_{side}"] = np.array([sx * leg_x, max(ankle_y, 0.12), leg_z])
        a[f"Ankle_{side}"] = np.array([sx * leg_x, ankle_y, leg_z])
        a[f"Ball_{side}"] = np.array([sx * leg_x, 0.03, leg_z + 0.10])
        a[f"ToeTip_{side}"] = np.array([sx * leg_x, 0.03, leg_z + 0.22])
    return a


def heat_bones():
    bones = [("Hips", "HipsLow", "Spine2", ""), ("Spine2", "Spine2", "Spine3", "Hips"),
             ("Spine3", "Spine3", "Neck", "Spine2"), ("Neck", "Neck", "Head", "Spine3"),
             ("Head", "Head", "HeadTop", "Neck")]
    for side, _ in SIDES:
        bones += [(f"Clavicle_{side}", f"ClavicleHeat_{side}", f"Shoulder_{side}", "Spine3"),
                  (f"Shoulder_{side}", f"Shoulder_{side}", f"Elbow_{side}", f"Clavicle_{side}"),
                  (f"Elbow_{side}", f"Elbow_{side}", f"Hand_{side}", f"Shoulder_{side}"),
                  (f"Hand_{side}", f"Hand_{side}", f"HandTip_{side}", f"Elbow_{side}"),
                  (f"Thigh_{side}", f"Thigh_{side}", f"Knee_{side}", "Hips"),
                  (f"Knee_{side}", f"Knee_{side}", f"AnkleTrue_{side}", f"Thigh_{side}"),
                  (f"Ankle_{side}", f"AnkleTrue_{side}", f"Ball_{side}", f"Knee_{side}"),
                  (f"Ball_{side}", f"Ball_{side}", f"ToeTip_{side}", f"Ankle_{side}")]
    return bones


def rotation_between(a, b):
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if c < -0.9999:
        return np.diag([1.0, -1.0, -1.0])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx / (1.0 + c)


def segment_transform(js, cs, jl, cl, axial=True, thicken=False):
    ds, dl = cs - js, cl - jl
    rot_row = rotation_between(ds, dl).T
    k = float(np.clip(np.linalg.norm(dl) / max(np.linalg.norm(ds), 1e-6), 0.6, 1.7)) if axial else 1.0
    r = float(np.sqrt(k)) if thicken and k > 1.0 else 1.0
    u = ds / max(np.linalg.norm(ds), 1e-6)
    m = (r * np.eye(3) + (k - r) * np.outer(u, u)) @ rot_row
    return m, jl - js @ m


def bake_transforms(anchors, base_pos):
    specs = {"Hips": ("Hips", "Spine2", "Hips", "Spine2", False),
             "Spine2": ("Spine2", "Spine3", "Spine2", "Spine3", True),
             "Spine3": ("Spine3", "Neck", "Spine3", "Neck", True),
             "Neck": ("Neck", "Head", "Neck", "Head", True)}
    for side, _ in SIDES:
        hand_end = f"Middle1_{side}" if f"Middle1_{side}" in base_pos else f"Hand_{side}"
        specs[f"Clavicle_{side}"] = (f"Clavicle_{side}", f"Shoulder_{side}", f"Clavicle_{side}", f"Shoulder_{side}", True)
        specs[f"Shoulder_{side}"] = (f"Shoulder_{side}", f"Elbow_{side}", f"Shoulder_{side}", f"Elbow_{side}", True)
        specs[f"Elbow_{side}"] = (f"Elbow_{side}", f"Hand_{side}", f"Elbow_{side}", f"Hand_{side}", True)
        if hand_end != f"Hand_{side}":
            specs[f"Hand_{side}"] = (f"Hand_{side}", f"HandTip_{side}", f"Hand_{side}", hand_end, False)
        specs[f"Thigh_{side}"] = (f"Thigh_{side}", f"Knee_{side}", f"Thigh_{side}", pick_bone(base_pos, f"Knee_{side}", f"Knee_Break_{side}"), True)
        specs[f"Knee_{side}"] = (f"Knee_{side}", f"Ankle_{side}", pick_bone(base_pos, f"Knee_{side}", f"Knee_Break_{side}"), pick_bone(base_pos, f"Ankle_{side}", f"Ankle_Break_{side}"), True)
    out = {}
    for bone, (sj, sc, lj, lc, axial) in specs.items():
        limb = bone.startswith(("Shoulder_", "Elbow_", "Thigh_", "Knee_"))
        out[bone] = segment_transform(anchors[sj], anchors[sc], base_pos[lj], base_pos[lc], axial, thicken=limb)
    if "Hand_Left" not in out:
        for side, _ in SIDES:
            out[f"Hand_{side}"] = (np.eye(3), base_pos[f"Hand_{side}"] - anchors[f"Hand_{side}"])
    out["Head"] = (np.eye(3), base_pos["Head"] - anchors["Head"])
    for side, _ in SIDES:
        foot_bone = pick_bone(base_pos, f"Ankle_{side}", f"Ankle_Break_{side}")
        foot = (np.eye(3), base_pos[foot_bone] - anchors[f"Ankle_{side}"])
        out[f"Ankle_{side}"] = foot
        out[f"Ball_{side}"] = foot
    return out


def weld(positions, faces):
    keys = np.round(positions / 1e-5).astype(np.int64)
    _, weld_id, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    inverse = inverse.reshape(-1)
    wverts = positions[weld_id]
    wfaces = inverse[faces]
    ok = (wfaces[:, 0] != wfaces[:, 1]) & (wfaces[:, 1] != wfaces[:, 2]) & (wfaces[:, 0] != wfaces[:, 2])
    return wverts, wfaces, inverse, ok


def run_heat(wverts, wfaces, anchors, tag):
    bones = heat_bones()
    hdir = BUILD / "heat" / tag
    hdir.mkdir(parents=True, exist_ok=True)
    src, dst = hdir / "heat_in.npz", hdir / "heat_out.npz"
    np.savez(src, verts=wverts, faces=wfaces,
             bone_names=np.array([b[0] for b in bones]),
             heads=np.array([anchors[b[1]] for b in bones]),
             tails=np.array([anchors[b[2]] for b in bones]),
             parents=np.array([b[3] for b in bones]))
    res = subprocess.run([str(BLENDER), "-b", "--factory-startup", "--python", str(HEAT_SCRIPT), "--", str(src), str(dst)],
                         capture_output=True, text=True)
    if res.returncode != 0 or not dst.exists():
        raise RuntimeError(f"Blender heat weighting failed for {tag}:\n{res.stdout}\n{res.stderr}")
    weights = np.load(dst)["weights"]
    empty = weights.sum(1) <= 1e-6
    if empty.all():
        print(f"  [heat] Blender bone-heat failed for {tag}; using smooth bone-segment envelope weights")
        # Distance from each vertex to each bone segment [head, tail]
        heads = np.array([anchors[b[1]] for b in bones])
        tails = np.array([anchors[b[2]] for b in bones])
        seg = tails - heads
        seg_len2 = np.maximum((seg * seg).sum(1), 1e-8)
        # (N, B, 3)
        diff = wverts[:, None, :] - heads[None, :, :]
        t = np.clip((diff * seg[None, :, :]).sum(2) / seg_len2[None, :], 0.0, 1.0)
        proj = heads[None, :, :] + t[:, :, None] * seg[None, :, :]
        dist2 = ((wverts[:, None, :] - proj) ** 2).sum(2)
        weights = 1.0 / np.maximum(dist2, 1e-5) ** 2.0
        weights /= weights.sum(1, keepdims=True)
    elif empty.any():
        tree = cKDTree(wverts[~empty])
        _, nearest = tree.query(wverts[empty])
        weights[empty] = weights[~empty][nearest]
    names = [b[0] for b in bones]
    # Remove cross-side weights below hips
    below = wverts[:, 1] < anchors["Hips"][1] * 0.9
    for side, sx in SIDES:
        other = "Right" if side == "Left" else "Left"
        rows = below & (np.sign(wverts[:, 0]) == sx)
        for col, bname in enumerate(names):
            if bname.endswith(other):
                mirror = names.index(bname[:-len(other)] + side)
                weights[rows, mirror] += weights[rows, col]
                weights[rows, col] = 0.0
    return names, weights


def quantize_weights(weights, bone_cols):
    order = np.argsort(-weights, axis=1)[:, :4]
    top = np.take_along_axis(weights, order, 1)
    top = top / np.maximum(top.sum(1, keepdims=True), 1e-9)
    q = np.floor(top * 255).astype(int)
    rem = 255 - q.sum(1)
    frac = top * 255 - q
    for i in np.nonzero(rem)[0]:
        for j in np.argsort(-frac[i])[:rem[i]]:
            q[i, j] += 1
    idx = bone_cols[order]
    idx[q == 0] = idx[:, :1].repeat(4, 1)[q == 0]
    return idx.astype(np.uint8), q.astype(np.uint8)


def vertex_normals(verts, faces):
    fn = np.cross(verts[faces[:, 1]] - verts[faces[:, 0]], verts[faces[:, 2]] - verts[faces[:, 0]])
    n = np.zeros_like(verts)
    for c in range(3):
        np.add.at(n, faces[:, c], fn)
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def vertex_tangents(verts, uvs, normals, faces):
    p0, p1, p2 = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    t0, t1, t2 = uvs[faces[:, 0]], uvs[faces[:, 1]], uvs[faces[:, 2]]
    e1, e2, d1, d2 = p1 - p0, p2 - p0, t1 - t0, t2 - t0
    r = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    r = np.where(np.abs(r) < 1e-12, 1e-12, r)
    sdir = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / r[:, None]
    tdir = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) / r[:, None]
    tan, bit = np.zeros_like(verts), np.zeros_like(verts)
    for c in range(3):
        np.add.at(tan, faces[:, c], sdir)
        np.add.at(bit, faces[:, c], tdir)
    tan = tan - normals * (tan * normals).sum(1, keepdims=True)
    bad = np.linalg.norm(tan, axis=1) < 1e-9
    tan[bad] = np.cross(normals[bad], [0.0, 0.0, 1.0]) + 1e-6
    tan /= np.linalg.norm(tan, axis=1, keepdims=True)
    w = np.where((np.cross(normals, tan) * bit).sum(1) < 0, -1.0, 1.0)
    return tan, w


def write_dds(img: Image.Image, fmt: str, path: Path):
    levels, size = [], img.size
    cur = img
    while True:
        buf = io.BytesIO()
        cur.save(buf, "DDS", pixel_format=fmt)
        levels.append(buf.getvalue()[128:])
        if min(cur.size) <= 4:
            break
        cur = cur.resize((cur.size[0] // 2, cur.size[1] // 2), Image.LANCZOS)
    header = struct.pack("<4s7I44s2I4s5I5I", b"DDS ", 124, 0x000A1007, size[1], size[0],
                         len(levels[0]), 0, len(levels), b"\0" * 44,
                         32, 0x4, fmt.encode(), 0, 0, 0, 0, 0,
                         0x401008, 0, 0, 0, 0)
    path.write_bytes(header + b"".join(levels))


def patch_oct(base_info, n_body: int, idx_count: int, bounds, base_name: str, new_name: str):
    data = bytearray(base_info["oct"])
    i24 = lambda *v: b"".join(struct.pack("<i", x)[:3] for x in v)
    u16 = lambda *v: b"".join(struct.pack("<H", x) for x in v)
    f32 = lambda *v: struct.pack(f"<{len(v)}f", *v)

    n_prims = len(base_info["prims"])
    n_dummy_tris = n_prims - 1
    n_all = n_body + 3 * n_dummy_tris
    s1 = n_all * 16
    new_vbuf_len = n_all * 40
    new_ibuf_len = (idx_count + 3 * n_dummy_tris) * 2

    def replace_unique(old_b, new_b, label):
        pos = data.find(old_b)
        assert pos >= 0, f"missing {label}: {old_b.hex()}"
        assert data.find(old_b, pos + 1) < 0, f"non-unique {label}: {old_b.hex()}"
        data[pos:pos + len(old_b)] = new_b

    def replace_first(old_b, new_b, label):
        pos = data.find(old_b)
        assert pos >= 0, f"missing {label}: {old_b.hex()}"
        data[pos:pos + len(old_b)] = new_b

    replace_unique(i24(base_info["vbuf_len"]), i24(new_vbuf_len), "vbuf_len")
    replace_first(i24(base_info["ibuf_len"]), i24(new_ibuf_len), "ibuf_len")

    # Patch each primitive
    for k, prim in enumerate(base_info["prims"]):
        if k == 0:
            new_vals = (0, 0, 0, idx_count)
            new_vdata = i24(2, n_body, 0, 0, 16, 0, s1, 24)
        else:
            d_idx = k - 1
            v_off_idx = n_body + d_idx * 3
            i_off_bytes = (idx_count + d_idx * 3) * 2
            new_vals = (0, i_off_bytes, v_off_idx, 3)
            new_vdata = i24(2, 3, 0, v_off_idx * 16, 16, 0, s1 + v_off_idx * 24, 24)

        old_i24 = i24(*prim["idata"])
        if all(0 <= x <= 65535 for x in prim["idata"]) and data.find(u16(*prim["idata"])) >= 0:
            old_u16 = u16(*prim["idata"])
            pos_i = data.find(old_u16)
            assert pos_i >= 0
            data[pos_i:pos_i + len(old_u16)] = u16(*new_vals)
        else:
            pos_i = data.find(old_i24)
            assert pos_i >= 0, f"missing idata[{k}]"
            data[pos_i:pos_i + len(old_i24)] = i24(*new_vals)

        old_vdata = i24(*prim["vdata"])
        pos_v = data.find(old_vdata)
        assert pos_v >= 0, f"missing vdata[{k}]"
        data[pos_v:pos_v + len(old_vdata)] = new_vdata

    bmin, bmax, center, radius = bounds
    replace_unique(f32(*base_info["bbox"]), f32(*bmin, *bmax), "bbox")

    # Equal-length material / filename swaps
    lbase, lname = base_name.lower(), new_name.lower()
    out = bytes(data)
    out = out.replace(f"{lbase}_0.vbuf\0".encode(), f"{lname}_0.vbuf\0".encode())
    out = out.replace(f"{lbase}_0.ibuf\0".encode(), f"{lname}_0.ibuf\0".encode())
    out = out.replace(f"{lbase}.mtb\0".encode(), f"{lname}.mtb\0".encode())
    out = out.replace(lbase.encode(), lname.encode())
    return out


def patch_mtb(base_info, spec):
    data = bytearray(base_info["mtb"])
    # In TEXB (bytes 32..144), first two 8-byte entries (separated by ffffffff) are Prim[0] diffuse + normal
    old_diff = data[36:44]
    old_norm = data[48:56]
    new_diff = bytes.fromhex(spec["diff_hash"])
    new_norm = bytes.fromhex(spec["norm_hash"])
    data = bytearray(bytes(data).replace(old_diff, new_diff).replace(old_norm, new_norm))

    # Replace first MATP GUID (36-byte ASCII UUID) and first 4-byte material hash after MATP
    matp = data.find(b"MATP")
    assert matp >= 0
    guids = list(re.finditer(rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", data))
    assert guids, "no GUID in mtb"
    g0 = guids[0]
    data[g0.start():g0.end()] = spec["mat_guid"]
    # Replace first 4-byte hash right after MATP header (at matp + 12 or inside first material block)
    # In DI3 mtb, the 4-byte MATP hash is at the end of the first material entry or near the GUID
    # Let's mutate a unique 4-byte tag or the hash right before the GUID:
    # Actually, let's replace bytes at g0.start()-8..g0.start()-4 if non-zero, or check!
    return bytes(data)


def render_preview(verts, faces, colors, path, joints=None, view="front", size=(600, 800)):
    w, h = size
    s = h / 2.6
    img = Image.new("RGB", size, (24, 24, 28))
    dr = ImageDraw.Draw(img)
    ax, depth, sign = (0, 2, 1.0) if view == "front" else (2, 0, 1.0)
    order = np.argsort(verts[faces].mean(1)[:, depth] * (1 if view == "front" else -1))
    for f in order:
        p = verts[faces[f]]
        dr.polygon([(w / 2 + sign * q[ax] * s, h - 40 - q[1] * s) for q in p], fill=tuple(int(c) for c in colors[f]))
    for pt in joints if joints is not None else []:
        x, y = w / 2 + pt[ax] * s, h - 40 - pt[1] * s
        dr.ellipse([x - 2, y - 2, x + 2, y + 2], outline=(0, 220, 100), width=1)
    img.save(path)


def build_b3_texture_zip(tex_out: Path) -> Path:
    prefix = "b3"
    vanilla = DI3_GAME / "assets" / "textures" / f"{prefix}.zip"
    work = BUILD / f"{prefix}_zip"
    shutil.rmtree(work, ignore_errors=True)
    (work / "extract").mkdir(parents=True)
    subprocess.run([str(QUICKBMS), "-Y", str(BMS_SCRIPT), str(vanilla), str(work / "extract")],
                   check=True, capture_output=True)
    root = work / "extract" / prefix
    for f in tex_out.iterdir():
        shutil.copy2(f, root / "textures" / f.name)
    archive = work / f"{prefix}.zip"
    subprocess.run([str(REENCRYPTER), "whyjustwhy", "-e", ZIP_KEY, str(root), str(archive)],
                   check=True, capture_output=True)
    return archive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-previews", action="store_true")
    args = parser.parse_args()

    shutil.rmtree(MOD_OUT, ignore_errors=True)
    MOD_OUT.mkdir(parents=True)
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    tex_out = BUILD / "textures"
    tex_out.mkdir(parents=True, exist_ok=True)

    actor_patches = []
    datamap_patches = []

    for spec in CHARACTERS:
        name = spec["name"]
        base = spec["base"]
        lname, lbase = name.lower(), base.lower()
        assert len(name) == len(base), (name, base)
        print(f"\n=== Building {name} (from TS3 {spec['ts3_dir']} -> DI3 {base}) ===")

        pos, nrm, faces_n, faces = None, None, None, None
        ts_pos, ts_nrm, ts_uv, ts_faces, atlas = load_ts3_character(spec)
        base_info = load_di3_base(base)

        bone_names = base_info["bone_names"]
        bone_world = base_info["bone_world"]
        base_pos = {bn: bone_world[i][3, :3] for i, bn in enumerate(bone_names)}

        # Compute scale to match DI3 base height (or 1.05x for Zurg)
        ts_h = float(ts_pos[:, 1].max() - ts_pos[:, 1].min())
        di_h = float(base_info["bbox"][4] - base_info["bbox"][1])
        scale = (di_h / ts_h) if spec["scale_to_base"] else 1.0

        anchors = make_tpose_anchors(ts_pos, base_pos, scale)
        scaled_pos = ts_pos * scale

        # Compute winding sign of TS3 mesh
        fn = np.cross(scaled_pos[ts_faces[:, 1]] - scaled_pos[ts_faces[:, 0]],
                      scaled_pos[ts_faces[:, 2]] - scaled_pos[ts_faces[:, 0]])
        ts_sign = float(np.sign(np.median((fn * ts_nrm[ts_faces].mean(1)).sum(1))))

        # Weld and run Blender heat weights
        wverts, wfaces, c2w, ok = weld(scaled_pos, ts_faces)
        ts_faces = ts_faces[ok]
        heat_names, heat_w = run_heat(wverts, wfaces[ok], anchors, lname)
        above_head = wverts[:, 1] >= anchors["Head"][1]
        heat_w[above_head] = 0.0
        heat_w[above_head, heat_names.index("Head")] = 1.0

        # Deduplicate (weld_id, uv)
        uv_key = np.round(ts_uv * 65536).astype(np.int64)
        keys = np.c_[c2w, uv_key]
        _, first, remap = np.unique(keys, axis=0, return_index=True, return_inverse=True)
        remap = remap.reshape(-1)
        vweld = c2w[first]
        vuv = ts_uv[first]
        vfaces = remap[ts_faces]
        if base_info["winding"] * ts_sign < 0:
            vfaces = vfaces[:, ::-1]

        # Bake T-pose into DI3 A-pose
        bake = bake_transforms(anchors, base_pos)
        w = heat_w[vweld]
        tpose = wverts[vweld]
        tnormals = ts_sign * vertex_normals(wverts, wfaces[ok])[vweld]
        baked = np.zeros_like(tpose)
        bnormals = np.zeros_like(tpose)
        for col, bname in enumerate(heat_names):
            if bname not in bake:
                continue
            m, t = bake[bname]
            baked += w[:, col:col + 1] * (tpose @ m + t)
            bnormals += w[:, col:col + 1] * (tnormals @ np.linalg.inv(m).T)
        bnormals /= np.maximum(np.linalg.norm(bnormals, axis=1, keepdims=True), 1e-12)
        tangents, tsign = vertex_tangents(baked, vuv, bnormals, vfaces)

        # Map heat bones -> DI3 skeleton bone indices
        bone_cols = []
        for hn in heat_names:
            if hn in bone_names:
                bone_cols.append(bone_names.index(hn))
            elif hn.startswith("Knee_") and hn.replace("Knee_", "Knee_Break_") in bone_names:
                bone_cols.append(bone_names.index(hn.replace("Knee_", "Knee_Break_")))
            elif hn.startswith("Ankle_") and hn.replace("Ankle_", "Ankle_Break_") in bone_names:
                bone_cols.append(bone_names.index(hn.replace("Ankle_", "Ankle_Break_")))
            elif hn.startswith("Ball_"):
                side = hn.split("_", 1)[1]
                b = pick_bone(base_pos, f"Ball_{side}", f"Toes_{side}", f"Toes_Break_{side}", f"Ankle_{side}", f"Ankle_Break_{side}")
                bone_cols.append(bone_names.index(b))
            elif hn == "Spine2" and "Spine1" in bone_names:
                bone_cols.append(bone_names.index("Spine1"))
            else:
                bone_cols.append(bone_names.index("Hips"))
        bone_cols = np.array(bone_cols)
        idx, wq = quantize_weights(w, bone_cols)

        # Build vbuf + ibuf with 1 hidden degenerate triangle per extra primitive (1..4)
        n_body = len(baked)
        n_dummy_tris = len(base_info["prims"]) - 1
        n_dummy_verts = n_dummy_tris * 3
        head_c = base_pos["Head"] + np.array([0.0, 0.05, 0.0])
        dummy_pos = np.repeat(head_c[None], n_dummy_verts, 0)
        all_pos = np.vstack([baked, dummy_pos]).astype("<f4")
        all_uv = np.vstack([vuv, np.zeros((n_dummy_verts, 2))]).astype("<f2")
        all_idx = np.vstack([idx, np.full((n_dummy_verts, 4), bone_names.index("Head"), np.uint8)])
        all_w = np.vstack([wq, np.tile(np.array([255, 0, 0, 0], np.uint8), (n_dummy_verts, 1))])
        all_n = np.vstack([bnormals, np.tile([0.0, 0.0, 1.0], (n_dummy_verts, 1))])
        all_t = np.vstack([np.c_[tangents, tsign], np.tile([1.0, 0.0, 0.0, 1.0], (n_dummy_verts, 1))])

        s0 = np.zeros((len(all_pos), 16), np.uint8)
        s0[:, :12] = all_pos.view(np.uint8).reshape(-1, 12)
        s0[:, 12:] = all_uv.view(np.uint8).reshape(-1, 4)
        s1 = np.zeros((len(all_pos), 24), np.uint8)
        s1[:, 0:4] = all_idx
        s1[:, 4:8] = all_w
        s1[:, 8:16] = np.c_[all_n, np.zeros(len(all_n))].astype("<f2").view(np.uint8).reshape(-1, 8)
        s1[:, 16:24] = all_t.astype("<f2").view(np.uint8).reshape(-1, 8)
        vbuf = s0.tobytes() + s1.tobytes()

        dummy_indices = np.arange(n_body, n_body + n_dummy_verts, dtype="<u2")
        indices = np.r_[vfaces.reshape(-1), dummy_indices].astype("<u2")
        assert n_body + n_dummy_verts < 65536

        bmin, bmax = baked.min(0), baked.max(0)
        center = (bmin + bmax) / 2.0
        radius = float(np.linalg.norm(baked - center, axis=1).max())

        char_dir = MOD_OUT / "characters" / lname
        char_dir.mkdir(parents=True, exist_ok=True)
        (char_dir / f"{lname}_0.vbuf").write_bytes(vbuf)
        (char_dir / f"{lname}_0.ibuf").write_bytes(indices.tobytes())
        (char_dir / f"{lname}.oct").write_bytes(patch_oct(base_info, n_body, vfaces.size, (bmin, bmax, center, radius), base, name))
        (char_dir / f"{lname}.mtb").write_bytes(patch_mtb(base_info, spec))
        (char_dir / f"{lname}.animtreeoverrides").write_bytes(base_info["anim"])

        # Patch .bent and .dnax
        bent = base_info["bent"].replace(f"characters/{lbase}/{lbase}.oct\0".encode(), f"characters/{lname}/{lname}.oct\0".encode())
        (char_dir / f"{lname}.bent").write_bytes(bent)

        model_path = f"characters/{name}/{name}".encode()
        dnax = re.sub(rb"characters/" + lbase.encode() + rb"/" + lbase.encode() + rb"(?=\x00)", model_path, base_info["dnax"], flags=re.IGNORECASE)
        dnax = re.sub(rb"characters/" + lbase.encode() + rb"/" + lbase.encode() + rb"\.animtreeoverrides(?=\x00)", model_path + b".animtreeoverrides", dnax, flags=re.IGNORECASE)
        (char_dir / f"{lname}.dnax").write_bytes(dnax)

        # Write textures
        write_dds(atlas, "DXT1", tex_out / f"{spec['diff_hash']}.tbody")
        write_dds(Image.new("RGBA", (256, 256), FLAT_NORMAL_RGBA), "DXT5", tex_out / f"{spec['norm_hash']}.tbody")

        # Write characters/<Name>.lua
        (MOD_OUT / "characters" / f"{name}.lua").write_text(
            f"-- {name} (Toy Story 3 port, cloned from {base})\n"
            "Crabe.VirtualReader.exposeCharacter({\n"
            f'    Name = "{name}",\n'
            f'    ProgressionTree = "{spec["prog_tree"]}",\n'
            "})\n", encoding="utf-8")

        actor_patches.append((lbase, lname, name))
        datamap_patches.append((lbase, lname))

        if not args.no_previews:
            atl = np.array(atlas)
            fuv = vuv[vfaces].mean(1)
            colors = atl[(np.clip(fuv[:, 1], 0, 0.999) * ATLAS_SIZE).astype(int),
                         (np.clip(fuv[:, 0], 0, 0.999) * ATLAS_SIZE).astype(int)]
            render_preview(baked, vfaces, colors, PREVIEWS / f"{lname}_front.png", joints=list(base_pos.values()), view="front")
            render_preview(baked, vfaces, colors, PREVIEWS / f"{lname}_side.png", joints=list(base_pos.values()), view="side")

        print(f"  OK: {n_body} verts, {len(vfaces)} tris, bbox={bmin.round(2)}..{bmax.round(2)}")

    # Build textures/b3.zip
    print("\n=== Packing textures/b3.zip ===")
    b3_zip = build_b3_texture_zip(tex_out)
    (MOD_OUT / "textures").mkdir(parents=True, exist_ok=True)
    shutil.copy2(b3_zip, MOD_OUT / "textures" / "b3.zip")

    # Write skilltrees/ActorList.patch & DataMap.patch
    skill_dir = MOD_OUT / "skilltrees"
    skill_dir.mkdir(parents=True, exist_ok=True)

    actor_lines = [
        "-- Registers T3_Woody, T3_Jessie, T3_Buzz, T3_Zurg in ActorList.",
        "if Data and Data.DBName == \"ActorList\" and Data.Records then",
        "    local recs = Data.Records",
        "    local existing = {}",
        "    local i = 1",
        "    while i <= #recs do",
        "        if recs[i] and recs[i].Name then existing[recs[i].Name] = true end",
        "        i = i + 1",
        "    end",
    ]
    for lbase, lname, name in actor_patches:
        actor_lines.append(
            f'    if existing["{lbase}"] and not existing["{lname}"] then '
            f'recs[#recs + 1] = {{ Parms = "DNAFile=characters/{name}/{name}.dnax", Type = "Avatar", Name = "{lname}" }} '
            f'existing["{lname}"] = true end'
        )
    actor_lines.append("end")
    (skill_dir / "ActorList.patch").write_text("\n".join(actor_lines) + "\n", encoding="utf-8")

    dm_lines = [
        "-- Clones DataMap.Actor and DataMap.Costume preload entries for TS3 characters.",
        "local function cloneFiles(files, from, to)",
        "    local out = {}",
        "    local i = 1",
        "    while files and i <= #files do",
        "        local v = files[i]",
        "        if v == from then v = to end",
        "        out[i] = v",
        "        i = i + 1",
        "    end",
        "    return out",
        "end",
        "local A = DataMap and DataMap.Actor",
        "local C = DataMap and DataMap.Costume",
    ]
    for lbase, lname in datamap_patches:
        dm_lines.extend([
            f'if A and A["{lbase}"] and not A["{lname}"] then',
            f'    local src = A["{lbase}"]',
            f'    A["{lname}"] = {{ Includes = src.Includes, Files = cloneFiles(src.Files, "DNA|characters/{lbase}", "DNA|characters/{lname}/{lname}") }}',
            "end",
            f'if C and C["{lbase}_default"] and not C["{lname}_default"] then',
            f'    local src = C["{lbase}_default"]',
            f'    C["{lname}_default"] = {{ Files = cloneFiles(src.Files, "Entity|characters/{lbase}/{lbase}", "Entity|characters/{lname}/{lname}") }}',
            "end",
        ])
    (skill_dir / "DataMap.patch").write_text("\n".join(dm_lines) + "\n", encoding="utf-8")

    # Copy mod.json and ts3_characters.lua into MOD_OUT
    shutil.copy2(MOD_ROOT / "mod.json", MOD_OUT / "mod.json")
    shutil.copy2(MOD_ROOT / "ts3_characters.lua", MOD_OUT / "ts3_characters.lua")
    print(f"\n[DONE] Mod built cleanly at: {MOD_OUT}")


if __name__ == "__main__":
    main()
