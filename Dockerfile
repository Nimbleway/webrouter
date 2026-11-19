# The web-router MCP server over streamable HTTP, for a team that self-hosts one shared server.
#
#   docker build -t webrouter-mcp .
#   docker run --rm -p 127.0.0.1:8000:8000 -e WEB_ROUTER_MCP_TOKEN -e TAVILY_API_KEY webrouter-mcp
#
# It listens on 0.0.0.0 inside the container, so it refuses to start without WEB_ROUTER_MCP_TOKEN, and every
# request must send "Authorization: Bearer <token>". Provider keys come from the environment you pass with -e
# or --env-file; the image holds none and reads no .env. See docs/mcp-server.mdx.

# Base images pinned by digest (tag kept for reading), so a rebuild gets the same bytes; bump both on purpose.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --extra mcp --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra mcp --no-editable

FROM ${PYTHON_IMAGE}
LABEL io.modelcontextprotocol.server.name="io.github.Nimbleway/webrouter" \
      org.opencontainers.image.source="https://github.com/Nimbleway/webrouter" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.description="web-router MCP server over streamable HTTP; bring your own keys"
RUN useradd --create-home --uid 10001 webrouter
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    WEB_ROUTER_MCP_LOAD_ENV=off \
    WEB_ROUTER_MCP_CWD_ENV=off \
    WEB_ROUTER_MCP_REMEMBERED_ENV=off
USER webrouter
WORKDIR /home/webrouter
EXPOSE 8000
ENTRYPOINT ["web-router-mcp", "--transport", "http", "--host", "0.0.0.0"]
CMD ["--port", "8000"]
