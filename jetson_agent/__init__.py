"""jetson_agent — per-agent companion that bridges the local nRF module to
Olympus over IP (redundant to the RF gateway) and fuses Jetson-side sensors.

Two transports live here:
  - agent.JetsonAgent — the binary swarm_proto link (swarm_node gateway firmware).
  - nrf_link.NrfLink  — the newline-delimited JSON link (coap_server firmware).
"""

from .nrf_link import NrfLink

__all__ = ["NrfLink"]
