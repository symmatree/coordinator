"""A scene of simple solids with known geometry and non-repeating texture.

World frame is local ENU in metres: x east, y north, z up, ground at z = 0. Solids are a ground
plane, boxes (yawed about z) and vertical cylinders. Each is intersected in closed form, so the
geometry an image shows is exactly the geometry recorded as truth.

Texture is a sum of octaves of 3-D value noise evaluated at the hit point, each octave on its own
randomly rotated lattice and every solid on its own seed. Nothing repeats, so every patch of
surface is a unique feature and a match between two images can only be the right one. Octaves
finer than the pixel's footprint on the surface are faded out rather than sampled, which keeps
distant and grazing surfaces from aliasing into noise that no real camera would record.

A box can carry a Siemens star on each face but the bottom: black and white sectors, sharp-edged
to the pixel and faded to grey where the sectors are narrower than the footprint, so the radius at
which a reconstruction loses them reads off its resolution. A `box_ring` in the config expands to
equal boxes spaced around a circle, each face-on to the centre.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _rot(rng: np.random.Generator) -> np.ndarray:
    q, r = np.linalg.qr(rng.normal(size=(3, 3)))
    return q * np.sign(np.diag(r))


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


@dataclass
class Triangles:
    """A triangle list for the rasteriser: three rows per triangle of world position, the face
    coordinates a Siemens star is drawn in, and that star's radius (0 for none)."""

    position: np.ndarray
    face_uv: np.ndarray
    star_r: np.ndarray

    @classmethod
    def plain(cls, position: np.ndarray) -> "Triangles":
        return cls(position, np.zeros((len(position), 2)), np.zeros(len(position)))

    @classmethod
    def join(cls, parts: list["Triangles"]) -> "Triangles":
        return cls(*(np.concatenate([getattr(t, k) for t in parts]) for k in ("position", "face_uv", "star_r")))


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

    # Far enough that the gap between its edge and the true horizon is a few pixels at 30 m.
    EXTENT_M = 20000.0
    TILE_M = 200.0

    def triangles(self) -> Triangles:
        """A grid of tiles out to EXTENT_M, small enough that interpolating world position across
        each one stays exact in float32."""
        g = np.arange(-self.EXTENT_M, self.EXTENT_M, self.TILE_M)
        x0, y0 = (a.ravel() for a in np.meshgrid(g, g))
        x1, y1 = x0 + self.TILE_M, y0 + self.TILE_M
        quad = [(x0, y0), (x1, y0), (x1, y1), (x0, y0), (x1, y1), (x0, y1)]
        p = np.stack([np.stack([x, y, np.full_like(x, self.height)], -1) for x, y in quad], 1)
        return Triangles.plain(p.reshape(-1, 3))


@dataclass
class Box:
    """Box of `size` (x, y, z extents) with its bottom face centred at `base`, yawed `yaw_deg` about z."""

    base: tuple[float, float, float]
    size: tuple[float, float, float]
    yaw_deg: float = 0.0
    decals: bool = False
    kind = "box"

    STAR_SECTOR_PAIRS = 36
    STAR_FILL = 0.45  # star radius as a fraction of the face's shorter side

    def __post_init__(self) -> None:
        c, s = np.cos(np.radians(self.yaw_deg)), np.sin(np.radians(self.yaw_deg))
        self._R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])  # box -> world
        self._half = np.array(self.size, dtype=float) / 2
        self._centre = np.array(self.base, dtype=float) + [0, 0, self._half[2]]
        self._radius = float(np.linalg.norm(self._half))

    def bound(self) -> tuple[np.ndarray, float]:
        return self._centre, self._radius

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

    def decal(self, p: np.ndarray, footprint: np.ndarray) -> np.ndarray:
        """Siemens star value at surface points `p`, NaN off the star (bottom face included)."""
        out = np.full(len(p), np.nan)
        if not self.decals:
            return out
        q = (p - self._centre) @ self._R
        axis = np.abs(q / self._half).argmax(axis=1)
        for a in range(3):
            m = (axis == a) & ~((a == 2) & (q[:, 2] < 0))
            u_ax, v_ax = [k for k in range(3) if k != a]
            u, v = q[m, u_ax], q[m, v_ax]
            star_r = self.STAR_FILL * 2 * min(self._half[u_ax], self._half[v_ax])
            r = np.hypot(u, v)
            s = np.sin(self.STAR_SECTOR_PAIRS * np.arctan2(v, u))
            fp = footprint[m]
            # Signed distance to the nearest sector edge is about r s / N, so the edge is
            # antialiased over one footprint; sectors narrower than two footprints fade to grey.
            edge = np.clip(r * s / (self.STAR_SECTOR_PAIRS * fp), -1, 1)
            period = 2 * np.pi * r / self.STAR_SECTOR_PAIRS
            fade = np.clip(period / fp / 2 - 1, 0, 1)
            val = np.where(r <= star_r, 0.5 + 0.5 * fade * edge, np.nan)
            out[np.flatnonzero(m)] = val
        return out

    def triangles(self) -> Triangles:
        """Six faces; on each, the face coordinates `decal` uses and the star radius (0 where
        there is no star)."""
        out = []
        for a in range(3):
            u_ax, v_ax = [k for k in range(3) if k != a]
            for sign in (-1.0, 1.0):
                corners = []
                for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, -1), (1, 1), (-1, 1)):
                    q = np.zeros(3)
                    q[a], q[u_ax], q[v_ax] = sign * self._half[a], su * self._half[u_ax], sv * self._half[v_ax]
                    corners.append(q)
                q = np.array(corners)
                star = self.decals and not (a == 2 and sign < 0)
                star_r = self.STAR_FILL * 2 * min(self._half[u_ax], self._half[v_ax]) if star else 0.0
                out.append(Triangles(q @ self._R.T + self._centre, q[:, [u_ax, v_ax]], np.full(6, star_r)))
        return Triangles.join(out)

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

    SEGMENTS = 256  # the polygon departs from the circle by radius * 7.5e-5

    def triangles(self) -> Triangles:
        a = np.linspace(0, 2 * np.pi, self.SEGMENTS + 1)
        ring = np.stack([self.x + self.radius * np.cos(a), self.y + self.radius * np.sin(a)], -1)
        r0, r1 = ring[:-1], ring[1:]
        lo, hi = np.full((self.SEGMENTS, 1), self.z0), np.full((self.SEGMENTS, 1), self.z1)
        side = [np.hstack([r0, lo]), np.hstack([r1, lo]), np.hstack([r1, hi]), np.hstack([r0, lo]), np.hstack([r1, hi]), np.hstack([r0, hi])]
        tris = [np.stack(side, 1).reshape(-1, 3)]
        for z in (lo, hi):
            centre = np.hstack([np.full((self.SEGMENTS, 2), [self.x, self.y]), z])
            tris.append(np.stack([centre, np.hstack([r0, z]), np.hstack([r1, z])], 1).reshape(-1, 3))
        return Triangles.plain(np.concatenate(tris))

    def distance(self, p: np.ndarray) -> np.ndarray:
        dr = np.hypot(p[:, 0] - self.x, p[:, 1] - self.y) - self.radius
        dz = np.maximum(self.z0 - p[:, 2], p[:, 2] - self.z1)
        outside = np.hypot(np.maximum(dr, 0), np.maximum(dz, 0))
        inside = np.minimum(np.maximum(dr, dz), 0)
        return np.abs(outside + inside)


_KINDS = {"plane": Plane, "box": Box, "cylinder": Cylinder}


def box_ring(centre: tuple[float, float], radius: float, spacing_m: float, size: tuple[float, float, float], offset_steps: float = 0.0) -> list[Box]:
    """Boxes of `size` evenly round a circle about `centre`, about `spacing_m` apart, each yawed
    to face the centre; the first sits `offset_steps` of a spacing round from east."""
    count = max(1, int(round(2 * np.pi * radius / spacing_m)))
    angles = (offset_steps + np.arange(count)) * 360.0 / count
    return [
        Box(base=(centre[0] + radius * np.cos(np.radians(a)), centre[1] + radius * np.sin(np.radians(a)), 0.0), size=tuple(size), yaw_deg=float(a))
        for a in angles
    ]


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
            kind = s.pop("kind")
            if kind == "box_ring":
                solids.extend(box_ring(**s))
            else:
                solids.append(_KINDS[kind](**s))
        return cls(solids, Texture(**cfg.get("texture", {})), float(cfg.get("sky", 0.85)))

    def intersect(self, o: np.ndarray, d: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Nearest hit: (t, solid index or -1, unit normal)."""
        t_best = np.full(len(o), np.inf)
        idx = np.full(len(o), -1)
        n_best = np.zeros_like(o)
        # A render's rays share one origin, so a bounded solid costs one product to find the rays
        # that pass within its bounding sphere, and only those are intersected.
        o0 = o[0]
        one_origin = bool(np.all(o == o0))
        dd = np.einsum("ij,ij->i", d, d)
        for i, s in enumerate(self.solids):
            if hasattr(s, "bound"):
                c, r = s.bound()
                if one_origin:
                    w = c - o0
                    b = d @ w
                    near = np.flatnonzero(w @ w - b * b / dd <= r * r)
                else:
                    w = c - o
                    b = np.einsum("ij,ij->i", d, w)
                    near = np.flatnonzero(np.einsum("ij,ij->i", w, w) - b * b / dd <= r * r)
            else:
                near = np.arange(len(o))
            if len(near) == 0:
                continue
            t, n = s.intersect(o[near], d[near])
            closer = t < t_best[near]
            sel = near[closer]
            t_best[sel] = t[closer]
            idx[sel] = i
            n_best[sel] = n[closer]
        return t_best, idx, n_best

    def triangles(self) -> tuple[Triangles, np.ndarray]:
        """Every solid's triangles, and the index into `solids` of each vertex's solid."""
        parts = [s.triangles() for s in self.solids]
        solid = np.concatenate([np.full(len(t.position), i) for i, t in enumerate(parts)])
        return Triangles.join(parts), solid

    def distance(self, p: np.ndarray) -> np.ndarray:
        """Unsigned distance from each point to the nearest solid surface."""
        # A running minimum: a reconstruction's points times hundreds of solids does not fit at once.
        out = np.full(len(p), np.inf)
        for s in self.solids:
            np.minimum(out, s.distance(p), out=out)
        return out
