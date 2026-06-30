# ---- Builder Stage ----
FROM rust:1.75-slim AS builder

RUN apt-get update && apt-get install -y \
    pkg-config \
    libssl-dev \
    cmake \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/app

# Copy the rust-bridge dependency first (for layer caching)
COPY ../rust-bridge /usr/src/rust-bridge

# Copy manifests
COPY Cargo.toml Cargo.lock* ./

# Create a dummy main.rs to cache dependency builds
RUN mkdir -p src && echo 'fn main() { println!("placeholder"); }' > src/main.rs

# Patch the path dependency for Docker context
RUN sed -i 's|path = "../rust-bridge"|path = "/usr/src/rust-bridge"|' Cargo.toml

# Build dependencies only (cached layer)
RUN cargo build --release 2>/dev/null || true

# Copy actual source code
COPY src/ src/

# Build the real binary
RUN cargo build --release

# ---- Runtime Stage ----
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y \
    ca-certificates \
    libssl3 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash appuser

COPY --from=builder /usr/src/app/target/release/vehicle-api /usr/local/bin/vehicle-api

USER appuser

ENV VEHICLE_API_PORT=3001
ENV ZENOH_ENDPOINTS=tcp/zenoh-router:7447
ENV RUST_LOG=vehicle_api=info,tower_http=info

EXPOSE 3001

CMD ["vehicle-api"]
