from __future__ import annotations

import numpy as np
from scipy.spatial import Voronoi
from shapely.geometry import Polygon, Point, MultiPolygon
from shapely.ops import unary_union
from typing import Optional
import logging

from olympus_brain.protocol import GeoPosition

logger = logging.getLogger(__name__)


class FieldPartitioner:

    def __init__(self, field_boundary: list[tuple[float, float]]):
        self.field_polygon = Polygon(field_boundary)
        self.field_bounds = self.field_polygon.bounds

        if not self.field_polygon.is_valid:
            self.field_polygon = self.field_polygon.buffer(0)

        logger.info(f"Field partitioner initialized: area={self.field_polygon.area:.6f} deg^2")

    def compute_partitions(
        self,
        drone_positions: list[tuple[float, float]],
        lloyd_iterations: int = 10,
    ) -> list[Polygon]:
        if len(drone_positions) < 1:
            return [self.field_polygon]

        if len(drone_positions) == 1:
            return [self.field_polygon]

        generators = np.array(drone_positions)

        for i in range(lloyd_iterations):
            partitions = self._voronoi_partition(generators)

            if not partitions:
                logger.warning(f"Lloyd iteration {i} produced no partitions")
                break

            new_generators = []
            for poly in partitions:
                if poly and not poly.is_empty:
                    centroid = poly.centroid
                    new_generators.append([centroid.x, centroid.y])
                else:
                    idx = len(new_generators)
                    if idx < len(generators):
                        new_generators.append(generators[idx].tolist())

            if len(new_generators) == len(generators):
                generators = np.array(new_generators)
            else:
                break

        partitions = self._voronoi_partition(generators)

        logger.info(f"Computed {len(partitions)} partitions after {lloyd_iterations} iterations")
        return partitions

    def _voronoi_partition(self, generators: np.ndarray) -> list[Polygon]:
        if len(generators) < 2:
            return [self.field_polygon]

        minx, miny, maxx, maxy = self.field_bounds
        padding = max(maxx - minx, maxy - miny) * 2

        dummy_points = np.array([
            [minx - padding, miny - padding],
            [minx - padding, maxy + padding],
            [maxx + padding, miny - padding],
            [maxx + padding, maxy + padding],
        ])

        all_points = np.vstack([generators, dummy_points])

        try:
            vor = Voronoi(all_points)
        except Exception as e:
            logger.error(f"Voronoi computation failed: {e}")
            return []

        partitions = []
        for i in range(len(generators)):
            region_idx = vor.point_region[i]
            if region_idx == -1:
                partitions.append(None)
                continue

            region = vor.regions[region_idx]
            if -1 in region or len(region) == 0:
                partitions.append(None)
                continue

            try:
                vertices = [vor.vertices[j] for j in region]
                poly = Polygon(vertices)

                clipped = poly.intersection(self.field_polygon)

                if isinstance(clipped, MultiPolygon):
                    clipped = max(clipped.geoms, key=lambda p: p.area)

                if clipped.is_valid and not clipped.is_empty:
                    partitions.append(clipped)
                else:
                    partitions.append(None)

            except Exception as e:
                logger.warning(f"Failed to create partition {i}: {e}")
                partitions.append(None)

        return [p if p else Polygon() for p in partitions]

    def get_partition_for_drone(
        self,
        drone_position: tuple[float, float],
        all_positions: list[tuple[float, float]],
    ) -> Optional[Polygon]:
        try:
            drone_idx = all_positions.index(drone_position)
        except ValueError:
            logger.warning("Drone position not in position list")
            return None

        partitions = self.compute_partitions(all_positions)

        if drone_idx < len(partitions):
            return partitions[drone_idx]
        return None

    @staticmethod
    def polygon_to_coordinates(poly: Polygon) -> list[tuple[float, float]]:
        if poly.is_empty:
            return []
        return list(poly.exterior.coords)

    @staticmethod
    def coordinates_to_geopositions(
        coords: list[tuple[float, float]],
        altitude: float = 0.0,
    ) -> list[GeoPosition]:
        return [
            GeoPosition(latitude=lat, longitude=lon, altitude=altitude)
            for lat, lon in coords
        ]


class CoveragePathPlanner:

    def __init__(self, swath_width: float = 0.0001):
        self.swath_width = swath_width

    def generate_path(
        self,
        region: Polygon,
        start_position: Optional[tuple[float, float]] = None,
        angle: float = 0.0,
    ) -> list[tuple[float, float]]:
        if region.is_empty:
            return []

        bounds = region.bounds
        minx, miny, maxx, maxy = bounds

        waypoints = []
        y = miny
        direction = 1

        while y <= maxy:
            if direction == 1:
                line_start = (minx, y)
                line_end = (maxx, y)
            else:
                line_start = (maxx, y)
                line_end = (minx, y)

            from shapely.geometry import LineString
            line = LineString([line_start, line_end])
            clipped = line.intersection(region)

            if not clipped.is_empty:
                if hasattr(clipped, 'coords'):
                    coords = list(clipped.coords)
                    if direction == -1:
                        coords.reverse()
                    waypoints.extend(coords)
                elif hasattr(clipped, 'geoms'):
                    for segment in clipped.geoms:
                        coords = list(segment.coords)
                        if direction == -1:
                            coords.reverse()
                        waypoints.extend(coords)

            y += self.swath_width
            direction *= -1

        if start_position and waypoints:
            start = np.array(start_position)
            distances = [np.linalg.norm(np.array(wp) - start) for wp in waypoints]
            nearest_idx = np.argmin(distances)

            waypoints = waypoints[nearest_idx:] + waypoints[:nearest_idx]

        logger.info(f"Generated coverage path with {len(waypoints)} waypoints")
        return waypoints

    def estimate_flight_time(
        self,
        path: list[tuple[float, float]],
        speed_mps: float = 10.0,
    ) -> float:
        if len(path) < 2:
            return 0.0

        total_distance = 0.0
        for i in range(len(path) - 1):
            p1 = GeoPosition(latitude=path[i][0], longitude=path[i][1])
            p2 = GeoPosition(latitude=path[i+1][0], longitude=path[i+1][1])
            total_distance += p1.distance_to(p2)

        return total_distance / speed_mps


def compute_field_coverage(
    field_boundary: list[tuple[float, float]],
    drone_positions: list[tuple[float, float]],
    swath_width: float = 0.0001,
) -> dict[int, list[tuple[float, float]]]:
    partitioner = FieldPartitioner(field_boundary)
    planner = CoveragePathPlanner(swath_width)

    partitions = partitioner.compute_partitions(drone_positions)

    paths = {}
    for i, (partition, position) in enumerate(zip(partitions, drone_positions)):
        if partition and not partition.is_empty:
            path = planner.generate_path(partition, start_position=position)
            paths[i] = path
        else:
            paths[i] = []

    return paths
