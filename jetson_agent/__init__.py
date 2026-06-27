"""jetson_agent — per-agent companion that bridges the local nRF module to
Olympus over IP (redundant to the RF gateway) and fuses Jetson-side sensors.

Two transports live here:
  - agent.JetsonAgent — the binary swarm_proto link (swarm_node gateway firmware).
  - nrf_link.NrfLink  — the newline-delimited JSON link (coap_server firmware).
"""

# Lazy export so `python -m jetson_agent.nrf_link` doesn't import nrf_link twice
# (which triggers a runpy RuntimeWarning).
__all__ = ["NrfLink"]


def __getattr__(name):
    if name == "NrfLink":
        from .nrf_link import NrfLink
        return NrfLink
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
