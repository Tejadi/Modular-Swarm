"""olympus_link — command-station bridge between the nRF swarm and Olympus.

Reads the gateway nRF over serial, maintains the live module registry, localizes
non-GPS modules, runs the overlay autorouter, and pushes the picture into
Olympus (vehicle-api registration + Zenoh telemetry/topology). Stdlib-only.
"""
