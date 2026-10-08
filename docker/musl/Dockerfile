FROM alpine:3.21 AS build

ARG TARGETARCH

# Compilers are only needed where a dependency has no pre-built wheel
# (cryptography on armv7/i386 needs Rust).
RUN apk add --no-cache python3 python3-dev gcc musl-dev linux-headers \
    && if [ "$TARGETARCH" != "amd64" ] && [ "$TARGETARCH" != "arm64" ]; then \
         apk add --no-cache cargo libffi-dev openssl-dev; \
       fi

WORKDIR /src
COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY nxbt ./nxbt
RUN python3 -m venv /opt/nxbt \
    && /opt/nxbt/bin/pip install --no-cache-dir .


FROM alpine:3.21

# libusb: Bumble USB transport. libcap: capability checks.
RUN apk add --no-cache python3 libcap libusb

COPY --from=build /opt/nxbt /opt/nxbt
ENV PATH="/opt/nxbt/bin:$PATH"

ENTRYPOINT ["nxbt"]
