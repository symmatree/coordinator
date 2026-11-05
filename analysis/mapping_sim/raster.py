"""Rasterise a scene's triangles on the GPU, the way a game draws a frame, then bend the result
through the lens.

Two passes per image:

1. **Faces.** Five pinhole views from the camera's optical centre -- along the optical axis and
   90 degrees right, left, down and up of it -- the faces of a cube with the back one left out,
   which no lens under 180 degrees reaches. Each is an ordinary vertex and fragment shader pass
   over the scene's triangles (`Scene.triangles`) with a depth buffer. The fragment shader
   evaluates the same texture `Texture.sample` does at the interpolated world position: the same
   hash, lattice rotations, offsets and per-solid seeds, octaves finer than the face pixel's
   footprint on the surface (from the screen-space derivatives of world position) faded out, and
   the same Siemens stars. Faces are rendered at the lens centre's pixel density, so a face pixel
   is never larger than the image pixel it ends up in, and they are mipmapped.
2. **Lens.** A full-screen pass at the sensor's size: each pixel's ray from
   `KannalaBrandt.unproject` (here in GLSL), the face it leaves through, and a trilinear,
   anisotropic sample of that face with the footprint of the neighbouring pixels' rays, so the
   resampling filters to the image pixel rather than point-sampling the faces.

Later effects belong after pass 2, on the sensor's grid and in the order a sensor sees them:
several pass-2 images at sub-frame times for rolling shutter and motion blur, defocus from a depth
attachment, then noise and exposure.

The context is headless EGL. On a machine with a GPU (lancer's Radeon, through `/dev/dri`) EGL's
first device is that GPU; without one it is Mesa's llvmpipe, which runs the same shaders on the CPU
and is how the tests run in CI.
"""

from __future__ import annotations

import moderngl
import numpy as np

from lens import KannalaBrandt
from scene import Box, Scene

MAX_OCTAVES = 16
NEAR_M, FAR_M = 0.05, 40000.0
# Faces reach this far past 45 degrees so the anisotropic filter's footprint at a seam stays on
# the face it is sampling.
FACE_PAD = 0.02

# Camera frame is OpenCV (x right, y down, z forward). Each face's rows are its right, down and
# forward axes in the camera frame: front, right, left, down, up.
FACES = np.array(
    [
        [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        [[0, 0, -1], [0, 1, 0], [1, 0, 0]],
        [[0, 0, 1], [0, 1, 0], [-1, 0, 0]],
        [[1, 0, 0], [0, 0, -1], [0, 1, 0]],
        [[1, 0, 0], [0, 0, 1], [0, -1, 0]],
    ],
    dtype=float,
)

FACE_VS = """
#version 430
uniform mat3 R_fw;
uniform vec3 centre;
uniform float pad_scale;
in vec3 in_pos;
in int in_solid;
in vec2 in_uv;
in float in_star;
out vec3 v_world;
out vec2 v_uv;
out float v_z;
flat out int v_solid;
flat out float v_star;
void main() {
    vec3 p = R_fw * (in_pos - centre);
    v_world = in_pos;
    v_uv = in_uv;
    v_z = p.z;
    v_solid = in_solid;
    v_star = in_star;
    const float a = (%(far)r + %(near)r) / (%(far)r - %(near)r);
    const float b = -2.0 * %(far)r * %(near)r / (%(far)r - %(near)r);
    // Image right is +x and image down is +y; GL's y is up.
    gl_Position = vec4(p.x * pad_scale, -p.y * pad_scale, a * p.z + b, p.z);
}
""" % {"near": NEAR_M, "far": FAR_M}

FACE_FS = """
#version 430
uniform int n_oct;
uniform mat3 rot[%(max_oct)d];
uniform vec3 off[%(max_oct)d];
uniform float lam[%(max_oct)d];
uniform float amp[%(max_oct)d];
uniform float contrast;
in vec3 v_world;
in vec2 v_uv;
in float v_z;
flat in int v_solid;
flat in float v_star;
out float value;

const int STAR_SECTOR_PAIRS = %(sectors)d;

// scene._hash, on the same uint32 arithmetic.
float lattice(ivec3 i, uint seedmix) {
    uint h = uint(i.x) * 0x8DA6B343u;
    h ^= uint(i.y) * 0xD8163841u;
    h ^= uint(i.z) * 0xCB1AB31Fu;
    h ^= seedmix;
    h ^= h >> 15u;
    h *= 0x2C1B3C6Du;
    h ^= h >> 12u;
    h *= 0x297A2D39u;
    h ^= h >> 15u;
    return float(h) / 2147483648.0 - 1.0;
}

// scene.value_noise.
float value_noise(vec3 p, uint seedmix) {
    vec3 fl = floor(p);
    vec3 f = p - fl;
    ivec3 i = ivec3(fl);
    vec3 s = f * f * (3.0 - 2.0 * f);
    float out_v = 0.0;
    for (int dx = 0; dx < 2; dx++)
        for (int dy = 0; dy < 2; dy++)
            for (int dz = 0; dz < 2; dz++) {
                float w = (dx == 1 ? s.x : 1.0 - s.x) * (dy == 1 ? s.y : 1.0 - s.y) * (dz == 1 ? s.z : 1.0 - s.z);
                out_v += w * lattice(i + ivec3(dx, dy, dz), seedmix);
            }
    return out_v;
}

void main() {
    float fp = max(max(length(dFdx(v_world)), length(dFdy(v_world))), 1e-9);
    // Texture.sample, with solid_seed = solid index + 1 as Scene.shade passes it.
    float v = 0.0;
    for (int o = 0; o < n_oct; o++) {
        float w = clamp(lam[o] / fp / 2.0 - 1.0, 0.0, 1.0);
        if (w <= 0.0) continue;
        vec3 q = rot[o] * v_world / lam[o] + off[o];
        uint seed = uint(v_solid + 1) * 1000003u + uint(o);
        v += amp[o] * w * value_noise(q, seed * 0x9E3779B9u);
    }
    value = 0.5 + 0.5 * tanh(contrast * v);
    // Box.decal.
    float r = length(v_uv);
    if (v_star > 0.0 && r <= v_star) {
        float s = sin(float(STAR_SECTOR_PAIRS) * atan(v_uv.y, v_uv.x));
        float edge = clamp(r * s / (float(STAR_SECTOR_PAIRS) * fp), -1.0, 1.0);
        float period = 2.0 * 3.141592653589793 * r / float(STAR_SECTOR_PAIRS);
        float fade = clamp(period / fp / 2.0 - 1.0, 0.0, 1.0);
        value = 0.5 + 0.5 * fade * edge;
    }
    // Logarithmic depth: float precision across 5 cm to 40 km.
    gl_FragDepth = log(v_z / %(near)r) / log(%(far)r / %(near)r);
}
""" % {"max_oct": MAX_OCTAVES, "sectors": Box.STAR_SECTOR_PAIRS, "near": NEAR_M, "far": FAR_M}

LENS_VS = """
#version 430
in vec2 in_xy;
void main() { gl_Position = vec4(in_xy, 0.0, 1.0); }
"""

LENS_FS = """
#version 430
uniform vec2 f;
uniform vec2 c;
uniform vec4 k;
uniform float height;
uniform float pad_scale;
uniform float sky;
uniform mat3 faces[5];
uniform sampler2D face0;
uniform sampler2D face1;
uniform sampler2D face2;
uniform sampler2D face3;
uniform sampler2D face4;
out float value;

// KannalaBrandt.unproject: Newton's method on theta_d(theta) from the equidistant answer.
vec3 ray(vec2 uv) {
    vec2 m = (uv - c) / f;
    float td = length(m);
    float t = td;
    for (int i = 0; i < 20; i++) {
        float t2 = t * t;
        float g = t * (1.0 + t2 * (k.x + t2 * (k.y + t2 * (k.z + t2 * k.w)))) - td;
        float dg = 1.0 + t2 * (3.0 * k.x + t2 * (5.0 * k.y + t2 * (7.0 * k.z + t2 * 9.0 * k.w)));
        t -= g / dg;
    }
    float s = td > 0.0 ? sin(t) / td : 1.0;
    return vec3(m * s, cos(t));
}

vec2 face_st(int i, vec3 d) {
    vec3 p = faces[i] * d;
    return (vec2(p.x, -p.y) / p.z * pad_scale + 1.0) * 0.5;
}

void main() {
    // gl_FragCoord is a pixel centre counted from the bottom; the image counts rows from the top.
    vec2 uv = vec2(gl_FragCoord.x, height - gl_FragCoord.y);
    vec3 d = ray(uv);
    int best = 0;
    float most = -2.0;
    for (int i = 0; i < 5; i++) {
        float z = (faces[i] * d).z;
        if (z > most) { most = z; best = i; }
    }
    if (most <= 0.0) { value = sky; return; }
    vec2 st = face_st(best, d);
    vec2 dx = face_st(best, ray(uv + vec2(1.0, 0.0))) - st;
    vec2 dy = face_st(best, ray(uv + vec2(0.0, 1.0))) - st;
    if (best == 0) value = textureGrad(face0, st, dx, dy).r;
    else if (best == 1) value = textureGrad(face1, st, dx, dy).r;
    else if (best == 2) value = textureGrad(face2, st, dx, dy).r;
    else if (best == 3) value = textureGrad(face3, st, dx, dy).r;
    else value = textureGrad(face4, st, dx, dy).r;
}
"""


def _mat3(R: np.ndarray) -> bytes:
    """A row-major 3x3 as GLSL's column-major mat3."""
    return np.ascontiguousarray(np.asarray(R, dtype="f4").T).tobytes()


def _padded(a) -> np.ndarray:
    """Per-octave values filled out with zeros to the shader's fixed array length."""
    a = np.asarray(a, dtype="f4")
    return np.concatenate([a, np.zeros((MAX_OCTAVES - len(a),) + a.shape[1:], "f4")])


class Rasterizer:
    """One GL context and the scene's triangles on the GPU; `render` draws one image."""

    def __init__(self, scene: Scene):
        self.ctx = moderngl.create_context(standalone=True, backend="egl")
        self.sky = scene.sky
        tris, solid = scene.triangles()
        verts = np.zeros(len(solid), dtype=[("pos", "f4", 3), ("solid", "i4"), ("uv", "f4", 2), ("star", "f4")])
        verts["pos"], verts["solid"], verts["uv"], verts["star"] = tris.position, solid, tris.face_uv, tris.star_r
        self.face_prog = self.ctx.program(vertex_shader=FACE_VS, fragment_shader=FACE_FS)
        tex = scene.texture
        assert tex.octaves <= MAX_OCTAVES, f"{tex.octaves} octaves; the shader holds {MAX_OCTAVES}"
        self.face_prog["n_oct"].value = tex.octaves
        self.face_prog["rot"].write(b"".join(_mat3(R) for R in _padded(tex.rotations)))
        self.face_prog["off"].write(_padded(tex.offsets).tobytes())
        self.face_prog["lam"].write(_padded(tex.wavelengths).tobytes())
        self.face_prog["amp"].write(_padded(tex.amplitudes).tobytes())
        self.face_prog["contrast"].value = tex.contrast
        self.face_vao = self.ctx.vertex_array(
            self.face_prog, [(self.ctx.buffer(verts.tobytes()), "3f 1i 2f 1f", "in_pos", "in_solid", "in_uv", "in_star")]
        )
        self.lens_prog = self.ctx.program(vertex_shader=LENS_VS, fragment_shader=LENS_FS)
        self.lens_prog["faces"].write(b"".join(_mat3(F) for F in FACES))
        self.lens_prog["sky"].value = scene.sky
        for i in range(5):
            self.lens_prog[f"face{i}"].value = i
        quad = self.ctx.buffer(np.array([-1, -1, 3, -1, -1, 3], dtype="f4").tobytes())
        self.lens_vao = self.ctx.vertex_array(self.lens_prog, [(quad, "2f", "in_xy")])
        self._faces: dict[int, list] = {}
        self._out: dict[tuple[int, int], moderngl.Framebuffer] = {}

    def _face_targets(self, n: int) -> list:
        if n not in self._faces:
            targets = []
            for _ in FACES:
                tex = self.ctx.texture((n, n), 1, dtype="f4")
                tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
                tex.repeat_x = tex.repeat_y = False
                tex.anisotropy = 16.0
                fbo = self.ctx.framebuffer([tex], self.ctx.depth_renderbuffer((n, n)))
                targets.append((tex, fbo))
            self._faces[n] = targets
        return self._faces[n]

    def render(self, lens: KannalaBrandt, R_wc: np.ndarray, centre: np.ndarray) -> np.ndarray:
        """(height, width) float32 intensity in [0, 1] for a camera with rotation R_wc (camera ->
        world) and optical centre `centre`."""
        n = int(np.ceil(2 * max(lens.fx, lens.fy) * (1 + FACE_PAD)))
        pad_scale = 1.0 / (1 + FACE_PAD)
        targets = self._face_targets(n)
        self.face_prog["centre"].value = tuple(float(x) for x in centre)
        self.face_prog["pad_scale"].value = pad_scale
        self.ctx.enable(moderngl.DEPTH_TEST)
        for F, (tex, fbo) in zip(FACES, targets):
            self.face_prog["R_fw"].write(_mat3(F @ R_wc.T))
            fbo.use()
            fbo.clear(self.sky, depth=1.0)
            self.face_vao.render(moderngl.TRIANGLES)
            tex.build_mipmaps()
        self.ctx.disable(moderngl.DEPTH_TEST)

        size = (lens.width, lens.height)
        if size not in self._out:
            self._out[size] = self.ctx.simple_framebuffer(size, components=1, dtype="f4")
        out = self._out[size]
        out.use()
        for i, (tex, _) in enumerate(targets):
            tex.use(i)
        p = self.lens_prog
        p["f"].value = (lens.fx, lens.fy)
        p["c"].value = (lens.cx, lens.cy)
        p["k"].value = tuple(float(x) for x in lens.k)
        p["height"].value = float(lens.height)
        p["pad_scale"].value = pad_scale
        self.lens_vao.render(moderngl.TRIANGLES)
        img = np.frombuffer(out.read(components=1, dtype="f4"), dtype="f4").reshape(lens.height, lens.width)
        return np.clip(img[::-1], 0.0, 1.0)
