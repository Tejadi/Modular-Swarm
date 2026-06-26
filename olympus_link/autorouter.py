"""Overlay autorouter — connects modules into a robust application topology.

OpenThread already routes packets across the mesh (L2/L3, self-healing). This
layer sits above it and decides *application* structure once modules are online:

  * an aggregation tree rooted at the command-station gateway, so every module
    has a primary parent to relay its telemetry toward the station,
  * a redundant secondary parent per module for fast failover,
  * provider -> consumer subscription edges, so modules that only consume swarm
    information are wired to the providers whose data they need.

The tree is a link-quality-weighted shortest-path tree (Dijkstra). Robustness
comes from: excluding offline nodes, recomputing every tick, re-parent
hysteresis (don't switch parents for a marginal gain — stops flapping), and a
secondary parent that gives an immediate fallback. A module the tree cannot
reach is flagged orphaned; the firmware's standing multicast announce keeps it
discoverable until a path reappears.

Pure Python — small graphs, no networkx needed.
"""

from __future__ import annotations

import heapq

import swarm_proto as sp
from config import Config
from model import ModuleState, Registry, RouteAssignment


def link_cost(rssi: int, range_cm: int, link_quality: int) -> float:
    """Lower is better. Blends signal strength, distance, and link quality."""
    cost = 1.0  # base per-hop cost
    cost += max(0.0, (-rssi - 50)) / 10.0       # weak signal penalty
    cost += (range_cm / 100.0) / 50.0           # distance penalty (per 50 m)
    cost += (255 - link_quality) / 64.0         # poor link penalty
    return max(0.1, cost)


class Autorouter:
    def __init__(self, reg: Registry, cfg: Config) -> None:
        self.reg = reg
        self.cfg = cfg

    # --- graph construction ---

    def _adjacency(self) -> dict[str, dict[str, float]]:
        """Symmetric weighted graph over online nodes (modules + gateway)."""
        online = {m.eui for m in self.reg.online_modules()}
        online.add(self.reg.gateway_eui)
        adj: dict[str, dict[str, float]] = {n: {} for n in online}

        for m in self.reg.online_modules():
            for nb_eui, nb in m.neighbors.items():
                if nb_eui not in online:
                    continue
                c = link_cost(nb.rssi, nb.range_cm, nb.link_quality)
                # Combine both observed directions into one symmetric edge.
                for a, b in ((m.eui, nb_eui), (nb_eui, m.eui)):
                    prev = adj[a].get(b)
                    adj[a][b] = c if prev is None else (prev + c) / 2.0
        return adj

    def _dijkstra(self, adj: dict[str, dict[str, float]], root: str):
        dist = {n: float("inf") for n in adj}
        prev: dict[str, str | None] = {n: None for n in adj}
        if root not in dist:
            return dist, prev
        dist[root] = 0.0
        pq = [(0.0, root)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]:
                continue
            for v, w in adj[u].items():
                nd = d + w
                if nd < dist[v]:
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        return dist, prev

    # --- main computation ---

    def compute(self) -> list[ModuleState]:
        """Recompute routes. Returns modules whose assignment changed."""
        adj = self._adjacency()
        root = self.reg.gateway_eui
        dist, prev = self._dijkstra(adj, root)

        changed: list[ModuleState] = []
        for m in self.reg.online_modules():
            if m.eui == root:
                continue

            primary = self._choose_primary(m, adj, dist, prev)
            secondary = self._choose_secondary(m, adj, dist, primary)
            subs = self._subscriptions(m)

            new = RouteAssignment(primary=primary or "", secondary=secondary or "",
                                  subscriptions=subs)
            h = hash((new.primary, new.secondary, tuple(sorted(new.subscriptions))))
            if h != m.route.pushed_hash:
                new.pushed_hash = h
                m.route = new
                changed.append(m)
            else:
                # Keep the assignment but preserve the hash so we don't resend.
                m.route.primary = new.primary
                m.route.secondary = new.secondary
                m.route.subscriptions = new.subscriptions
        return changed

    def _choose_primary(self, m, adj, dist, prev) -> str | None:
        candidate = prev.get(m.eui)  # optimal next hop toward root
        if candidate is None and dist.get(m.eui, float("inf")) == float("inf"):
            return None  # orphaned — no path to the gateway this tick

        current = m.route.primary
        # Hysteresis: stick with the current parent unless the new one is
        # meaningfully cheaper, to avoid flapping between near-equal links.
        if current and current in adj.get(m.eui, {}) and dist.get(current, float("inf")) < float("inf"):
            cost_cur = dist[current] + adj[m.eui][current]
            cost_new = (dist[candidate] + adj[m.eui][candidate]) if candidate else float("inf")
            if cost_new >= cost_cur * (1.0 - self.cfg.reparent_margin):
                return current
        return candidate

    def _choose_secondary(self, m, adj, dist, primary) -> str | None:
        """Best alternate next hop that is loop-free (closer to the root)."""
        best, best_cost = None, float("inf")
        my_dist = dist.get(m.eui, float("inf"))
        for nb, w in adj.get(m.eui, {}).items():
            if nb == primary:
                continue
            nd = dist.get(nb, float("inf"))
            if nd == float("inf") or nd >= my_dist:
                continue  # must be closer to root than us — keeps it acyclic
            c = nd + w
            if c < best_cost:
                best, best_cost = nb, c
        return best

    def _subscriptions(self, consumer: ModuleState) -> list[str]:
        """Which providers a consumer should pull swarm info from."""
        if not consumer.is_consumer:
            return []
        providers = [m for m in self.reg.online_modules()
                     if m.is_provider and m.eui != consumer.eui]
        if not providers:
            return []

        def rank(p: ModuleState) -> tuple[int, float]:
            # Prefer providers carrying sensors the consumer lacks, then nearer.
            new_sensors = bin(p.sensors & ~consumer.sensors).count("1")
            d = self._proximity(consumer, p)
            return (-new_sensors, d)

        providers.sort(key=rank)
        return [p.eui for p in providers[: self.cfg.max_subscriptions]]

    def _proximity(self, a: ModuleState, b: ModuleState) -> float:
        if a.position.valid and b.position.valid:
            from localization import _to_xy
            ax, ay = _to_xy(a.position.lat, a.position.lon, a.position.lat, a.position.lon)
            bx, by = _to_xy(b.position.lat, b.position.lon, a.position.lat, a.position.lon)
            return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
        # Fall back to "is it a direct neighbor" — direct links rank closer.
        return 0.0 if b.eui in a.neighbors else 1e6

    # --- outputs ---

    def route_message(self, m: ModuleState, seq: int) -> sp.Route:
        none = sp.EUI64_NONE
        primary = sp.eui_bytes(m.route.primary) if m.route.primary else none
        secondary = sp.eui_bytes(m.route.secondary) if m.route.secondary else none
        subs = [sp.eui_bytes(s) for s in m.route.subscriptions]
        return sp.Route(eui=sp.eui_bytes(m.eui), primary_parent=primary,
                        secondary_parent=secondary, subscriptions=subs, seq=seq)

    def orphans(self) -> list[str]:
        return [m.eui for m in self.reg.online_modules()
                if m.eui != self.reg.gateway_eui and not m.route.primary]

    def topology(self) -> dict:
        """JSON-serializable snapshot for the dashboard's swarm layer."""
        nodes, edges, tree = [], [], []
        seen_edges = set()
        for m in self.reg.online_modules():
            if m.eui == self.reg.gateway_eui:
                continue
            nodes.append({
                "id": m.eui,
                "name": m.name,
                "role": m.contribution(),
                "is_provider": m.is_provider,
                "is_consumer": m.is_consumer,
                "mount": "vehicle" if m.mount == sp.Mount.VEHICLE else "standalone",
                "attached_to": m.attached_to,
                "sensors": m.sensor_names(),
                "position": {"lat": m.position.lat, "lon": m.position.lon,
                             "alt": m.position.alt, "source": int(m.position.source),
                             "quality": m.position.quality},
                "battery": m.battery_pct,
                "status": int(m.status),
                "parent": m.route.primary,
                "secondary": m.route.secondary,
                "subscriptions": m.route.subscriptions,
            })
            if m.route.primary:
                tree.append({"child": m.eui, "parent": m.route.primary})
            for nb_eui, nb in m.neighbors.items():
                key = tuple(sorted((m.eui, nb_eui)))
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                edges.append({"a": m.eui, "b": nb_eui, "rssi": nb.rssi,
                              "range_m": nb.range_cm / 100.0,
                              "quality": nb.link_quality})
        return {"gateway": self.reg.gateway_eui, "nodes": nodes,
                "links": edges, "tree": tree, "orphans": self.orphans()}
