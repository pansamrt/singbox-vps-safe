ARG DEBIAN_RELEASE=12
FROM debian:${DEBIAN_RELEASE}-slim
RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends \
    python3 ca-certificates curl iproute2 passwd util-linux openssl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /work
CMD ["bash", "/work/tests/integration/manual_install_ci.sh"]
