FROM rustlang/rust:nightly-alpine AS builder

RUN apk add --no-cache \
    musl-dev gcc g++ make cmake pkgconfig sqlite-dev \
    git clang-dev llvm-dev build-base proj-dev proj-util

WORKDIR /usr/src/app
COPY . .

ENV LIBCLANG_PATH=/usr/lib
ENV RUSTFLAGS="-C target-feature=-crt-static"
ENV PROJ_SYS_USE_PKG_CONFIG=1

RUN cargo build --release

FROM alpine:latest
RUN apk add --no-cache libgcc libstdc++ sqlite-libs proj proj-util
WORKDIR /app

COPY --from=builder /usr/src/app/target/release/olympus-bridge .

RUN addgroup -S olympus && adduser -S olympus -G olympus
USER olympus

ENV ZENOH_ENDPOINTS=tcp/zenoh-router:7447
CMD ["./olympus-bridge"]
