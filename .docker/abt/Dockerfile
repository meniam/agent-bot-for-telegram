# Multi-bot Telegram agent gateway.
#
# The three agent backends are external CLIs the image must carry:
#   - claude  -> npm @anthropic-ai/claude-code   (Node)
#   - pi      -> npm @earendil-works/pi-coding-agent (Node)
#   - codex   -> npm @openai/codex               (ships a platform binary)
# All three are Node-based or Node-installed, so Node lives in the image and
# Python drives them over subprocess/SDK.
FROM python:3.14-slim

# UV_PYTHON_PREFERENCE overrides the project's `only-managed` (pyproject.toml):
# inside the image the interpreter is the one the base image ships, uv must
# not download another. The venv goes first on PATH so `python` in CMD and in
# the compose healthcheck is the project interpreter.
ENV PYTHONUNBUFFERED=1 \
    UV_PYTHON_PREFERENCE=only-system \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    NPM_CONFIG_UPDATE_NOTIFIER=false

# Node 22 LTS + the tools the agents shell out to: git for repo ops,
# openssh-client to ssh into other hosts via mounted keys, ripgrep/fd/jq/tree
# for fast search & JSON handling, sqlite3 for local DB inspection,
# poppler-utils/unzip/ffmpeg/imagemagick for PDF/archive/media uploads.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      curl ca-certificates gnupg git openssh-client \
      ripgrep fd-find jq tree sqlite3 \
      poppler-utils unzip ffmpeg imagemagick \
 && ln -s /usr/bin/fdfind /usr/local/bin/fd \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

# Agent CLIs on PATH (global npm bin -> /usr/local/bin).
RUN npm install -g \
      @anthropic-ai/claude-code \
      @earendil-works/pi-coding-agent \
      @openai/codex

# uv/uvx on PATH — MCP servers configured to run via `uvx` (e.g. postgres-mcp
# in the vault's .mcp.json) shell out to it.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install locked runtime deps first for layer caching, then the project.
# `uv sync` installs the project editable, which keeps `src` importable from
# /app so a bind-mounted config at /app/src/config/config.yaml is the one the
# loader reads (CONFIG_DIR is resolved from src/config/__init__.py).
COPY pyproject.toml uv.lock .python-version README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
COPY .agents ./.agents
RUN uv sync --locked --no-dev

COPY .docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "src.bot"]
