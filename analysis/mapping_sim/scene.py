"""A scene of simple solids with known geometry and non-repeating texture.

World frame is local ENU in metres: x east, y north, z up, ground at z = 0. Solids are a ground
plane, boxes (yawed about z) and vertical cylinders. Each is intersected in closed form, so the
geometry an image shows is exactly the geometry recorded as truth.

Texture is a sum of octaves of 3-D value noise evaluated at the hit point, each octave on its own
randomly rotated lattice and every solid on its own seed. Nothing repeats, so every patch of
surface is a unique feature and a match between two images can only be the right one. Octaves
finer than the pixel's footprint on the surface are faded out rather than sampled, which keeps
distant and grazing surfaces from aliasing into noise that no real camera would record.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_U32 = np.uint32


def _rot(rng: np.random.Generator) -> np.ndarray:
    q, r = np.linalg.qr(rng.normal(size=(3, 3)))
    return q * np.sign(np.diag(r))


def _hash(ix: np.ndarray, iy: np.ndarray, iz: np.ndarray, seed: int) -> np.ndarray:
    """Integer lattice point -> float in [-1, 1). A multiply-xorshift mix on uint32, which wraps."""
    h = (ix.astype(np.int64) & 0xFFFFFFFF).astype(_U32) * _U32(0x8DA6B343)
    h ^= (iy.astype(np.int64) & 0xFFFFFFFF).astype(_U32) * _U32(0xD8163841)
    h ^= (iz.astype(np.int64) & 0xFFFFFFFF).astype(_U32) * _U32(0xCB1AB31F)
    h ^= _U32((seed * 0x9E3779B9) & 0xFFFFFFFF)
    h ^= h >> _U32(15)
    h *= _U32(0x2C1B3C6D)
    h ^= h >> _U32(12)
    h *= _U32(0x297A2D39)
    h ^= h >> _U32(15)
    return h.astype(np.float64) / 2147483648.0 - 1.0


def value_noise(p: np.ndarray, seed: int) -> np.ndarray:
    """(N, 3) points on a unit lattice -> (N,) smoothly interpolated noise in [-1, 1]."""
    i = np.floor(p)
    f = p - i
    i = i.astype(np.int64)
    s = f * f * (3 - 2 * f)
    out = np.zeros(len(p))
    for dx in (0, 1):
        wx = s[:, 0] if dx else 1 - s[:, 0]
        for dy in (0, 1):
            wy = s[:, 1] if dy else 1 - s[:, 1]
            for dz in (0, 1):
                wz = s[:, 2] if dz else 1 - s[:, 2]
                out += wx * wy * wz * _hash(i[:, 0] + dx, i[:, 1] + dy, i[:, 2] + dz, seed)
    return out


@dataclass
class Texture:
    """Octave wavelengths from `finest_m` doubling `octaves` times; amplitude ~ wavelength^slope."""

    finest_m: float = 0.03
    octaves: int = 8
    slope: float = 0.3
    contrast: float = 1.6
    seed: int = 1

    def __post_init__(self) -> None:
        rng = np.random.default_rng(self.seed)
        self.wavelengths = self.finest_m * 2.0 ** np.arange(self.octaves)
        self.amplitudes = self.wavelengths**self.slope
        self.amplitudes /= self.amplitudes.sum()
        self.rotations = [_rot(rng) for _ in range(self.octaves)]
        self.offsets = rng.uniform(0, 1000, size=(self.octaves, 3))

    def sample(self, p: np.ndarray, footprint: np.ndarray, solid_seed: int) -> np.ndarray:
        """(N, 3) world points and (N,) footprint in metres -> (N,) intensity in [0, 1]."""
        v = np.zeros(len(p))
        for o, (lam, amp) in enumerate(zip(self.wavelengths, self.amplitudes)):
            # Full weight at >= 4 pixels per wavelength, none at <= 2 (the sampling limit).
            w = np.clip(lam / np.maximum(footprint, 1e-9) / 2.0 - 1.0, 0.0, 1.0)
            live = w > 0
            if not live.any():
                continue
            q = (p[live] @ self.rotations[o].T) / lam + self.offsets[o]
            v[live] += amp * w[live] * value_noise(q, solid_seed * 1000003 + o)
        # tanh rather than a clip: it steepens mid-grey transitions into edges without leaving
        # flat saturated patches, which would be featureless.
        return 0.5 + 0.5 * np.tanh(self.contrast * v)


@dataclass
class Plane:
    """The ground, z = height, unbounded."""

    height: float = 0.0
    kind = "plane"

    def intersect(self, o: np.ndarray, d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        dz = d[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (self.height - o[:, 2]) / dz
        t = np.where((dz < 0) & (t > 0), t, np.inf)
        n = np.broadcast_to(np.array([0.0, 0.0, 1.0]), o.shape)
        return t, n

    def distance(self, p: np.ndarray) -> np.ndarray:
        return np.abs(p[:, 2] - self.height)


@dataclass
class Box:
    """Box of `size` (x, y, z extents) with its bottom face centred at `base`, yawed `yaw_deg` about z."""

    base: tuple[float, float, float]
    size: tuple[float, float, float]
    yaw_deg: float = 0.0
    kind = "box"

    def __post_init__(self) -> None:
        c, s = np.cos(np.radians(self.yaw_deg)), np.sin(np.radians(self.yaw_deg))
        self._R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])  # box -> world
        self._half = np.array(self.size, dtype=float) / 2
        self._centre = np.array(self.base, dtype=float) + [0, 0, self._half[2]]

    def intersect(self, o: np.ndarray, d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        ob = (o - self._centre) @ self._R
        db = d @ self._R
        with np.errstate(divide="ignore", invalid="ignore"):
            inv = 1.0 / db
            t1 = (-self._half - ob) * inv
            t2 = (self._half - ob) * inv
        tmin = np.minimum(t1, t2)
        tmax = np.maximum(t1, t2)
        tmin = np.where(np.isnan(tmin), -np.inf, tmin)
        tmax = np.where(np.isnan(tmax), np.inf, tmax)
        t_near = tmin.max(axis=1)
        t_far = tmax.min(axis=1)
        hit = (t_near <= t_far) & (t_near > 0)
        t = np.where(hit, t_near, np.inf)
        axis = tmin.argmax(axis=1)
        nb = np.zeros_like(ob)
        nb[np.arange(len(ob)), axis] = -np.sign(db[np.arange(len(ob)), axis])
        return t, nb @ self._R.T

    def distance(self, p: np.ndarray) -> np.ndarray:
        q = np.abs((p - self._centre) @ self._R) - self._half
        outside = np.linalg.norm(np.maximum(q, 0), axis=1)
        inside = np.minimum(q.max(axis=1), 0)
        return np.abs(outside + inside)


@dataclass
class Cylinder:
    """Vertical cylinder of `radius`, axis through (x, y), from z = z0 to z = z1, capped."""

    x: float
    y: float
    radius: float
    z0: float = 0.0
    z1: float = 1.0
    kind = "cylinder"

    def intersect(self, o: np.ndarray, d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        ox, oy = o[:, 0] - self.x, o[:, 1] - self.y
        dx, dy = d[:, 0], d[:, 1]
        a = dx * dx + dy * dy
        b = 2 * (ox * dx + oy * dy)
        c = ox * ox + oy * oy - self.radius**2
        disc = b * b - 4 * a * c
        with np.errstate(divide="ignore", invalid="ignore"):
            sq = np.sqrt(np.maximum(disc, 0))
            ts = (-b - sq) / (2 * a)
        z = o[:, 2] + ts * d[:, 2]
        side_ok = (disc >= 0) & (a > 0) & (ts > 0) & (z >= self.z0) & (z <= self.z1)
        t_side = np.where(side_ok, ts, np.inf)
        n_side = np.stack([ox + ts * dx, oy + ts * dy, np.zeros_like(ts)], axis=1) / self.radius

        t_cap = np.full(len(o), np.inf)
        n_cap = np.zeros_like(o)
        for zc, nz in ((self.z1, 1.0), (self.z0, -1.0)):
            with np.errstate(divide="ignore", invalid="ignore"):
                tc = (zc - o[:, 2]) / d[:, 2]
            px, py = ox + tc * dx, oy + tc * dy
            ok = (tc > 0) & (px * px + py * py <= self.radius**2) & (np.sign(d[:, 2]) == -nz)
            better = ok & (tc < t_cap)
            t_cap = np.where(better, tc, t_cap)
            n_cap[better] = [0, 0, nz]

        use_side = t_side <= t_cap
        t = np.where(use_side, t_side, t_cap)
        n = np.where(use_side[:, None], n_side, n_cap)
        return t, n

    def distance(self, p: np.ndarray) -> np.ndarray:
        dr = np.hypot(p[:, 0] - self.x, p[:, 1] - self.y) - self.radius
        dz = np.maximum(self.z0 - p[:, 2], p[:, 2] - self.z1)
        outside = np.hypot(np.maximum(dr, 0), np.maximum(dz, 0))
        inside = np.minimum(np.maximum(dr, dz), 0)
        return np.abs(outside + inside)


_KINDS = {"plane": Plane, "box": Box, "cylinder": Cylinder}


class Scene:
    def __init__(self, solids: list, texture: Texture, sky: float = 0.85):
        self.solids = solids
        self.texture = texture
        self.sky = sky

    @classmethod
    def from_config(cls, cfg: dict) -> "Scene":
        solids = []
        for s in cfg["solids"]:
            s = dict(s)
            solids.append(_KINDS[s.pop("kind")](**s))
        return cls(solids, Texture(**cfg.get("texture", {})), float(cfg.get("sky", 0.85)))

    def intersect(self, o: np.ndarray, d: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Nearest hit: (t, solid index or -1, unit normal)."""
        t_best = np.full(len(o), np.inf)
        idx = np.full(len(o), -1)
        n_best = np.zeros_like(o)
        for i, s in enumerate(self.solids):
            t, n = s.intersect(o, d)
            closer = t < t_best
            t_best = np.where(closer, t, t_best)
            idx = np.where(closer, i, idx)
            n_best[closer] = n[closer]
        return t_best, idx, n_best

    def shade(self, o: np.ndarray, d: np.ndarray, pixel_angle: np.ndarray) -> np.ndarray:
        """Unlit albedo along each ray: no shadows, no shading, so nothing changes with view."""
        t, idx, n = self.intersect(o, d)
        out = np.full(len(o), self.sky)
        hit = idx >= 0
        if hit.any():
            p = o[hit] + t[hit, None] * d[hit]
            cos_inc = np.abs(np.einsum("ij,ij->i", n[hit], d[hit]))
            footprint = t[hit] * pixel_angle[hit] / np.maximum(cos_inc, 0.05)
            vals = np.empty(hit.sum())
            for i in np.unique(idx[hit]):
                m = idx[hit] == i
                vals[m] = self.texture.sample(p[m], footprint[m], solid_seed=int(i) + 1)
            out[hit] = vals
        return out

    def distance(self, p: np.ndarray) -> np.ndarray:
        """Unsigned distance from each point to the nearest solid surface."""
        return np.min([s.distance(p) for s in self.solids], axis=0)
