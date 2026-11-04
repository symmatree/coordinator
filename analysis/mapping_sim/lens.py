"""Kannala-Brandt fisheye lens: pixel <-> camera-frame ray.

The model is OpenCV's fisheye model, which is also OpenSfM's `fisheye_opencv` projection, so the
intrinsics a reconstruction recovers are directly comparable with the ones an image was rendered
with:

    theta   = angle between the ray and the optical axis
    theta_d = theta * (1 + k1 theta^2 + k2 theta^4 + k3 theta^6 + k4 theta^8)
    u = fx * theta_d * x / r + cx,   v = fy * theta_d * y / r + cy

with (x, y, z) the ray in the OpenCV camera frame (x right, y down, z along the optical axis) and
r = hypot(x, y). All k = 0 is the equidistant fisheye.

Pixel coordinates are continuous with (0, 0) at the top-left corner of the top-left pixel, so the
centre of pixel (i, j) is (j + 0.5, i + 0.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np


@dataclass(frozen=True)
class KannalaBrandt:
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    k: tuple[float, float, float, float] = field(default=(0.0, 0.0, 0.0, 0.0))

    def theta_d(self, theta: np.ndarray) -> np.ndarray:
        t2 = theta * theta
        k1, k2, k3, k4 = self.k
        return theta * (1 + t2 * (k1 + t2 * (k2 + t2 * (k3 + t2 * k4))))

    def _dtheta_d(self, theta: np.ndarray) -> np.ndarray:
        t2 = theta * theta
        k1, k2, k3, k4 = self.k
        return 1 + t2 * (3 * k1 + t2 * (5 * k2 + t2 * (7 * k3 + t2 * 9 * k4)))

    def theta(self, theta_d: np.ndarray, iters: int = 20) -> np.ndarray:
        """Invert theta_d(theta) by Newton's method, starting from the equidistant answer."""
        t = np.array(theta_d, dtype=np.float64, copy=True)
        for _ in range(iters):
            t = t - (self.theta_d(t) - theta_d) / self._dtheta_d(t)
        return t

    def project(self, rays: np.ndarray) -> np.ndarray:
        """(..., 3) camera-frame rays -> (..., 2) pixel coordinates."""
        x, y, z = rays[..., 0], rays[..., 1], rays[..., 2]
        r = np.hypot(x, y)
        theta = np.arctan2(r, z)
        scale = np.where(r > 0, self.theta_d(theta) / np.where(r > 0, r, 1.0), 1.0 / np.where(z > 0, z, 1.0))
        return np.stack([self.fx * x * scale + self.cx, self.fy * y * scale + self.cy], axis=-1)

    def unproject(self, uv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(..., 2) pixel coordinates -> ((..., 3) unit rays, (...) theta)."""
        mx = (uv[..., 0] - self.cx) / self.fx
        my = (uv[..., 1] - self.cy) / self.fy
        td = np.hypot(mx, my)
        theta = self.theta(td)
        s = np.where(td > 0, np.sin(theta) / np.where(td > 0, td, 1.0), 1.0)
        rays = np.stack([mx * s, my * s, np.cos(theta)], axis=-1)
        return rays, theta

    def pixel_angle(self, theta: np.ndarray) -> np.ndarray:
        """Angular size of one pixel at off-axis angle theta: the larger of the radial and
        tangential extents, so a texture band-limited to it does not alias in either direction."""
        f = min(self.fx, self.fy)
        radial = 1.0 / (f * self._dtheta_d(theta))
        td = self.theta_d(theta)
        tangential = np.where(td > 0, np.sin(theta) / (f * np.where(td > 0, td, 1.0)), 1.0 / f)
        return np.maximum(radial, tangential)

    def scaled(self, factor: float) -> "KannalaBrandt":
        """The same lens on a sensor resampled by `factor` (0.25 = quarter resolution)."""
        return replace(
            self,
            width=int(round(self.width * factor)),
            height=int(round(self.height * factor)),
            fx=self.fx * factor,
            fy=self.fy * factor,
            cx=self.cx * factor,
            cy=self.cy * factor,
        )

    def fov_deg(self) -> dict[str, float]:
        """Full field of view across the horizontal, vertical and diagonal through the centre."""

        def across(du: float, dv: float) -> float:
            a = self.unproject(np.array([self.cx - du, self.cy - dv]))[1]
            b = self.unproject(np.array([self.cx + du, self.cy + dv]))[1]
            return float(np.degrees(a + b))

        return {
            "h": across(self.width / 2, 0),
            "v": across(0, self.height / 2),
            "d": across(self.width / 2, self.height / 2),
        }

    def opensfm(self) -> dict:
        """These intrinsics in OpenSfM's `fisheye_opencv` convention: focal and principal point
        normalised by the larger image dimension, principal point relative to the image centre."""
        size = max(self.width, self.height)
        return {
            "projection_type": "fisheye_opencv",
            "width": self.width,
            "height": self.height,
            "focal": self.fx / size,
            "aspect_ratio": self.fy / self.fx,
            "c_x": (self.cx - self.width / 2) / size,
            "c_y": (self.cy - self.height / 2) / size,
            "k1": self.k[0],
            "k2": self.k[1],
            "k3": self.k[2],
            "k4": self.k[3],
        }

    @classmethod
    def from_config(cls, cfg: dict) -> "KannalaBrandt":
        w, h = int(cfg["width"]), int(cfg["height"])
        f = float(cfg["focal_px"])
        k = tuple(float(x) for x in cfg.get("k", (0, 0, 0, 0)))
        return cls(
            width=w,
            height=h,
            fx=f,
            fy=f * float(cfg.get("aspect_ratio", 1.0)),
            cx=float(cfg.get("cx", w / 2)),
            cy=float(cfg.get("cy", h / 2)),
            k=k + (0.0,) * (4 - len(k)),
        )
