"""Генератор иллюстраций БПЛА для карточек парка (app/static/drones/*.svg).

Дроны собираются из 3D-примитивов (короба, цилиндры, сферы, диски винтов, пластины крыла),
проецируются в аксонометрию 3/4 сверху-спереди и рисуются по глубине с простым освещением.
Фон — светлая «студийная» сцена с неконтрастным намёком на отрасль.

    python scripts/make_drone_images.py
"""
from __future__ import annotations

import math
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "drones"
W, H = 800, 500
YAW, PITCH = math.radians(-32), math.radians(28)
LIGHT = (-0.35, -0.45, 0.82)  # свет слева-спереди-сверху


# ------------------------------------------------------------------ геометрия
def rot(p):
    x, y, z = p
    c, s = math.cos(YAW), math.sin(YAW)
    return (x * c - y * s, x * s + y * c, z)


class Scene:
    def __init__(self, scale=3.4, cx=W / 2, cy=372):
        self.s, self.cx, self.cy = scale, cx, cy
        self.items: list[tuple[float, str]] = []
        self.defs: list[str] = []
        self._gid = 0

    def P(self, p):
        x, y, z = rot(p)
        return (self.cx + self.s * x, self.cy - self.s * (z * math.cos(PITCH) + y * math.sin(PITCH)))

    def depth(self, p):
        x, y, z = rot(p)
        return -y * math.cos(PITCH) + z * math.sin(PITCH)

    def add(self, p, svg, bias=0.0):
        self.items.append((self.depth(p) + bias, svg))

    def gid(self, prefix):
        self._gid += 1
        return f"{prefix}{self._gid}"

    # ---- примитивы
    def box(self, c, size, color, bias=0.0, rx=0):
        cx, cy, cz = c
        sx, sy, sz = (v / 2 for v in size)
        corners = {(i, j, k): (cx + i * sx, cy + j * sy, cz + k * sz) for i in (-1, 1) for j in (-1, 1) for k in (-1, 1)}
        faces = [((0, 0, 1), [(-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]),
                 ((0, -1, 0), [(-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1)]),
                 ((0, 1, 0), [(-1, 1, -1), (1, 1, -1), (1, 1, 1), (-1, 1, 1)]),
                 ((1, 0, 0), [(1, -1, -1), (1, 1, -1), (1, 1, 1), (1, -1, 1)]),
                 ((-1, 0, 0), [(-1, -1, -1), (-1, 1, -1), (-1, 1, 1), (-1, -1, 1)])]
        view = (0, -math.cos(PITCH), math.sin(PITCH))
        out = []
        for n, ids in faces:
            nr = rot(n)
            if nr[0] * view[0] + nr[1] * view[1] + nr[2] * view[2] <= 0.02:
                continue
            pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (self.P(corners[i]) for i in ids))
            out.append(f'<polygon points="{pts}" fill="{shade(color, nr)}" stroke="{shade(color, nr, -0.08)}" stroke-width="0.6" stroke-linejoin="round"/>')
        self.add(c, "".join(out), bias)

    def cyl(self, c, r, h, color, bias=0.0, top=None):
        x, y, z = c
        (bx, by), (tx, ty) = self.P((x, y, z)), self.P((x, y, z + h))
        rx, ry = r * self.s, r * self.s * math.sin(PITCH)
        g = self.gid("cy")
        self.defs.append(f'<linearGradient id="{g}" x1="0" x2="1" y1="0" y2="0"><stop offset="0" stop-color="{mix(color, "#ffffff", .25)}"/>'
                         f'<stop offset=".45" stop-color="{color}"/><stop offset="1" stop-color="{mix(color, "#000000", .35)}"/></linearGradient>')
        svg = (f'<path d="M{bx - rx:.1f},{by:.1f} A{rx:.1f},{ry:.1f} 0 0 0 {bx + rx:.1f},{by:.1f} L{tx + rx:.1f},{ty:.1f} '
               f'A{rx:.1f},{ry:.1f} 0 0 1 {tx - rx:.1f},{ty:.1f} Z" fill="url(#{g})"/>'
               f'<ellipse cx="{tx:.1f}" cy="{ty:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" fill="{top or mix(color, "#ffffff", .3)}"/>')
        self.add((x, y, z + h / 2), svg, bias)

    def sphere(self, c, r, color, bias=0.0, lens=None):
        x, y = self.P(c)
        g = self.gid("sp")
        self.defs.append(f'<radialGradient id="{g}" cx=".35" cy=".3" r=".75"><stop offset="0" stop-color="{mix(color, "#ffffff", .55)}"/>'
                         f'<stop offset=".55" stop-color="{color}"/><stop offset="1" stop-color="{mix(color, "#000000", .45)}"/></radialGradient>')
        svg = f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r * self.s:.1f}" fill="url(#{g})"/>'
        if lens:
            lr = r * self.s * 0.48
            lx, ly = x - r * self.s * 0.12, y + r * self.s * 0.18
            svg += (f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="{lr:.1f}" fill="#0f1520"/>'
                    f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="{lr * .62:.1f}" fill="{lens}"/>'
                    f'<circle cx="{lx - lr * .3:.1f}" cy="{ly - lr * .3:.1f}" r="{lr * .18:.1f}" fill="#fff" opacity=".7"/>')
        self.add(c, svg, bias)

    def arm(self, a, b, w, color, bias=0.0):
        (x1, y1), (x2, y2) = self.P(a), self.P(b)
        mid = tuple((u + v) / 2 for u, v in zip(a, b))
        self.add(mid, f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{mix(color, "#000000", .25)}" stroke-width="{w * self.s + 1.6:.1f}" stroke-linecap="round"/>'
                      f'<line x1="{x1:.1f}" y1="{y1 - 0.6:.1f}" x2="{x2:.1f}" y2="{y2 - 0.6:.1f}" stroke="{color}" stroke-width="{w * self.s:.1f}" stroke-linecap="round"/>', bias)

    def rotor(self, c, r, bias=0.0, blades=2):
        x, y = self.P(c)
        rx, ry = r * self.s, r * self.s * math.sin(PITCH)
        g = self.gid("pr")
        self.defs.append(f'<radialGradient id="{g}"><stop offset="0" stop-color="#2a3343" stop-opacity=".05"/>'
                         f'<stop offset=".8" stop-color="#2a3343" stop-opacity=".16"/><stop offset="1" stop-color="#2a3343" stop-opacity=".30"/></radialGradient>')
        svg = f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" fill="url(#{g})"/>'
        svg += f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="{rx * .93:.1f}" ry="{ry * .16:.1f}" fill="#1b2230" opacity=".2" filter="url(#soft)"/>'
        svg += f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="{rx * .99:.1f}" ry="{ry * .99:.1f}" fill="none" stroke="#ffffff" stroke-opacity=".35" stroke-width="1"/>'
        svg += f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="{rx * .1:.1f}" ry="{ry * .1:.1f}" fill="#2a3343"/>'
        self.add(c, svg, bias + 0.5)

    def plate(self, pts3, color, bias=0.0, n=(0, 0, 1)):
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (self.P(p) for p in pts3))
        c = tuple(sum(v) / len(pts3) for v in zip(*pts3))
        nr = rot(n)
        self.add(c, f'<polygon points="{pts}" fill="{shade(color, nr)}" stroke="{shade(color, nr, -0.1)}" stroke-width="0.8" stroke-linejoin="round"/>', bias)

    def shadow(self, rx, ry, dx=0, dy=0, opacity=.13):
        x, y = self.P((dx, dy, 0))
        self.items.append((-1e9, f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="{rx * self.s:.1f}" ry="{ry * self.s * math.sin(PITCH):.1f}" fill="#1b2433" opacity="{opacity}" filter="url(#shadow)"/>'))

    def render(self, background: str) -> str:
        body = "".join(svg for _, svg in sorted(self.items, key=lambda i: i[0]))
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
                f'<defs><filter id="shadow" x="-30%" y="-80%" width="160%" height="260%"><feGaussianBlur stdDeviation="14"/></filter>'
                f'<filter id="soft"><feGaussianBlur stdDeviation="1.4"/></filter>'
                f'<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f6f8fb"/><stop offset="1" stop-color="#e2e8f1"/></linearGradient>'
                f'{"".join(self.defs)}</defs><rect width="{W}" height="{H}" fill="url(#bg)"/>{background}{body}</svg>')


def hexrgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, u):
    pa, pb = hexrgb(a), hexrgb(b)
    return "#" + "".join(f"{round(x + (y - x) * u):02x}" for x, y in zip(pa, pb))


def shade(color, n, extra=0.0):
    lam = n[0] * LIGHT[0] + n[1] * LIGHT[1] + n[2] * LIGHT[2]
    k = 0.18 + 0.55 * max(0.0, lam) + extra
    return mix(color, "#ffffff", k - 0.3) if k >= 0.3 else mix(color, "#000000", (0.3 - k) * 1.1)


# ------------------------------------------------------------------ фоновые сцены (неконтрастные)
def bg_field():
    rows = "".join(f'<path d="M{-200 + i * 70},{H} L{330 + i * 26},{300}" stroke="#cfd9c4" stroke-width="10" opacity=".55"/>' for i in range(16))
    return f'<path d="M0,300 L{W},300 L{W},{H} L0,{H} Z" fill="#e6ecdf" opacity=".9"/>{rows}<rect y="298" width="{W}" height="4" fill="#dfe6d6"/>'


def bg_powerline():
    def pylon(x, s):
        return (f'<g stroke="#c5cfdc" stroke-width="{2.2 * s}" fill="none" opacity=".9">'
                f'<path d="M{x - 22 * s},300 L{x},{300 - 190 * s} L{x + 22 * s},300 M{x - 34 * s},{300 - 150 * s} L{x + 34 * s},{300 - 150 * s} '
                f'M{x - 26 * s},{300 - 110 * s} L{x + 26 * s},{300 - 110 * s} M{x - 14 * s},300 L{x + 8 * s},{300 - 110 * s} M{x + 14 * s},300 L{x - 8 * s},{300 - 110 * s}"/></g>')
    wires = "".join(f'<path d="M{80 - 34 * 1.0},{150 + d} Q 400,{205 + d} {700 + 34 * .8},{178 + d}" stroke="#ccd5e1" stroke-width="1.4" fill="none"/>' for d in (0, 40))
    return f'<rect y="300" width="{W}" height="200" fill="#e8edf3"/>{pylon(80, 1.0)}{pylon(700, .8)}{wires}'


def bg_city():
    blocks, x = [], 0
    for w, h in [(60, 120), (44, 170), (70, 90), (52, 210), (80, 130), (46, 160), (64, 100), (58, 190), (72, 120), (50, 150), (66, 110), (60, 175), (58, 95)]:
        blocks.append(f'<rect x="{x}" y="{300 - h}" width="{w - 6}" height="{h}" rx="3" fill="#dde4ee"/>')
        x += w
    return f'<rect y="300" width="{W}" height="200" fill="#e7ecf2"/>{"".join(blocks)}'


def bg_mountains():
    return ('<path d="M0,300 L90,190 L160,240 L260,140 L360,250 L450,170 L560,260 L650,180 L800,280 L800,300 Z" fill="#dfe6ef"/>'
            '<path d="M0,300 L130,240 L230,280 L340,215 L470,290 L600,230 L720,285 L800,260 L800,300 Z" fill="#d6dee9"/>'
            f'<rect y="300" width="{W}" height="200" fill="#e7ecf2"/>')


def bg_contours():
    lines = "".join(f'<ellipse cx="{560 + i * 4}" cy="{120 + i * 3}" rx="{60 + i * 34}" ry="{30 + i * 17}" fill="none" stroke="#d3dbe6" stroke-width="1.3"/>' for i in range(9))
    lines += "".join(f'<ellipse cx="170" cy="400" rx="{40 + i * 30}" ry="{18 + i * 13}" fill="none" stroke="#d6dde8" stroke-width="1.2"/>' for i in range(7))
    grid = "".join(f'<path d="M{x},0 V{H}" stroke="#e1e7ef" stroke-width="1"/>' for x in range(0, W, 80))
    return grid + lines


# ------------------------------------------------------------------ дроны
def quad(sc: Scene, body, accent, arm_len=46, rotor_r=25, z=34, legs=True):
    for i, (sx, sy) in enumerate([(1, 1), (-1, 1), (1, -1), (-1, -1)]):
        a = math.radians(45)
        m = (sx * arm_len * math.cos(a), sy * arm_len * math.sin(a), z + 1)
        sc.arm((sx * 9, sy * 13, z), m, 4.2, "#2b3340")
        sc.cyl((m[0], m[1], z - 2), 4.2, 7, "#3a4352", top="#1d2430")
        sc.cyl((m[0], m[1], z + 5), 1.6, 3, accent if sy > 0 else "#d8dee8")
        sc.rotor((m[0], m[1], z + 8.5), rotor_r)
    sc.box((0, 0, z + 1), (24, 34, 10), body)
    sc.box((0, 3, z + 7.5), (17, 22, 3), mix(body, "#ffffff", .2))
    sc.box((0, -15.6, z + 1.5), (12, 2, 3), "#1d2430", bias=0.3)           # передняя камера обхода препятствий
    sc.box((-6, -17.4, z + 1.5), (2.6, 0.6, 1.6), accent, bias=0.6)
    if legs:
        for sx in (-1, 1):
            sc.arm((sx * 10, 4, z - 4), (sx * 17, 4, z - 22), 1.5, "#5a6476", bias=-30)
            sc.arm((sx * 17, -10, z - 22), (sx * 17, 16, z - 22), 1.9, "#5a6476", bias=-30)


def inspection():
    sc = Scene()
    sc.shadow(48, 48)
    quad(sc, "#eef1f5", "#2f6fed")
    sc.box((0, -8, 26.5), (8, 6, 5), "#2b3340", bias=0.2)                 # подвес
    sc.sphere((0, -9, 20), 7.5, "#3b4454", bias=1.2, lens="#3a6fb8")      # камера с зумом
    return sc.render(bg_powerline())


def delivery():
    sc = Scene()
    sc.shadow(50, 50)
    quad(sc, "#f2f4f7", "#2f6fed", arm_len=50, rotor_r=27, z=40)
    sc.box((0, 0, 24), (26, 26, 16), "#f0b429", bias=0.2)                 # грузовой контейнер
    sc.box((0, 0, 32.6), (27, 27, 1.2), "#c98e15", bias=0.25)
    sc.box((0, -13.2, 24), (9, 0.6, 5), "#ffffff", bias=0.6)              # этикетка
    sc.box((-1.5, -13.4, 24.6), (4.5, 0.4, 0.8), "#2b3340", bias=0.7)
    return sc.render(bg_city())


def rescue():
    sc = Scene()
    sc.shadow(48, 48)
    quad(sc, "#e2513b", "#ffffff")
    sc.box((0, -8, 26.5), (9, 7, 5), "#2b3340", bias=0.2)
    sc.box((-3.5, -11, 21), (6, 6, 6.5), "#3b4454", bias=1.0)             # тепловизор
    sc.box((-3.5, -14.1, 21), (3.6, 0.4, 3.6), "#7a2a6b", bias=1.3)
    sc.box((4.5, -11, 21), (6, 6, 6.5), "#3b4454", bias=1.0)              # прожектор
    sc.box((4.5, -14.1, 21), (4.2, 0.4, 4.2), "#fff6d6", bias=1.3)
    return sc.render(bg_mountains())


def mapping():
    sc = Scene(scale=3.3, cy=372)
    sc.shadow(70, 45)
    z = 36
    span, chord = 140, 24
    wing = [(-span / 2, -chord / 2 + 6, z), (span / 2, -chord / 2 + 6, z), (span / 2 - 6, chord / 2, z), (-span / 2 + 6, chord / 2, z)]
    sc.plate(wing, "#eef1f5", bias=0.4)
    sc.plate([(-span / 2, -5, z), (span / 2, -5, z), (span / 2, -5, z - 2.2), (-span / 2, -5, z - 2.2)], "#d5dbe5", bias=0.35, n=(0, -1, 0))
    for sx in (-1, 1):                                                   # балки с подъёмными винтами
        sc.box((sx * 30, 4, z - 2.5), (3.2, 70, 3.4), "#2b3340", bias=0.6)
        for y in (-28, 36):
            sc.cyl((sx * 30, y, z - 1), 3.4, 4.5, "#3a4352", top="#1d2430")
            sc.rotor((sx * 30, y, z + 4.5), 19, bias=0.6)
        sc.plate([(sx * 30, 38, z), (sx * 30, 46, z), (sx * 30, 46, z + 11), (sx * 30, 42, z + 11)], "#e4e8ef", bias=0.7, n=(1, 0, 0))
    sc.box((0, 2, z + 1.5), (12, 52, 9), "#f4f6f9", bias=0.8)             # фюзеляж
    sc.box((0, -25, z + 0.5), (9, 6, 6), "#e9edf3", bias=0.85)
    sc.box((0, -8, z - 4), (6, 7, 2), "#1d2430", bias=0.2)                # аэрофотокамера снизу
    sc.cyl((0, 29.5, z + 1.5), 2.2, 2, "#3a4352")
    sc.rotor((0, 31, z + 1.5), 11, bias=0.9)                              # маршевый винт
    sc.box((-30, -4.6, z + 0.2), (14, 0.6, 1.2), "#2f6fed", bias=0.9)
    return sc.render(bg_contours())


def agro():
    sc = Scene(scale=2.75, cy=372)
    sc.shadow(70, 70)
    z = 42
    for k in range(6):
        a = math.radians(30 + 60 * k)
        m = (62 * math.cos(a), 62 * math.sin(a), z + 2)
        sc.arm((16 * math.cos(a), 16 * math.sin(a), z), m, 4.6, "#2b3340")
        sc.cyl((m[0], m[1], z - 3), 5, 8, "#3a4352", top="#1d2430")
        sc.rotor((m[0], m[1], z + 6.5), 27)
        sc.box((m[0], m[1], z - 6.5), (1.8, 1.8, 3), "#2b3340", bias=-0.5)   # форсунки под винтами
    sc.cyl((0, 0, z - 24), 15, 22, "#f4f6f9", top="#e8ecf2")              # бак
    sc.cyl((0, 0, z - 2.5), 4.5, 2.5, "#2b8a3e")
    sc.box((0, 0, z + 2), (30, 30, 7), "#2f3a33")
    sc.box((0, 0, z + 6.2), (22, 22, 1.5), "#3d8f52")
    for sx in (-1, 1):
        sc.arm((sx * 14, 6, z - 2), (sx * 26, 6, z - 30), 2.2, "#5a6476", bias=-40)
        sc.arm((sx * 26, -16, z - 30), (sx * 26, 24, z - 30), 2.4, "#5a6476", bias=-40)
    return sc.render(bg_field())


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in [("inspection", inspection), ("delivery", delivery), ("rescue", rescue), ("mapping", mapping), ("agro", agro)]:
        (OUT / f"{name}.svg").write_text(fn(), encoding="utf-8")
        print("ok", name)
