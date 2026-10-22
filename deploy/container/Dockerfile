FROM python:3.12-slim-bookworm

ARG NODE_VERSION=24.12.0
ARG INSTALL_CLIS=1

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    MORPHD_CONTAINER_ISOLATED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git xz-utils build-essential \
 && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" -o /tmp/node.tar.xz \
 && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
 && rm /tmp/node.tar.xz \
 && npm install -g pnpm@10 \
 && if [ "$INSTALL_CLIS" = "1" ]; then \
      npm install -g \
        @deepseek-ai/dsh@0.2.1-rc.2 \
        @openai/codex@0.144.6 \
        @anthropic-ai/claude-code@2.1.302; \
    fi \
 && apt-get purge -y build-essential \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 morphd \
 && mkdir -p /opt/morphd /data/tasks /var/lib/morphd \
 && chown -R morphd:morphd /opt/morphd /data /var/lib/morphd

COPY pyproject.toml README.md LICENSE ./
COPY morphd ./morphd
COPY vendor ./vendor
RUN pip install --no-cache-dir ./vendor/harness2-src \
 && pip install --no-cache-dir . \
 && chown -R morphd:morphd /opt/morphd

COPY deploy/container/entrypoint.sh /usr/local/bin/morphd-entrypoint
RUN chmod 0755 /usr/local/bin/morphd-entrypoint

USER morphd
WORKDIR /opt/morphd
ENV MORPHD_HOME=/var/lib/morphd \
    MORPHD_TASKS=/data/tasks \
    HOME=/home/morphd

ENTRYPOINT ["/usr/local/bin/morphd-entrypoint"]
CMD ["--help"]
