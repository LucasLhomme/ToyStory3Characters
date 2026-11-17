"""build_ts3_characters.py - Import Woody, Jessie, Buzz, and Zurg from Toy Story 3 into Disney Infinity 3.0.

Uses TS3's authentic artist-authored SkinRemap bone weights and BoneToModelMatrix
skeleton directly (no guessed bounding-box heuristics or bone-heat artifacts),
rotates only the arms from TS3's T-pose into DI3's A-pose via volume-preserving
axis-angle blending, and maps TS3 bones 1-to-1 onto the DI3 base skeleton.
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
from PIL import Image
from scipy.spatial.transform import Rotation

MOD_ROOT = Path(__file__).resolve().parent
# The tool folders (CrabeLoader/tools, DisneyInfinityCreator) sit next to this repo; a CI checkout elsewhere sets DIM2_ROOT
DIM2 = Path(os.environ.get("DIM2_ROOT", MOD_ROOT.parent))
TOOLS = DIM2 / "CrabeLoader" / "tools"
QUICKBMS = TOOLS / "QuickBMS" / "quickbms.exe"
BMS_SCRIPT = TOOLS / "QuickBMS" / "disney_infinity_new.bms"
REENCRYPTER = TOOLS / ".ZIP Reencrypter" / "offsetting.exe"
ZIP_KEY = "681BBEEA63160188F9B7945104A51499"
C2DITOOLS_SRC = TOOLS / "Cars2TheVideoGameModding" / "src"

TS3_GAME = Path(r"D:\Jeu\Crack\Toy Story 3 Complete Edition")
DI3_GAME = Path(r"D:\SteamLibrary\steamapps\common\Disney Infinity 3.0 Gold Edition")

BUILD = MOD_ROOT / "build"
RAW = BUILD / "raw"
MOD_OUT = BUILD / "mod"

ATLAS_SIZE = 1024
ATLAS_PAD = 8
FLAT_NORMAL_RGBA = (255, 127, 140, 127)

CHARACTERS = [
    {
        "name": "T3_Woody",
        "base": "TS_Woody",
        "actor_key": "av_woody",
        "parms_extra": "",
        "ts3_dir": "woody",
        "ts3_zip": "woody.zip",
        "skip_mats": {"EyeLid_RightUpper1", "EyeLid_RightLower1", "EyeLid_LeftUpper1", "EyeLid_LeftLower1"},
        "mat_guid": b"b3000001-0001-4000-8000-000000000001",
        "mat_hash": bytes.fromhex("b3010001"),
        "diff_hash": "b301000100000001",
        "norm_hash": "b301000200000002",
        "prog_tree": "IN1_TS_Woody",
        "scale_to_base": True,
    },
    {
        "name": "T3_Jessie",
        "base": "TS_Jessie",
        "actor_key": "av_jessie",
        "parms_extra": "",
        "ts3_dir": "jessie",
        "ts3_zip": "jessie.zip",
        "skip_mats": {"_EyeLid_Upper_RIGHT", "_EyeLid_Lower_RIGHT", "_EyeLid_Upper_LEFT", "_EyeLid_Lower_LEFT"},
        "double_sided_mats": {"jessie_hairhat4", "hat_Jessie_HatPiping"},
        "mat_guid": b"b3000002-0002-4000-8000-000000000002",
        "mat_hash": bytes.fromhex("b3020001"),
        "diff_hash": "b302000100000003",
        "norm_hash": "b302000200000004",
        "prog_tree": "IN1_TS_Jessie",
        "scale_to_base": True,
    },
    {
        "name": "T3_Buzz",
        "base": "TS_Buzz",
        "actor_key": "av_buzz",
        "parms_extra": "; modelDef=Avatar_Buzz_Classic;",
        "ts3_dir": "buzzlightyear",
        "ts3_zip": "buzzlightyear.zip",
        "skip_mats": {"Dome3", "wings_n_such_Buzz_bindpose2_JetPack_Flame4", "wings_n_such_Buzz_bindpose2_JetPack_Flame3"},
        "mat_guid": b"b3000003-0003-4000-8000-000000000003",
        "mat_hash": bytes.fromhex("b3030001"),
        "diff_hash": "b303000100000005",
        "norm_hash": "b303000200000006",
        "prog_tree": "IN1_TS_Buzz",
        "scale_to_base": True,
    },
    {
        "name": "T3_Zurg",
        "base": "TS_Buzz",
        "actor_key": "av_buzz",
        "parms_extra": "; modelDef=Avatar_Buzz_Classic;",
        "ts3_dir": "zurgavatar",
        "ts3_zip": "zurgavatar.zip",
        "skip_mats": {"Smokey_plastic", "Zurg_gun_mat", "Yellow_ball"},
        "mat_guid": b"b3000004-0004-4000-8000-000000000004",
        "mat_hash": bytes.fromhex("b3040001"),
        "diff_hash": "b304000100000007",
        "norm_hash": "b304000200000008",
        "prog_tree": "IN1_TS_Buzz",
        "scale_to_base": True,
        # Buzz's skeleton put Zurg's joints 40-70 cm off; the DI1 TS_Zurg rig has his proportions (body = prim 2)
        "model_base": "TS_Zurg",
        "body_prim": 2,
        "body_tex": (4, 5),
        "retarget_arms": True,
        # Buzz's actor auto-starts ABILITY_BUZZ = the jetpack on his back; Zurg has none (POWERUP_BUZZ is AutoStartTool5)
        "dnax_replace": {b"ABILITY_BUZZ": b"POWERUP_BUZZ"},
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


def map_ts3_bone_to_di3(ts3_bname: str, di3_bones: list[str]) -> str:
    """Maps a TS3 bone name to the closest matching bone name in the DI3 skeleton."""
    def pick(*cands):
        for c in cands:
            if c in di3_bones:
                return c
        return "Hips"

    # Side detection (TS3 uses both FooLeft and FooLeft1 / FooLeftDown / FooUpLeft)
    side = None
    if "Left" in ts3_bname:
        side = "Left"
    elif "Right" in ts3_bname:
        side = "Right"

    # Direct torso / head / rigid attachments
    if ts3_bname in ("Body", "Hips", "Main", "Origin", "Holster", "LassoAttach", "PullRing", "Knee", "Foot"):
        return pick("Hips")
    if ts3_bname == "Spine1":
        return pick("Spine1", "Hips")
    if ts3_bname == "Spine2":
        return pick("Spine2", "Spine1", "Hips")
    if ts3_bname in ("Spine3", "BackPackButton", "PackReloader", "CapeAdjust", "Cape1", "Cape2", "Cape3", "Cape4") or ts3_bname.startswith(("Wing", "Dome", "Flame", "Sternum", "Tube")):
        return pick("Spine3", "Spine2", "Spine1", "Hips")
    if ts3_bname == "Neck":
        return pick("Neck", "Spine3")
    # Facial bones (Jaw, lips, brows) + Jessie's hair braid match DI3's skeleton
    low = ts3_bname.lower()
    if low == "jaw":
        return pick("Jaw", "Head")
    if low == "upperlip":
        return pick("UpperLip", "Head")
    if low == "lowerlip":
        return pick("LowerLip", "Head")
    if low == "lipcornerleft":
        return pick("CornerLipLeft", "Head")
    if low == "lipcornerright":
        return pick("CornerLipRight", "Head")
    if low == "browleft":
        return pick("BrowLeft", "Head")
    if low == "browright":
        return pick("BrowRight", "Head")
    if low in ("middlebrow", "midddlebrow", "browmiddle"):
        return pick("BrowCenter", "Head")
    if low == "hair1":
        return pick("Hair01", "Head")
    if low in ("hair2", "hair3", "hair4"):
        return pick("Hair02", "Hair01", "Head")
    if ts3_bname in ("Head", "Hat") or low.startswith(("eye", "lid")):
        return pick("Head", "Neck")

    if side:
        if ts3_bname.startswith("Clavicle"):
            return pick(f"Clavicle_{side}", "Spine3")
        if ts3_bname.startswith("Bicep"):
            return pick(f"Shoulder_{side}")
        if ts3_bname.startswith(("Forearm", "Elbow")):
            return pick(f"Elbow_{side}")
        if ts3_bname.startswith(("Hand", "Carry")):
            return pick(f"Hand_{side}")
        if ts3_bname.startswith(("Index", "Middle", "Ring", "Pinky", "Thumb")):
            for fbase in ("Index1", "Middle1", "Thumb1"):
                if ts3_bname.startswith(fbase[:4]) and f"{fbase}_{side}" in di3_bones:
                    return f"{fbase}_{side}"
            return pick(f"Hand_{side}")
        if ts3_bname.startswith(("Leg", "Thigh")):
            return pick(f"Thigh_{side}")
        if ts3_bname.startswith("Calf"):
            return pick(f"Knee_{side}", f"Knee_Break_{side}")
        if ts3_bname.startswith("Foot"):
            return pick(f"Ankle_{side}", f"Ankle_Break_{side}")
        if ts3_bname.startswith("Toe"):
            return pick(f"Toes_{side}", f"Ball_{side}", f"Toes_Break_{side}", f"Ankle_{side}", f"Ankle_Break_{side}")

    if ts3_bname in ("Laser", "LightningRod", "GunHandle", "Revolver"):
        return pick("Hand_Right")
    return pick("Hips")


def load_ts3_character(spec):
    """Extracts TS3 character zip, decodes .oct, and reads authentic geometry + SkinRemap weights."""
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

    # Build BoneID -> (NodeName, BoneToModelPosition)
    ts3_bones_by_id = {}
    ts3_bone_pos = {}
    for node in root.findall("./SceneTreeNodePool/*"):
        bid = node.findtext("BoneID")
        if bid is not None:
            bname = node.findtext("NodeName")
            ts3_bones_by_id[int(bid)] = bname
            m_el = node.find("BoneToModelMatrix")
            if m_el is not None:
                vals = [float(e.text) for e in m_el.findall("entry")]
                if len(vals) == 16:
                    ts3_bone_pos[bname] = np.array(vals).reshape(4, 4)[3, :3]

    scs = root.findall("./SceneComponentPool/*")
    texs = [t.findtext("Name") for t in root.findall("./TexturePool/*")]
    mats = []
    for m in root.findall("./MaterialPool/*"):
        trefs = [int(t.text) for t in m.findall(".//TextureReference")]
        mats.append((m.findtext("Name"), [texs[i] for i in trefs if i < len(texs)]))

    vstreams = root.findall("./VertexStreamPool/*")
    istreams = root.findall("./IndexStreamPool/*")

    prims = []
    used_tex_names = []
    for el in root.iter():
        mr = el.find("MaterialReference")
        vsr = el.find("VertexStreamReferences")
        isr = el.find("IndexStreamReference")
        scr = el.find("SceneComponentReferences")
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
        remap = []
        if scr is not None:
            sc_id = int(scr.find("entry").text)
            remap = [int(x.text) for x in scs[sc_id].findall("./RemapData/entry")]
        prims.append((vs_ids, is_id, tex_name, remap, mat_name))
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
    all_bnames, all_bweights = [], []  # per-vertex 4 bone names + 4 uint8 weights
    v_base = 0
    for vs_ids, is_id, tex_name, remap, mat_name in prims:
        vs0 = vstreams[vs_ids[0]]
        nv = int(vs0.findtext("Length"))
        w0 = int(vs0.findtext("Width"))
        off0 = int(vs0.findtext("VertexBufferOffset") or 0)
        raw0 = np.frombuffer(vbuf[off0:off0 + nv * w0], np.uint8).reshape(nv, w0)
        pos = raw0[:, :12].copy().view("<f4").reshape(nv, 3)
        bidx_local = raw0[:, 12:16]
        bweights = raw0[:, 16:20]
        uv_raw = raw0[:, 20:24].copy().view("<f2").reshape(nv, 2).astype(float)
        uv = np.where((uv_raw < -0.01) | (uv_raw > 1.01), np.mod(uv_raw, 1.0), np.clip(uv_raw, 0.0, 1.0))
        rx, ry, rw, rh = rects[tex_name]
        au = (rx + ATLAS_PAD + uv[:, 0] * (rw - 2 * ATLAS_PAD)) / ATLAS_SIZE
        av = (ry + ATLAS_PAD + uv[:, 1] * (rh - 2 * ATLAS_PAD)) / ATLAS_SIZE

        if len(vs_ids) > 1:
            vs1 = vstreams[vs_ids[1]]
            w1 = int(vs1.findtext("Width"))
            off1 = int(vs1.findtext("VertexBufferOffset") or 0)
            raw1 = np.frombuffer(vbuf[off1:off1 + nv * w1], np.uint8).reshape(nv, w1)
            nrm = (raw1[:, 8:14] if w1 == 24 else raw1[:, :6]).copy().view("<f2").reshape(nv, 3).astype(float)
        else:
            nrm = np.tile([0.0, 0.0, 1.0], (nv, 1))

        is_el = istreams[is_id]
        ni = int(is_el.findtext("Length"))
        ioff = int(is_el.findtext("IndexBufferOffset") or 0)
        tris = np.frombuffer(ibuf[ioff:ioff + ni * 2], "<u2").reshape(-1, 3).astype(int)

        used_v, inv = np.unique(tris, return_inverse=True)
        tris_local = inv.reshape(-1, 3) + v_base

        # Resolve local bone indices via SkinRemap -> BoneID -> NodeName
        remap_arr = np.array(remap, dtype=int) if remap else np.arange(256)
        v_bidx = bidx_local[used_v]
        v_bw = bweights[used_v]
        v_bnames = []
        for row_idx in v_bidx:
            v_bnames.append([ts3_bones_by_id.get(int(remap_arr[int(li)]), "Hips") if int(li) < len(remap_arr) else "Hips" for li in row_idx])

        all_pos.append(pos[used_v])
        all_nrm.append(nrm[used_v])
        all_uv.append(np.stack([au[used_v], av[used_v]], 1))
        all_faces.append(tris_local)
        all_bnames.extend(v_bnames)
        all_bweights.append(v_bw)
        v_base += len(used_v)

        # TS3 drew these double-sided; DI3 culls back faces, so add a reversed copy (Jessie's hat brim from below)
        if mat_name in spec.get("double_sided_mats", ()):
            all_pos.append(pos[used_v])
            all_nrm.append(-nrm[used_v])
            all_uv.append(all_uv[-1])
            all_faces.append(tris_local[:, ::-1] + len(used_v))
            all_bnames.extend(v_bnames)
            all_bweights.append(v_bw)
            v_base += len(used_v)

    return (
        np.vstack(all_pos),
        np.vstack(all_nrm),
        np.vstack(all_uv),
        np.vstack(all_faces),
        all_bnames,
        np.vstack(all_bweights),
        ts3_bone_pos,
        atlas,
    )


def load_di3_base(base_name: str):
    """Extracts DI3 base character and parses skeleton, primitives, and winding."""
    lbase = base_name.lower()
    work = RAW / "di3" / lbase
    loose = DI3_GAME / "assets" / "characters" / lbase
    exts = (".oct", ".mtb", ".bent", "_0.vbuf", "_0.ibuf", ".animtreeoverrides")
    if not (work / f"{lbase}.xml").exists():
        if (loose / f"{lbase}_0.vbuf").exists():
            # Model-only bases such as TS_Zurg ship loose files and have no dnax in characters.zip
            work.mkdir(parents=True, exist_ok=True)
            for ext in exts:
                shutil.copy2(loose / f"{lbase}{ext}", work / f"{lbase}{ext}")
        else:
            z = loose / f"{lbase}.zip"
            for ext in exts:
                quickbms_extract(z, f"*{lbase}{ext}", work)
            quickbms_extract(DI3_GAME / "assets" / "characters" / "characters.zip", f"*{lbase}.dnax", work / "dnax")
        oct_file = next(work.rglob(f"{lbase}.oct"))
        run_c2di_dec(oct_file, work / f"{lbase}.xml")

    oct_file = next(work.rglob(f"{lbase}.oct"))
    mtb_file = next(work.rglob(f"{lbase}.mtb"))
    bent_file = next(work.rglob(f"{lbase}.bent"))
    vbuf_file = next(work.rglob(f"{lbase}_0.vbuf"))
    ibuf_file = next(work.rglob(f"{lbase}_0.ibuf"))
    anim_file = next(work.rglob(f"{lbase}.animtreeoverrides"))
    dnax_file = next((work / "dnax").rglob(f"{lbase}.dnax"), None)

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
        "dnax": dnax_file.read_bytes() if dnax_file else None,
        "bone_names": names,
        "bone_world": np.array(world),
        "prims": prims,
        "bbox": bbox,
        "winding": winding,
    }


def rotate_arms_to_apose(pos: np.ndarray, nrm: np.ndarray, ts_bnames: list[list[str]], ts_bw: np.ndarray, ts3_bone_pos: dict[str, np.ndarray], di3_pos: dict[str, np.ndarray], scale: float):
    """Rotates ONLY vertices weighted to TS3 arm bones from T-pose into DI3's A-pose.

    Uses volume-preserving axis-angle interpolation around each Bicep joint so
    torso, wings, chestplates, capes, head, and legs remain 100% undeformed.
    """
    out_pos = pos.copy()
    out_nrm = nrm.copy()

    for side in ("Left", "Right"):
        bicep_key = f"Bicep{side}"
        hand_key = f"Hand{side}"
        if bicep_key not in ts3_bone_pos or hand_key not in ts3_bone_pos:
            continue
        pivot = ts3_bone_pos[bicep_key] * scale
        ts_vec = (ts3_bone_pos[hand_key] - ts3_bone_pos[bicep_key]) * scale
        di_vec = di3_pos[f"Hand_{side}"] - di3_pos[f"Shoulder_{side}"]
        ts_u = ts_vec / np.linalg.norm(ts_vec)
        di_u = di_vec / np.linalg.norm(di_vec)
        rot_full, _ = Rotation.align_vectors([di_u], [ts_u])
        rotvec = rot_full.as_rotvec()

        # Sum vertex weight belonging to this side's arm (Bicep, Forearm, Elbow, Hand, fingers)
        arm_prefixes = ("Bicep", "Forearm", "Elbow", "Hand", "Index", "Middle", "Ring", "Pinky", "Thumb", "Carry")
        w_arm = np.zeros(len(pos), dtype=float)
        for vi, (bn_row, bw_row) in enumerate(zip(ts_bnames, ts_bw)):
            s = 0.0
            for bn, bw in zip(bn_row, bw_row):
                if bw > 0 and (side in bn) and bn.startswith(arm_prefixes):
                    s += float(bw) / 255.0
            w_arm[vi] = min(s, 1.0)

        mask = w_arm > 1e-4
        if np.any(mask):
            # Apply per-vertex axis-angle rotation around Bicep pivot
            rots = Rotation.from_rotvec(rotvec[None, :] * w_arm[mask, None])
            out_pos[mask] = rots.apply(out_pos[mask] - pivot) + pivot
            out_nrm[mask] = rots.apply(out_nrm[mask])

    return out_pos, out_nrm


def detail_parent(di3_bone: str):
    if di3_bone.startswith(("Jaw", "UpperLip", "LowerLip", "CornerLip", "Brow", "Hair")):
        return "Head"
    if di3_bone.startswith(("Index1_", "Middle1_", "Thumb1_")):
        return "Hand_" + di3_bone.split("_")[-1]
    return None


# Maps every TS3 bone to a DI3 bone. A face/finger/hair bone whose DI3 pivot sits more than 3 cm from the posed
# TS3 joint goes to its parent (Head/Hand) instead: animating it would swing the vertices around a far pivot
# (twisted mouth on Jessie, flying claws on Zurg). Moving the mesh onto the DI3 joints was measured to tear Zurg.
def ts3_bone_map(ts_bnames, joints, di3_pos, bone_names):
    mapped = {bn: map_ts3_bone_to_di3(bn, bone_names) for row in ts_bnames for bn in row}
    for tb, db in mapped.items():
        parent = detail_parent(db)
        if parent and (tb not in joints or np.linalg.norm(joints[tb] - di3_pos[db]) > 0.03):
            mapped[tb] = parent
    return mapped


# Hand edits made in Blender (tools/blender_export_edits.py): vertex positions and skin weights that replace the
# computed ones. Vertex order must be unchanged, so only moving vertices and painting weights are allowed.
def load_hand_edit(lname: str, n_body: int, bone_names: list[str]):
    path = MOD_ROOT / "edits" / f"{lname}.npz"
    if not path.exists():
        return None
    e = np.load(path)
    assert len(e["pos"]) == n_body, f"{path}: {len(e['pos'])} vertices, build has {n_body} (vertices added or deleted?)"
    assert [str(s) for s in e["bone_names"]] == bone_names, f"{path}: skeleton differs from the build"
    return e


# Maps segment a->b onto a2->b2: rotate onto the new direction and stretch along the axis only (thickness kept).
def _segment_map(pts, a, b, a2, b2):
    d, d2 = b - a, b2 - a2
    rot, _ = Rotation.align_vectors([d2 / np.linalg.norm(d2)], [d / np.linalg.norm(d)])
    u = d / np.linalg.norm(d)
    rel = pts - a
    rel = rel + np.outer(rel @ u, u) * (np.linalg.norm(d2) / np.linalg.norm(d) - 1.0)
    return a2 + rot.apply(rel)


# Puts the TS3 shoulder, elbow and wrist exactly on the DI3 Shoulder/Elbow/Hand bones so the arm pieces pivot where
# the DI3 animations expect (Zurg's hands flew off his forearms 26-30 cm otherwise). Upper arm maps shoulder->elbow,
# forearm and hand map elbow->wrist; vertices blend by skin weight, everything else stays put. Returns new joints too.
def retarget_arms(pos, ts_bnames, ts_bw, joints, di3_pos):
    out = pos.copy()
    joints = dict(joints)
    for side in ("Left", "Right"):
        s, e, h = (f"Bicep{side}", f"Forearm{side}", f"Hand{side}")
        if not all(k in joints for k in (s, e, h)):
            continue
        S2, E2, H2 = di3_pos[f"Shoulder_{side}"], di3_pos[f"Elbow_{side}"], di3_pos[f"Hand_{side}"]
        upper = _segment_map(pos, joints[s], joints[e], S2, E2)
        fore = _segment_map(pos, joints[e], joints[h], E2, H2)
        w_up = np.zeros(len(pos))
        w_fo = np.zeros(len(pos))
        for vi, (row, w) in enumerate(zip(ts_bnames, ts_bw)):
            for bn, wt in zip(row, w):
                if side not in bn or not wt:
                    continue
                if bn.startswith("Bicep"):
                    w_up[vi] += wt / 255.0
                elif bn.startswith(("Forearm", "Elbow", "Hand", "Carry", "Index", "Middle", "Ring", "Pinky", "Thumb")):
                    w_fo[vi] += wt / 255.0
        out += w_up[:, None] * (upper - pos) + w_fo[:, None] * (fore - pos)
        names = [k for k in joints if side in k and k.startswith(("Hand", "Carry", "Index", "Middle", "Ring", "Pinky", "Thumb"))]
        moved = _segment_map(np.array([joints[k] for k in names]), joints[e], joints[h], E2, H2)
        joints.update(dict(zip(names, moved)))
        joints[s], joints[e] = S2, E2
    return out, joints


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


# Writes a PC .tbody (DDS). The engine reads dwCaps3 bit0 as sRGB and dwCaps4 as the U/V/W wrap mode.
# Leaving both at 0 gives washed-out colours and an invalid sampler; 0x49 = Wrap on all axes, as on vanilla textures.
def write_dds(img: Image.Image, fmt: str, path: Path, srgb: bool):
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
                         0x401008, 0, int(srgb), 0x49, 0)
    path.write_bytes(header + b"".join(levels))


OLD_OCT_VDATA_TAG = bytes.fromhex("1a16a20308")


# Numbers live in the oct data section, but older octs (TS_Zurg) keep them earlier: retry past the string table.
def patch_oct(base_info, n_body: int, idx_count: int, bounds, base_name: str, new_name: str, body_prim: int = 0):
    raw = base_info["oct"]
    str_sz, _, data_sz = struct.unpack_from("<III", raw, 12)
    try:
        return _patch_oct(base_info, n_body, idx_count, bounds, base_name, new_name, body_prim, len(raw) - data_sz)
    except AssertionError:
        return _patch_oct(base_info, n_body, idx_count, bounds, base_name, new_name, body_prim, str_sz + 64)


def _patch_oct(base_info, n_body, idx_count, bounds, base_name, new_name, body_prim, data_start):
    raw = bytearray(base_info["oct"])
    str_sz = struct.unpack_from("<I", raw, 12)[0]
    head_part = bytearray(raw[:data_start])
    data_part = bytearray(raw[data_start:])

    i24 = lambda *v: b"".join(struct.pack("<i", x)[:3] for x in v)
    u16 = lambda *v: b"".join(struct.pack("<H", x) for x in v)
    f32 = lambda *v: struct.pack(f"<{len(v)}f", *v)

    n_prims = len(base_info["prims"])
    n_dummy_tris = n_prims - 1
    n_all = n_body + 3 * n_dummy_tris
    s1 = n_all * 16
    new_vbuf_len = n_all * 40
    new_ibuf_len = (idx_count + 3 * n_dummy_tris) * 2

    def replace_in_data(old_b, new_b, label):
        pos = data_part.find(old_b)
        assert pos >= 0, f"missing {label} in oct data section: {old_b.hex()}"
        assert data_part.find(old_b, pos + 1) < 0, f"non-unique {label} in oct data section: {old_b.hex()}"
        data_part[pos:pos + len(old_b)] = new_b

    replace_in_data(i24(base_info["vbuf_len"]), i24(new_vbuf_len), "vbuf_len")
    replace_in_data(i24(base_info["ibuf_len"]), i24(new_ibuf_len), "ibuf_len")

    for k, prim in enumerate(base_info["prims"]):
        if k == body_prim:
            new_vals = (0, 0, 0, idx_count)
            new_vdata = i24(2, n_body, 0, 0, 16, 0, s1, 24)
        else:
            d_idx = k if k < body_prim else k - 1
            v_off_idx = n_body + d_idx * 3
            i_off_bytes = (idx_count + d_idx * 3) * 2
            new_vals = (0, i_off_bytes, v_off_idx, 3)
            new_vdata = i24(2, 3, 0, v_off_idx * 16, 16, 0, s1 + v_off_idx * 24, 24)

        old_vdata = i24(*prim["vdata"])
        pos_v = data_part.find(old_vdata)
        assert pos_v >= 0 and data_part.count(old_vdata) == 1, f"vdata[{k}] not found exactly once"
        old_u16 = u16(*prim["idata"]) if all(0 <= x <= 65535 for x in prim["idata"]) else None
        if data_part[pos_v - 5:pos_v] == OLD_OCT_VDATA_TAG:
            # Older octs (TS_Zurg) store idata right before vdata with a per-list int width (u8/u16/i24).
            # A dummy whose values overflow that width draws the degenerate triangle at the head of the ibuf.
            w = None
            for width in (1, 2, 3):
                st = pos_v - 5 - 4 * width
                if data_part[st - 5] == 0x1a and data_part[st - 4] == 0x13 + width and data_part[st - 3:st] == b"\xa1\x03\x04":
                    w = width
            assert w, f"idata[{k}] tag not found before vdata"
            st = pos_v - 5 - 4 * w
            if k != body_prim and max(new_vals) >= 1 << (8 * w - (w == 3)):
                new_vals = (0, 0, 0, 3)
                new_vdata = i24(2, n_body, 0, 0, 16, 0, s1, 24)
            assert max(new_vals) < 1 << (8 * w - (w == 3)), f"idata[{k}] {new_vals} does not fit {w} bytes"
            data_part[st:st + 4 * w] = b"".join(x.to_bytes(w, "little", signed=(w == 3)) for x in new_vals)
        elif old_u16 and data_part.count(old_u16) == 1:
            pos_i = data_part.find(old_u16)
            data_part[pos_i:pos_i + len(old_u16)] = u16(*new_vals)
        else:
            old_i24 = i24(*prim["idata"])
            assert data_part.count(old_i24) == 1, f"idata[{k}] not found exactly once: {prim['idata']}"
            pos_i = data_part.find(old_i24)
            data_part[pos_i:pos_i + len(old_i24)] = i24(*new_vals)

        data_part[pos_v:pos_v + len(old_vdata)] = new_vdata

    bmin, bmax, center, radius = bounds
    replace_in_data(f32(*base_info["bbox"]), f32(*bmin, *bmax), "bbox")

    # Rename strings strictly within the string table
    lbase, lname = base_name.lower(), new_name.lower()
    str_end = min(len(head_part), str_sz + 64)
    str_sec = bytes(head_part[:str_end])
    str_sec = str_sec.replace(f"{lbase}_0.vbuf\0".encode(), f"{lname}_0.vbuf\0".encode())
    str_sec = str_sec.replace(f"{lbase}_0.ibuf\0".encode(), f"{lname}_0.ibuf\0".encode())
    str_sec = str_sec.replace(f"{lbase}.mtb\0".encode(), f"{lname}.mtb\0".encode())
    str_sec = str_sec.replace(lbase.encode(), lname.encode())
    head_part[:str_end] = str_sec
    return bytes(head_part) + bytes(data_part)


# Points the body material (index body_prim, textures body_tex in TEXB) at the TS3 atlas and gives it a unique GUID/hash.
def patch_mtb(base_info, spec):
    data = bytearray(base_info["mtb"])
    body = spec.get("body_prim", 0)
    t_diff, t_norm = spec.get("body_tex", (0, 1))
    old_diff = data[36 + 12 * t_diff:44 + 12 * t_diff]
    old_norm = data[36 + 12 * t_norm:44 + 12 * t_norm]
    new_diff = bytes.fromhex(spec["diff_hash"])
    new_norm = bytes.fromhex(spec["norm_hash"])
    data = bytearray(bytes(data).replace(old_diff, new_diff).replace(old_norm, new_norm))

    guids = list(re.finditer(rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", data))
    assert guids, "no GUID in mtb"
    g0 = guids[body]
    data[g0.start():g0.end()] = spec["mat_guid"]

    # Patch materialHashes[0] in MATP so characters cloned from the same base (or vanilla)
    # do not collide in Material::FindCachedMaterial(uint32_t hash).
    matp = data.find(b"MATP")
    assert matp >= 0, "missing MATP in mtb"
    num_mats = struct.unpack_from("<I", data, matp + 12)[0]
    pos = guids[-1].end() + 1  # past trailing NUL of last GUID
    pos = (pos + 1) & ~1       # align 2 for uint16 shaderIds[num_mats]
    pos_hashes = (pos + num_mats * 2 + 3) & ~3  # align 4 for uint32 materialHashes[num_mats]
    data[pos_hashes + 4 * body:pos_hashes + 4 * body + 4] = spec["mat_hash"]
    return bytes(data)


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


CREATOR = DIM2 / "DisneyInfinityCreator"
ICONS = BUILD / "icons"


# Virtual Reader pictures (figure, small, glowing silhouette) from build/icons/<char>_body.png (tools/render_icons.py).
# The game reads ui/virtualreadercharacters only from its archive, so the mod ships a rebuilt copy: vanilla entries
# byte for byte + ours, built and read back by DisneyInfinityCreator's shared._build (same layout as its own icons).
def build_reader_icons():
    sys.path.insert(0, str(CREATOR))
    from src.core import icon, shared

    files = {}
    stage = BUILD / "icons_stage"
    shutil.rmtree(stage, ignore_errors=True)
    for spec in CHARACTERS:
        lname = spec["name"].lower()
        body = ICONS / f"{lname}_body.png"
        if not body.exists():
            print(f"  no icon render for {lname} (run tools/render_icons.py)")
            continue
        figure = icon.figure_icon(Image.open(body))
        pictures = {lname: figure, f"{lname}_sm": figure.resize((icon.SMALL_SIZE,) * 2, Image.LANCZOS),
                    f"{lname}_s": icon.silhouette_icon(figure)}
        for name, pic in pictures.items():
            tex = icon.texture_hash(name)
            (stage / "textures").mkdir(parents=True, exist_ok=True)
            (stage / f"{name}.mtb").write_bytes(icon.ui_bundle(name, tex))
            (stage / "textures" / f"{tex}.tbody").write_bytes(icon.dds_single(pic))
            files[f"virtualreadercharacters/{name}.mtb"] = stage / f"{name}.mtb"
            files[f"virtualreadercharacters/textures/{tex}.tbody"] = stage / "textures" / f"{tex}.tbody"
    # Character grid tile av_<icon suffix> (256 px bust) and HUD portrait hud_playericons_<name> (128 px head):
    # the grid derives its tile from the row's Icon (HUD_PlayerIcons_Buzz -> av_buzz), both live in icons.zip.
    grid = {}
    for spec in CHARACTERS:
        lname = spec["name"].lower()
        bust, head = ICONS / f"{lname}_bust.png", ICONS / f"{lname}_head.png"
        if not (bust.exists() and head.exists()):
            print(f"  no portrait render for {lname} (run tools/render_portraits.py)")
            continue
        tile = Image.open(bust).convert("RGBA").resize((256, 256), Image.LANCZOS)
        framed = icon._glow(tile.getchannel("A"), 3)
        framed.alpha_composite(tile)
        for name, pic in ((f"av_{lname}", framed), (icon.hud_name(spec["name"]).lower(), icon.hud_icon(Image.open(head)))):
            tex = icon.texture_hash(name)
            (stage / "textures").mkdir(parents=True, exist_ok=True)
            (stage / f"{name}.mtb").write_bytes(icon.ui_bundle(name, tex))
            (stage / "textures" / f"{tex}.tbody").write_bytes(icon.dds_single(pic))
            grid[f"icons/{name}.mtb"] = stage / f"{name}.mtb"
            grid[f"icons/textures/{tex}.tbody"] = stage / "textures" / f"{tex}.tbody"

    for stem, ours in (("virtualreadercharacters", files), ("icons", grid)):
        if not ours:
            continue
        vanilla = DI3_GAME / "assets" / "ui" / stem / f"{stem}.zip"
        out = MOD_OUT / "ui" / stem / f"{stem}.zip"
        n = shared._build(vanilla, stem, ours, out, BUILD / "icons_work", print)
        print(f"  {stem}.zip: {n} entries ({len(ours)} ours)")


def main():
    parser = argparse.ArgumentParser()
    parser.parse_args()

    shutil.rmtree(MOD_OUT, ignore_errors=True)
    MOD_OUT.mkdir(parents=True)
    tex_out = MOD_OUT / "textures"
    tex_out.mkdir(parents=True, exist_ok=True)

    actor_patches = []
    datamap_patches = []

    for spec in CHARACTERS:
        name = spec["name"]
        base = spec["base"]
        lname, lbase = name.lower(), base.lower()
        # model_base gives the skeleton/oct/mtb/bent; base keeps the dnax, anim overrides and actor data
        model = spec.get("model_base", base)
        lmodel = model.lower()
        assert len(name) == len(base) == len(model), (name, base, model)
        print(f"\n=== Building {name} (from TS3 {spec['ts3_dir']} -> DI3 {model}, actor {base}) ===")

        ts_pos, ts_nrm, ts_uv, ts_faces, ts_bnames, ts_bw, ts3_bone_pos, atlas = load_ts3_character(spec)
        base_info = load_di3_base(model)
        if model != base:
            actor_info = load_di3_base(base)
            base_info["dnax"], base_info["anim"], base_info["bent"] = actor_info["dnax"], actor_info["anim"], actor_info["bent"]

        bone_names = base_info["bone_names"]
        bone_world = base_info["bone_world"]
        di3_pos = {bn: bone_world[i][3, :3] for i, bn in enumerate(bone_names)}

        ts_h = float(ts_pos[:, 1].max() - ts_pos[:, 1].min())
        di_h = float(base_info["bbox"][4] - base_info["bbox"][1])
        scale = (di_h / ts_h) if spec["scale_to_base"] else 1.0

        # Scale uniformly and place feet at y=0
        scaled_pos = ts_pos * scale
        floor = float(scaled_pos[:, 1].min())
        scaled_pos[:, 1] -= floor

        # Rotate ONLY the arms from T-pose into DI3's A-pose
        baked, bnormals = rotate_arms_to_apose(scaled_pos, ts_nrm, ts_bnames, ts_bw, ts3_bone_pos, di3_pos, scale)

        # Pose the TS3 joints exactly like the mesh to compare them with the DI3 pivots
        jn = list(ts3_bone_pos)
        jp = np.array([ts3_bone_pos[k] for k in jn]) * scale
        jposed, _ = rotate_arms_to_apose(jp, np.zeros_like(jp), [[k] * 4 for k in jn],
                                         np.tile([255, 0, 0, 0], (len(jn), 1)), ts3_bone_pos, di3_pos, scale)
        jposed[:, 1] -= floor
        joints = dict(zip(jn, jposed))
        if spec.get("retarget_arms"):
            baked, joints = retarget_arms(baked, ts_bnames, ts_bw, joints, di3_pos)
        bone_map = ts3_bone_map(ts_bnames, joints, di3_pos, bone_names)
        bnormals /= np.maximum(np.linalg.norm(bnormals, axis=1, keepdims=True), 1e-12)

        # Check winding sign against DI3 base
        fn = np.cross(baked[ts_faces[:, 1]] - baked[ts_faces[:, 0]], baked[ts_faces[:, 2]] - baked[ts_faces[:, 0]])
        ts_sign = float(np.sign(np.median((fn * bnormals[ts_faces].mean(1)).sum(1))))
        vfaces = ts_faces[:, ::-1] if (base_info["winding"] * ts_sign < 0) else ts_faces
        if base_info["winding"] * ts_sign < 0:
            bnormals = -bnormals

        edit = load_hand_edit(lname, len(baked), bone_names)
        if edit is not None:
            baked = edit["pos"].astype(float)

        # Recompute smooth vertex normals & tangents on the posed mesh, guarding against zero-area faces
        recomp = vertex_normals(baked, vfaces) * base_info["winding"]
        zero_recomp = np.linalg.norm(recomp, axis=1) < 1e-4
        bnormals = np.where(zero_recomp[:, None], bnormals, recomp)
        zero_all = np.linalg.norm(bnormals, axis=1) < 1e-4
        bnormals[zero_all] = [0.0, 0.0, 1.0]
        bnormals /= np.maximum(np.linalg.norm(bnormals, axis=1, keepdims=True), 1e-12)
        tangents, tsign = vertex_tangents(baked, ts_uv, bnormals, vfaces)

        # Map TS3 bone names directly to DI3 skeleton indices
        bone_cache = {}
        idx = np.zeros_like(ts_bw, dtype=np.uint8)
        for vi, row in enumerate(ts_bnames):
            for k, bn in enumerate(row):
                if bn not in bone_cache:
                    bone_cache[bn] = bone_names.index(bone_map[bn])
                idx[vi, k] = bone_cache[bn]
        wq = ts_bw.astype(np.uint8)
        if edit is not None:
            idx, wq = edit["bidx"].astype(np.uint8), edit["bw"].astype(np.uint8)
            print(f"  hand edit applied: edits/{lname}.npz")

        n_body = len(baked)
        n_dummy_tris = len(base_info["prims"]) - 1
        n_dummy_verts = n_dummy_tris * 3
        head_c = di3_pos["Head"] + np.array([0.0, 0.05, 0.0])
        dummy_pos = np.repeat(head_c[None], n_dummy_verts, 0)
        all_pos = np.vstack([baked, dummy_pos]).astype("<f4")
        all_uv = np.vstack([ts_uv, np.zeros((n_dummy_verts, 2))]).astype("<f2")
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
        # Leading degenerate triangle: a dummy prim that cannot address its own triangle draws this one (see patch_oct)
        body_indices = np.r_[[0, 0, 0], vfaces.reshape(-1)]
        indices = np.r_[body_indices, dummy_indices].astype("<u2")
        assert n_body + n_dummy_verts < 65536

        bmin, bmax = baked.min(0), baked.max(0)
        center = (bmin + bmax) / 2.0
        radius = float(np.linalg.norm(baked - center, axis=1).max())

        char_dir = MOD_OUT / "characters" / lname
        char_dir.mkdir(parents=True, exist_ok=True)
        (char_dir / f"{lname}_0.vbuf").write_bytes(vbuf)
        (char_dir / f"{lname}_0.ibuf").write_bytes(indices.tobytes())
        (char_dir / f"{lname}.oct").write_bytes(patch_oct(base_info, n_body, body_indices.size, (bmin, bmax, center, radius), model, name, spec.get("body_prim", 0)))
        (char_dir / f"{lname}.mtb").write_bytes(patch_mtb(base_info, spec))
        (char_dir / f"{lname}.animtreeoverrides").write_bytes(base_info["anim"])

        old_oct = f"characters/{lbase}/{lbase}.oct\0".encode()
        assert old_oct in base_info["bent"], f"{old_oct} missing from {base}.bent"
        bent = base_info["bent"].replace(old_oct, f"characters/{lname}/{lname}.oct\0".encode())
        (char_dir / f"{lname}.bent").write_bytes(bent)

        model_path = f"characters/{name}/{name}".encode()
        dnax = re.sub(rb"characters/" + lbase.encode() + rb"/" + lbase.encode() + rb"(?=\x00)", model_path, base_info["dnax"], flags=re.IGNORECASE)
        dnax = re.sub(rb"characters/" + lbase.encode() + rb"/" + lbase.encode() + rb"\.animtreeoverrides(?=\x00)", model_path + b".animtreeoverrides", dnax, flags=re.IGNORECASE)
        # Same-length constant swaps in the Lua bytecode (lengths are stored, so sizes must match)
        for old, new in spec.get("dnax_replace", {}).items():
            assert len(old) == len(new) and dnax.count(old) == 1, (name, old, new)
            dnax = dnax.replace(old, new)
        (char_dir / f"{lname}.dnax").write_bytes(dnax)

        write_dds(atlas, "DXT1", tex_out / f"{spec['diff_hash']}.tbody", srgb=True)
        write_dds(Image.new("RGBA", (256, 256), FLAT_NORMAL_RGBA), "DXT5", tex_out / f"{spec['norm_hash']}.tbody", srgb=False)

        (MOD_OUT / "characters" / f"{name}.lua").write_text(
            f"-- {name} (Toy Story 3 port, cloned from {base})\n"
            "Crabe.VirtualReader.exposeCharacter({\n"
            f'    Name = "{name}",\n'
            f'    ProgressionTree = "{spec["prog_tree"]}",\n'
            f'    Icon = "HUD_PlayerIcons_{name}",\n'
            "})\n", encoding="utf-8")

        actor_patches.append((spec["actor_key"], lname, name, spec["parms_extra"]))
        datamap_patches.append((spec["actor_key"], lbase, lname))
        print(f"  OK: {n_body} verts, {len(vfaces)} tris, bbox={bmin.round(2)}..{bmax.round(2)}")

    print(f"\n=== Loose textures written to {tex_out} (CrabeLoader V2 VFS) ===")

    skill_dir = MOD_OUT / "skilltrees"
    skill_dir.mkdir(parents=True, exist_ok=True)

    actor_lines = [
        "-- Registers T3_Woody, T3_Jessie, T3_Buzz, T3_Zurg in ActorList.",
        "-- Note: DI 1.0 Toy Story actors use av_woody / av_jessie / av_buzz in ActorList.",
        "if Data and Data.DBName == \"ActorList\" and Data.Records then",
        "    local recs = Data.Records",
        "    local existing = {}",
        "    local i = 1",
        "    while i <= #recs do",
        "        if recs[i] and recs[i].Name then existing[recs[i].Name] = true end",
        "        i = i + 1",
        "    end",
    ]
    for actor_key, lname, name, parms_extra in actor_patches:
        actor_lines.append(
            f'    if existing["{actor_key}"] and not existing["{lname}"] then '
            f'recs[#recs + 1] = {{ Parms = "DNAFile=characters/{name}/{name}.dnax{parms_extra}", Type = "Avatar", Name = "{lname}" }} '
            f'existing["{lname}"] = true end'
        )
    actor_lines.append("end")
    (skill_dir / "ActorList.patch").write_text("\n".join(actor_lines) + "\n", encoding="utf-8")

    dm_lines = [
        "-- Clones DataMap.Actor preload entries for TS3 characters.",
        "local function cloneFiles(files, lbase, lname)",
        "    local out = {}",
        "    local dnaFrom = \"DNA|characters/\" .. lbase",
        "    local dnaTo = \"DNA|characters/\" .. lname .. \"/\" .. lname",
        "    local entFrom = \"Entity|characters/\" .. lbase .. \"/\" .. lbase",
        "    local entTo = \"Entity|characters/\" .. lname .. \"/\" .. lname",
        "    local hasEnt = false",
        "    local i = 1",
        "    while files and i <= #files do",
        "        local v = files[i]",
        "        if v == dnaFrom then v = dnaTo end",
        "        if v == entFrom then v = entTo hasEnt = true end",
        "        out[i] = v",
        "        i = i + 1",
        "    end",
        "    if not hasEnt then out[#out + 1] = entTo end",
        "    return out",
        "end",
        "local A = DataMap and DataMap.Actor",
    ]
    for actor_key, lbase, lname in datamap_patches:
        dm_lines.extend([
            f'if A and A["{actor_key}"] and not A["{lname}"] then',
            f'    local src = A["{actor_key}"]',
            f'    A["{lname}"] = {{ Includes = src.Includes, Files = cloneFiles(src.Files, "{lbase}", "{lname}") }}',
            "end",
        ])
    (skill_dir / "DataMap.patch").write_text("\n".join(dm_lines) + "\n", encoding="utf-8")

    build_reader_icons()

    shutil.copy2(MOD_ROOT / "mod.json", MOD_OUT / "mod.json")
    shutil.copy2(MOD_ROOT / "ts3_characters.lua", MOD_OUT / "ts3_characters.lua")
    print(f"\n[DONE] Mod built cleanly at: {MOD_OUT}")


if __name__ == "__main__":
    main()
