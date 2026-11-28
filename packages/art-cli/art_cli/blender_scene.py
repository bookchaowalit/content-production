"""Blender side of `art 3d`. Run as:
    blender -b --factory-startup --python blender_scene.py -- '<json job>'

Job keys: mode (turntable|shot|logo), model, svg, out_dir, size [w,h], frames, samples, engine,
angles, color, metallic, roughness, extrude, elevation, lens.
Renders RGBA PNGs with a shadow-catcher floor; the CLI composites them over the background.
"""
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

job = json.loads(sys.argv[sys.argv.index("--") + 1])
out = Path(job["out_dir"])
out.mkdir(parents=True, exist_ok=True)
W, H = job.get("size", [1080, 1080])


def log(*a):
    print("ART3D", *a, flush=True)


# ------------------------------------------------------------------ clean scene
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


# ------------------------------------------------------------------ import
def import_model(path: str) -> list:
    before = set(bpy.data.objects)
    ext = Path(path).suffix.lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == ".stl":
        bpy.ops.wm.stl_import(filepath=path)
    elif ext == ".ply":
        bpy.ops.wm.ply_import(filepath=path)
    elif ext in (".usd", ".usda", ".usdc", ".usdz"):
        bpy.ops.wm.usd_import(filepath=path)
    elif ext == ".svg":
        bpy.ops.import_curve.svg(filepath=path)
    elif ext == ".blend":
        with bpy.data.libraries.load(path) as (src, dst):
            dst.objects = list(src.objects)
        for o in dst.objects:
            # keep the file's own render visibility: hidden helpers, rigs and collections stay out
            if o is not None and o.type not in ("CAMERA", "LIGHT") and not o.hide_render \
                    and not any(c.hide_render for c in o.users_collection):
                scene.collection.objects.link(o)
    else:
        raise SystemExit(f"unsupported model type {ext}")
    new = [o for o in bpy.data.objects if o not in before]
    for o in new:  # imported cameras/lights would fight the studio rig
        if o.type in ("CAMERA", "LIGHT"):
            bpy.data.objects.remove(o, do_unlink=True)
    return [o for o in bpy.data.objects if o not in before]


def make_material(color, metallic, roughness):
    m = bpy.data.materials.new("art_mat")
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    c = color.lstrip("#")
    rgb = [int(c[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    rgb = [(v / 12.92) if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]  # sRGB -> linear
    bsdf.inputs["Base Color"].default_value = (*rgb, 1)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    return m


if job["mode"] == "logo":
    objs = import_model(job["svg"])
    curves = [o for o in objs if o.type == "CURVE"]
    if not curves:
        raise SystemExit("SVG produced no curves (text must be converted to paths)")
    mat = make_material(job.get("color", "#f59e0b"), job.get("metallic", 0.25), job.get("roughness", 0.35))
    bpy.context.view_layer.update()
    span = max(max((o.matrix_world @ Vector(c)).x for o in curves for c in o.bound_box)
               - min((o.matrix_world @ Vector(c)).x for o in curves for c in o.bound_box), 1e-6)
    depth = job.get("depth", 0.08) * span / 2  # extrude is half the thickness
    for o in curves:
        o.data.extrude = depth
        o.data.bevel_depth = depth * 0.12
        o.data.bevel_resolution = 3
        o.data.materials.clear()
        o.data.materials.append(mat)
        o.rotation_euler = (math.radians(90), 0, 0)  # stand the logo up, facing -Y (the camera)
    # bake curves to one mesh once, so frames do not re-evaluate bevels
    bpy.ops.object.select_all(action="DESELECT")
    for o in curves:
        o.select_set(True)
    bpy.context.view_layer.objects.active = curves[0]
    bpy.ops.object.convert(target="MESH")
    bpy.ops.object.join()
    logo_obj = bpy.context.view_layer.objects.active
    bpy.ops.object.shade_auto_smooth() if hasattr(bpy.ops.object, "shade_auto_smooth") else None
    objs = [logo_obj]
else:
    objs = import_model(job["model"])
    if job.get("color"):
        mat = make_material(job["color"], job.get("metallic", 0.0), job.get("roughness", 0.45))
        for o in objs:
            if o.type == "MESH" and not o.data.materials:
                o.data.materials.append(mat)

bpy.context.view_layer.update()

# ------------------------------------------------------------------ normalise: centre, floor at z=0, max size 2
geo = [o for o in objs if o.type in ("MESH", "CURVE", "FONT", "SURFACE", "META")]
if not geo:
    raise SystemExit("model has no visible geometry")
pts = [o.matrix_world @ Vector(c) for o in geo for c in o.bound_box]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
dims = hi - lo
scale = 2.0 / max(dims.x, dims.y, dims.z, 1e-6)
pivot = bpy.data.objects.new("art_pivot", None)
scene.collection.objects.link(pivot)
pivot.location = ((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z)
bpy.context.view_layer.update()
for o in objs:
    if o.parent is None:
        mw = o.matrix_world.copy()
        o.parent = pivot
        o.matrix_parent_inverse = pivot.matrix_world.inverted()
        o.matrix_world = mw
pivot.location = (0, 0, 0)
pivot.scale = (scale, scale, scale)
bpy.context.view_layer.update()
height = dims.z * scale
radius = math.hypot(dims.x, dims.y) * scale / 2
log("normalised", [round(v * scale, 3) for v in dims])

# ------------------------------------------------------------------ studio
floor_mesh = bpy.data.meshes.new("floor")
floor_mesh.from_pydata([(-50, -50, 0), (50, -50, 0), (50, 50, 0), (-50, 50, 0)], [], [(0, 1, 2, 3)])
floor = bpy.data.objects.new("floor", floor_mesh)
scene.collection.objects.link(floor)
floor.is_shadow_catcher = True

world = bpy.data.worlds.new("studio")
scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.8, 0.8, 0.82, 1)
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.6

target = Vector((0, 0, height * 0.5))


def add_light(name, energy, size, az, el, dist=6.0):
    ld = bpy.data.lights.new(name, "AREA")
    ld.energy, ld.size = energy, size
    lo_ = bpy.data.objects.new(name, ld)
    scene.collection.objects.link(lo_)
    a, e = math.radians(az), math.radians(el)
    lo_.location = target + Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e))) * dist
    lo_.rotation_euler = (target - lo_.location).to_track_quat("-Z", "Y").to_euler()


k = job.get("light", 0.4 if job["mode"] == "logo" else 1.0)  # Standard view clips sooner than AgX
add_light("key", 900 * k, 3.0, -40, 45)
add_light("fill", 300 * k, 4.0, 50, 20)
add_light("rim", 600 * k, 2.0, 170, 35)

cam_data = bpy.data.cameras.new("cam")
cam_data.lens = job.get("lens", 50)
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
aspect = W / H
sensor_half = math.atan((cam_data.sensor_width / 2) / cam_data.lens)  # horizontal half-FOV
v_half = math.atan(math.tan(sensor_half) / aspect) if aspect >= 1 else sensor_half
h_half = math.atan(math.tan(v_half) * aspect)
def fit_distance(el_deg):
    """Distance that keeps the model in frame from this elevation.

    A spinning model sweeps a circle of `radius`: its projected width is 2*radius, and seen from
    elevation e its projected height is height*cos(e) + 2*radius*sin(e) (top face foreshortened in).
    """
    e = math.radians(abs(el_deg))
    half_h = (height * math.cos(e) + 2 * radius * math.sin(e)) / 2
    return max(radius / math.tan(h_half), half_h / math.tan(v_half)) * job.get("fit", 1.1) + radius * 0.6


def place_camera(az_deg, el_deg):
    a, e = math.radians(az_deg), math.radians(el_deg)
    cam.location = target + Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e))) * fit_distance(el_deg)
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()


# ------------------------------------------------------------------ render settings
r = scene.render
r.resolution_x, r.resolution_y, r.resolution_percentage = W, H, 100
r.film_transparent = True
r.use_persistent_data = True  # keep BVH/textures between frames
# brand colours must stay exact on logos; AgX (default) suits photoreal models
scene.view_settings.view_transform = job.get("view", "Standard" if job["mode"] == "logo" else "AgX")
r.image_settings.file_format = "PNG"
r.image_settings.color_mode = "RGBA"
engine = job.get("engine", "cycles")
if engine == "eevee":
    r.engine = "BLENDER_EEVEE"
    floor.hide_render = True  # shadow catchers are Cycles-only; EEVEE would draw a solid plane
else:
    r.engine = "CYCLES"
    cy = scene.cycles
    cy.samples = job.get("samples", 64)
    cy.use_denoising = True
    cy.denoising_use_gpu = True
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.compute_device_type = "CUDA"
        prefs.refresh_devices()
        gpus = [d for d in prefs.devices if d.type == "CUDA"]
        for d in prefs.devices:
            d.use = d.type == "CUDA"
        cy.device = "GPU" if gpus else "CPU"
    except Exception as e:  # noqa: BLE001
        cy.device = "CPU"
        log("gpu unavailable", e)
    log("device", cy.device)

elev = job.get("elevation", 18)

# ------------------------------------------------------------------ modes
if job["mode"] in ("turntable", "logo"):
    n = job.get("frames", 72)
    place_camera(0, elev)
    scene.frame_start, scene.frame_end = 1, n
    swing = job.get("swing", 30 if job["mode"] == "logo" else 0)
    if swing:  # rock -swing..+swing and back: text never shows its mirrored back
        for f, deg in ((1, -swing), (n // 2 + 1, swing), (n + 1, -swing)):
            pivot.rotation_euler = (0, 0, math.radians(deg))
            pivot.keyframe_insert("rotation_euler", index=2, frame=f)
    else:
        pivot.rotation_euler = (0, 0, 0)
        pivot.keyframe_insert("rotation_euler", index=2, frame=1)
        pivot.rotation_euler = (0, 0, math.radians(360))
        pivot.keyframe_insert("rotation_euler", index=2, frame=n + 1)
    act = pivot.animation_data.action
    try:
        fcurves = act.fcurves
    except AttributeError:  # slotted actions (4.4+)
        fcurves = act.layers[0].strips[0].channelbags[0].fcurves
    for fc in fcurves:
        for k in fc.keyframe_points:
            k.interpolation = "SINE" if swing else "LINEAR"
            if swing:
                k.easing = "EASE_IN_OUT"
    r.filepath = str(out / "f_")
    bpy.ops.render.render(animation=True)
    log("frames", n)
else:  # shot
    angles = job.get("angles", [[-35, 20], [0, 8], [35, 35]])
    for i, (az, el) in enumerate(angles, 1):
        place_camera(az, el)
        r.filepath = str(out / f"shot_{i:02d}.png")
        bpy.ops.render.render(write_still=True)
    log("shots", len(angles))
