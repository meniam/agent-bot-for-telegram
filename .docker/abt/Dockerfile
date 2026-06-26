# Multi-bot Telegram agent gateway.
#
# The three agent backends are external CLIs the image must carry:
#   - claude  -> npm @anthropic-ai/claude-code   (Node)
#   - pi      -> npm @earendil-works/pi-coding-agent (Node)
#   - codex   -> npm @openai/codex               (ships a platform binary)
# All three are Node-based or Node-installed, so Node lives in the image and
# Python drives them over subprocess/SDK.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5 \
    NPM_CONFIG_UPDATE_NOTIFIER=false

# Node 22 LTS + the few tools the agents shell out to (git for repo ops).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates gnupg git \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

# Agent CLIs on PATH (global npm bin -> /usr/local/bin).
RUN npm install -g \
      @anthropic-ai/claude-code \
      @earendil-works/pi-coding-agent \
      @openai/codex

WORKDIR /app

# Install Python deps first for layer caching, then the editable package.
# Editable keeps `src` importable from /app so a bind-mounted config at
# /app/src/config/config.yaml is the one the loader reads (CONFIG_DIR is
# resolved from src/config/__init__.py).
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY .agents ./.agents
RUN pip install -e .

COPY .docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "src.bot"]
