from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .protocol import GeoPosition


@dataclass
class Neighbor:
    position: GeoPosition
    velocity: np.ndarray
    radius: float


@dataclass
class ORCAResult:
    velocity: np.ndarray
    is_adjusted: bool


class ORCAPlanner:

    DEFAULT_RADIUS = 10.0
    DEFAULT_NEIGHBOR_DIST = 50.0
    TIME_HORIZON = 5.0
    MIN_SEPARATION = 20.0
    EMERGENCY_SEPARATION = 10.0
    EMERGENCY_ALT_OFFSET = 15.0

    def __init__(
        self,
        agent_radius: float = DEFAULT_RADIUS,
        max_speed: float = 15.0,
        neighbor_dist: float = DEFAULT_NEIGHBOR_DIST,
    ):
        self._agent_radius = agent_radius
        self._max_speed = max_speed
        self._neighbor_dist = neighbor_dist

    def compute_safe_velocity(
        self,
        my_pos: GeoPosition,
        preferred_vel: np.ndarray,
        neighbors: list[Neighbor],
    ) -> ORCAResult:
        if not neighbors:
            return ORCAResult(velocity=preferred_vel, is_adjusted=False)

        orca_lines: list[tuple[np.ndarray, np.ndarray]] = []

        for neighbor in neighbors:
            rel_pos = _geo_to_local(my_pos, neighbor.position)
            rel_vel = preferred_vel - neighbor.velocity

            dist = float(np.linalg.norm(rel_pos))
            combined_radius = self._agent_radius + neighbor.radius

            if dist < 0.001:
                direction = np.array([1.0, 0.0])
                orca_lines.append((direction, direction * self._max_speed * 0.5))
                continue

            if dist < combined_radius:
                w = rel_vel - rel_pos / self.TIME_HORIZON
                w_len = float(np.linalg.norm(w))
                if w_len < 0.001:
                    direction = _normalize(rel_pos)
                else:
                    direction = _normalize(w)

                u = direction * (combined_radius / self.TIME_HORIZON - w_len)
                line_point = preferred_vel + u * 0.5
                line_dir = np.array([-direction[1], direction[0]])
                orca_lines.append((line_dir, line_point))
                continue

            leg = math.sqrt(dist * dist - combined_radius * combined_radius)
            rel_pos_unit = rel_pos / dist

            cos_theta = leg / dist
            sin_theta = combined_radius / dist

            left_dir = np.array([
                rel_pos_unit[0] * cos_theta - rel_pos_unit[1] * sin_theta,
                rel_pos_unit[0] * sin_theta + rel_pos_unit[1] * cos_theta,
            ])
            right_dir = np.array([
                rel_pos_unit[0] * cos_theta + rel_pos_unit[1] * sin_theta,
                -rel_pos_unit[0] * sin_theta + rel_pos_unit[1] * cos_theta,
            ])

            if _det(rel_pos, rel_vel) > 0:
                direction = left_dir
                line_dir = np.array([direction[1], -direction[0]])
            else:
                direction = right_dir
                line_dir = np.array([-direction[1], direction[0]])

            proj = np.dot(rel_vel, direction)
            u = direction * max(0, proj) - rel_vel
            u = u + direction * (combined_radius / self.TIME_HORIZON)
            line_point = preferred_vel + u * 0.5
            orca_lines.append((line_dir, line_point))

        if not orca_lines:
            return ORCAResult(velocity=preferred_vel, is_adjusted=False)

        result = self._solve_linear_program(orca_lines, preferred_vel)
        speed = float(np.linalg.norm(result))
        if speed > self._max_speed:
            result = result * (self._max_speed / speed)

        is_adjusted = float(np.linalg.norm(result - preferred_vel)) > 0.1
        return ORCAResult(velocity=result, is_adjusted=is_adjusted)

    def check_altitude_separation(
        self,
        my_pos: GeoPosition,
        neighbors: list[GeoPosition],
        my_role_altitude: float,
    ) -> Optional[float]:
        for n in neighbors:
            horiz_dist = my_pos.distance_to(n)
            alt_diff = abs(my_pos.altitude - n.altitude)

            if horiz_dist < self.EMERGENCY_SEPARATION and alt_diff < self.MIN_SEPARATION:
                if my_pos.altitude <= n.altitude:
                    return my_role_altitude - self.EMERGENCY_ALT_OFFSET
                else:
                    return my_role_altitude + self.EMERGENCY_ALT_OFFSET

        return None

    def get_neighbors(
        self,
        my_id: str,
        swarm_positions: dict[str, GeoPosition],
        swarm_velocities: dict[str, np.ndarray],
    ) -> list[Neighbor]:
        my_pos = swarm_positions.get(my_id)
        if my_pos is None:
            return []

        result = []
        for drone_id, pos in swarm_positions.items():
            if drone_id == my_id:
                continue

            dist = my_pos.distance_to(pos)
            if dist > self._neighbor_dist:
                continue

            vel = swarm_velocities.get(drone_id, np.zeros(2))
            result.append(Neighbor(
                position=pos,
                velocity=vel,
                radius=self._agent_radius,
            ))

        return result

    def _solve_linear_program(
        self,
        lines: list[tuple[np.ndarray, np.ndarray]],
        preferred: np.ndarray,
    ) -> np.ndarray:
        result = preferred.copy()

        for direction, line_point in lines:
            if np.dot(direction, result - line_point) < 0:
                t_opt = np.dot(direction, preferred - line_point)
                result = line_point + direction * t_opt

                speed = float(np.linalg.norm(result))
                if speed > self._max_speed:
                    result = result * (self._max_speed / speed)

        return result


def _geo_to_local(origin: GeoPosition, target: GeoPosition) -> np.ndarray:
    R = 6_371_000.0
    dlat = math.radians(target.latitude - origin.latitude)
    dlon = math.radians(target.longitude - origin.longitude)
    cos_lat = math.cos(math.radians(origin.latitude))
    return np.array([dlon * cos_lat * R, dlat * R])


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n < 1e-10:
        return np.array([1.0, 0.0])
    return v / n


def _det(a: np.ndarray, b: np.ndarray) -> float:
    return float(a[0] * b[1] - a[1] * b[0])
