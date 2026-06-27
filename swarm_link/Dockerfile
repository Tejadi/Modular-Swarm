# swarm-link — host-side bridge: nRF gateway/leader serial <-> Olympus.
#
# Reuses the olympus_link package (registry, localization, autorouter) and the
# shared proto/ codec, so this image is just a deploy wrapper — the logic stays
# in olympus_link/ which jetson_agent and sim/ also import. Publishes telemetry
# and topology to the Olympus Zenoh router and registers modules via vehicle-api.
#
# Build context is the repo root (so proto/ and olympus_link/ are both visible):
#   docker build -f swarm_link/Dockerfile -t swarm-link .
FROM python:3.11-slim

WORKDIR /app

# pyserial for the real gateway device; eclipse-zenoh enables the native sink
# (the default "rest" sink needs neither and talks to the zenoh REST API).
RUN pip install --no-cache-dir pyserial eclipse-zenoh

COPY proto/ /app/proto/
COPY olympus_link/ /app/olympus_link/

# All knobs come from SWARM_* env vars (see olympus_link/config.py). __main__
# adds proto/ and itself to sys.path, so `-m olympus_link` runs from /app.
CMD ["python", "-m", "olympus_link"]
