"""Batted-ball flight physics: moist-air density, drag, Magnus lift, wind.

Pure Python (no numpy). Follows the conventions of Alan Nathan's public
Trajectory Calculator: a drag coefficient that grows slightly with spin, a lift
coefficient driven by the spin parameter S = r*omega/v, and RK4 integration.

Coordinates for a single flight:  x = along the batted-ball horizontal
direction, y = lateral (left of travel is +y), z = up.  Wind is given in the
same frame as (tail, cross) components in m/s.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# ---- ball & air constants ---------------------------------------------------
M_BALL = 0.1453                      # kg   (5.125 oz)
R_BALL = 0.03683                     # m    (circumference 9.125 in)
A_BALL = math.pi * R_BALL ** 2       # m^2
G = 9.80665
R_DRY = 287.058                      # J/(kg K)
R_VAP = 461.495

# Nathan trajectory-calculator aerodynamic constants
CD0, CDSPIN = 0.3008, 0.0292         # Cd = CD0 + CDSPIN * (rpm / 1000)
CL0, CL1, CL2 = 0.583, 2.333, 1.120  # Cl = CL2 * S / (CL0 + CL1 * S)

# Reference atmosphere used for "표준 대비" comparisons
REF_TEMP_C = 20.0
REF_PRESSURE_HPA = 1013.25
REF_RH = 50.0


def saturation_vapor_pressure_hpa(temp_c: float) -> float:
    """Magnus formula over liquid water (hPa)."""
    return 6.1078 * math.exp(17.27 * temp_c / (temp_c + 237.3))


def air_density(temp_c: float, pressure_hpa: float, rh_pct: float) -> float:
    """Moist-air density in kg/m^3 from *station* pressure (not sea-level)."""
    e = max(0.0, min(100.0, rh_pct)) / 100.0 * saturation_vapor_pressure_hpa(temp_c)
    p_dry = pressure_hpa - e
    t_k = temp_c + 273.15
    return (p_dry * 100.0) / (R_DRY * t_k) + (e * 100.0) / (R_VAP * t_k)


def station_pressure_from_msl(pressure_msl_hpa: float, elevation_m: float, temp_c: float) -> float:
    """Reduce mean-sea-level pressure to the venue elevation (hypsometric)."""
    t_k = temp_c + 273.15
    return pressure_msl_hpa * math.exp(-G * elevation_m / (R_DRY * t_k))


REF_DENSITY = air_density(REF_TEMP_C, REF_PRESSURE_HPA, REF_RH)


@dataclass(frozen=True)
class Launch:
    speed_ms: float = 44.7        # 100 mph ≈ 161 km/h
    angle_deg: float = 28.0       # typical home-run launch angle
    backspin_rpm: float = 1800.0
    height_m: float = 1.0


@dataclass(frozen=True)
class Flight:
    distance_m: float
    hang_time_s: float
    apex_m: float
    lateral_m: float
    path: tuple = ()          # (수평 거리, 높이) 표본 — 대시보드 궤적 그림용


def fly(launch: Launch, rho: float, tail_ms: float = 0.0, cross_ms: float = 0.0,
        dt: float = 0.004, trace: bool = False) -> Flight:
    """Integrate one batted ball until it returns to ground level (z = 0)."""
    omega = launch.backspin_rpm * 2.0 * math.pi / 60.0      # rad/s
    cd = CD0 + CDSPIN * (launch.backspin_rpm / 1000.0)
    k = rho * A_BALL / (2.0 * M_BALL)                        # 1/m

    ang = math.radians(launch.angle_deg)
    x, y, z = 0.0, 0.0, launch.height_m
    vx, vy, vz = launch.speed_ms * math.cos(ang), 0.0, launch.speed_ms * math.sin(ang)

    def accel(vx: float, vy: float, vz: float):
        # velocity relative to the moving air
        rx, ry, rz = vx - tail_ms, vy - cross_ms, vz
        v = math.sqrt(rx * rx + ry * ry + rz * rz) or 1e-9
        s = R_BALL * omega / v
        cl = CL2 * s / (CL0 + CL1 * s)
        drag = -k * cd * v
        # pure backspin: spin axis = -y  ->  lift direction = (-y) x v_hat
        lx, ly, lz = -rz / v, 0.0, rx / v
        lift = k * cl * v * v
        return (drag * rx + lift * lx,
                drag * ry + lift * ly,
                drag * rz + lift * lz - G)

    t = 0.0
    apex = z
    step = 0
    path = [(0.0, z)] if trace else []
    while True:
        # RK4 step on (pos, vel)
        a1 = accel(vx, vy, vz)
        k1 = (vx, vy, vz, *a1)
        a2 = accel(vx + 0.5 * dt * k1[3], vy + 0.5 * dt * k1[4], vz + 0.5 * dt * k1[5])
        k2 = (vx + 0.5 * dt * k1[3], vy + 0.5 * dt * k1[4], vz + 0.5 * dt * k1[5], *a2)
        a3 = accel(vx + 0.5 * dt * k2[3], vy + 0.5 * dt * k2[4], vz + 0.5 * dt * k2[5])
        k3 = (vx + 0.5 * dt * k2[3], vy + 0.5 * dt * k2[4], vz + 0.5 * dt * k2[5], *a3)
        a4 = accel(vx + dt * k3[3], vy + dt * k3[4], vz + dt * k3[5])
        k4 = (vx + dt * k3[3], vy + dt * k3[4], vz + dt * k3[5], *a4)
        nx = x + dt / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        ny = y + dt / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
        nz = z + dt / 6 * (k1[2] + 2 * k2[2] + 2 * k3[2] + k4[2])
        vx += dt / 6 * (k1[3] + 2 * k2[3] + 2 * k3[3] + k4[3])
        vy += dt / 6 * (k1[4] + 2 * k2[4] + 2 * k3[4] + k4[4])
        vz += dt / 6 * (k1[5] + 2 * k2[5] + 2 * k3[5] + k4[5])
        t += dt
        if nz <= 0.0 and t > 0.5:
            # linear interpolation to z = 0
            f = z / (z - nz) if z != nz else 1.0
            x += (nx - x) * f
            y += (ny - y) * f
            t -= dt * (1.0 - f)
            break
        x, y, z = nx, ny, nz
        apex = max(apex, z)
        step += 1
        if trace and step % 25 == 0:
            path.append((math.hypot(x, y), z))
        if t > 20.0:
            break
    if trace:
        path.append((math.hypot(x, y), 0.0))
    return Flight(distance_m=math.hypot(x, y), hang_time_s=t, apex_m=apex, lateral_m=y,
                  path=tuple((round(a, 1), round(b, 2)) for a, b in path))


# ---- wind geometry ------------------------------------------------------------
def wind_components(wind_speed_ms: float, wind_from_deg: float, ball_bearing_deg: float):
    """Decompose a meteorological wind (direction it blows FROM) into
    (tail, cross) relative to a ball travelling toward `ball_bearing_deg`.
    tail > 0 helps the ball; cross > 0 pushes it to the left of travel."""
    to_deg = (wind_from_deg + 180.0) % 360.0
    rel = math.radians(to_deg - ball_bearing_deg)
    tail = wind_speed_ms * math.cos(rel)
    cross = -wind_speed_ms * math.sin(rel)
    return tail, cross


@dataclass(frozen=True)
class CarryResult:
    direction: str                # "LF" / "CF" / "RF"
    bearing_deg: float
    distance_m: float
    delta_vs_ref_m: float
    tail_ms: float
    cross_ms: float
    hang_time_s: float


def carry_report(temp_c: float, pressure_station_hpa: float, rh_pct: float,
                 wind_speed_ms: float, wind_from_deg: float | None,
                 cf_azimuth_deg: float | None, shelter: float = 1.0,
                 launch: Launch = Launch()) -> dict:
    """Standard fly ball (100 mph / 28° / 1800 rpm) under today's air + wind,
    compared with the reference atmosphere (20°C, 1013 hPa, 50 %, calm).

    `shelter` scales the ambient 10 m wind to what the ball actually feels
    inside the bowl (0 = dome, ~0.55 enclosed stadium, ~0.85 open field)."""
    rho = air_density(temp_c, pressure_station_hpa, rh_pct)
    ref = fly(launch, REF_DENSITY, trace=True)
    calm = fly(launch, rho, trace=True)
    out = {
        "air_density": round(rho, 4),
        "ref_density": round(REF_DENSITY, 4),
        "density_delta_pct": round((rho / REF_DENSITY - 1.0) * 100.0, 2),
        "ref_distance_m": round(ref.distance_m, 1),
        "ref_hang_s": round(ref.hang_time_s, 2),
        "calm_distance_m": round(calm.distance_m, 1),
        "air_only_delta_m": round(calm.distance_m - ref.distance_m, 1),
        "effective_wind_ms": round(wind_speed_ms * shelter, 2),
        "path_ref": list(ref.path),      # 표준 대기 궤적 (비교선)
        "path_cf": list(calm.path),      # 중앙 방향 오늘 궤적 (바람 반영 시 아래에서 교체)
        "directions": [],
    }
    if cf_azimuth_deg is None or wind_from_deg is None:
        for name, off in (("LF", -45.0), ("CF", 0.0), ("RF", 45.0)):
            out["directions"].append(CarryResult(name, float("nan"), round(calm.distance_m, 1),
                                                 round(calm.distance_m - ref.distance_m, 1),
                                                 0.0, 0.0, round(calm.hang_time_s, 2)).__dict__)
        return out
    w = wind_speed_ms * shelter
    for name, off in (("LF", -45.0), ("CF", 0.0), ("RF", 45.0)):
        bearing = (cf_azimuth_deg + off) % 360.0
        tail, cross = wind_components(w, wind_from_deg, bearing)
        f = fly(launch, rho, tail, cross, trace=(name == 'CF'))
        if name == 'CF':
            out['path_cf'] = list(f.path)
        out["directions"].append(CarryResult(name, round(bearing, 1), round(f.distance_m, 1),
                                             round(f.distance_m - ref.distance_m, 1),
                                             round(tail, 2), round(cross, 2),
                                             round(f.hang_time_s, 2)).__dict__)
    return out


def height_at(path, distance_m: float) -> float | None:
    """궤적이 그 거리를 지날 때의 높이(m). 담장을 넘는지 보려면 착지 거리가 아니라 이 값이 필요하다."""
    pts = [(x, z) for x, z in path]
    if not pts or distance_m < pts[0][0] or distance_m > pts[-1][0]:
        return None
    for (x0, z0), (x1, z1) in zip(pts, pts[1:]):
        if x0 <= distance_m <= x1:
            if x1 == x0:
                return round(z1, 2)
            f = (distance_m - x0) / (x1 - x0)
            return round(z0 + (z1 - z0) * f, 2)
    return None


def hr_rate_multiplier(delta_m: float, ref_distance_m: float) -> float:
    """Rule of thumb (Nathan): +1 % fly-ball carry ≈ +6 % home runs."""
    return 1.0 + 6.0 * (delta_m / ref_distance_m)


if __name__ == "__main__":  # quick self-check
    ref = fly(Launch(), REF_DENSITY)
    print(f"ref rho={REF_DENSITY:.4f}  D={ref.distance_m:.1f} m  hang={ref.hang_time_s:.2f} s  apex={ref.apex_m:.1f} m")
    for t in (0, 10, 20, 30, 35):
        rho = air_density(t, 1013.25, 50)
        print(f"T={t:2d}C rho={rho:.4f}  D={fly(Launch(), rho).distance_m:.1f}")
    for w in (-5, -2, 0, 2, 5):
        print(f"tail={w:+d} m/s  D={fly(Launch(), REF_DENSITY, tail_ms=w).distance_m:.1f}")
