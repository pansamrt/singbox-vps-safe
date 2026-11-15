# Runs ONLY in disposable GitHub Actions Docker containers.
# No privileges, host network, Docker socket, host system mount or VPS credentials.
ARG DEBIAN_RELEASE=12
FROM debian:${DEBIAN_RELEASE}-slim

RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /work
CMD ["python3", "/work/tests/integration/debian_runtime.py"]
