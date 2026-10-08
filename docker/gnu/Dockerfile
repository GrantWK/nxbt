FROM debian:stable-slim AS build

ARG TARGETARCH

# Compilers are only needed where a dependency has no pre-built wheel
# (cryptography on armv7/i386 needs Rust).
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv python3-dev gcc \
    && if [ "$TARGETARCH" != "amd64" ] && [ "$TARGETARCH" != "arm64" ]; then \
         apt-get install -y --no-install-recommends cargo libffi-dev libssl-dev pkg-config; \
       fi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY nxbt ./nxbt
RUN python3 -m venv /opt/nxbt \
    && /opt/nxbt/bin/pip install --no-cache-dir .


FROM debian:stable-slim

# libusb: Bumble USB transport. libcap: capability checks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 libcap2 libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/nxbt /opt/nxbt
ENV PATH="/opt/nxbt/bin:$PATH"

ENTRYPOINT ["nxbt"]
