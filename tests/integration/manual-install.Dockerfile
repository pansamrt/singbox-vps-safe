FROM debian:12-slim
RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends \
    python3 ca-certificates curl iproute2 passwd util-linux \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /work
CMD ["bash", "/work/tests/integration/manual_install_ci.sh"]
