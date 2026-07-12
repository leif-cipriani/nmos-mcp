# NMOS MCP server — container image.
#
# Runs the stdio MCP server by default (for Claude Code / Desktop, launched via
# `docker run -i --rm ...`). Pass `--http` as an argument to serve the
# streamable-HTTP transport instead.
FROM python:3.13-slim

# Keep Python output unbuffered and skip .pyc files (small, predictable containers).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install the package. Only the files needed to build the wheel are copied so the
# image layer caches well and stays small (no tests, no .venv, no .env).
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Run as a non-root user.
RUN useradd --create-home --uid 10001 app \
    && chown -R app:app /app
USER app

# stdout is reserved for the MCP stdio protocol; all logs go to stderr.
# `docker run <image>`         -> stdio transport
# `docker run <image> --http`  -> streamable-HTTP transport (expose with -p 8000:8000)
ENTRYPOINT ["nmos-mcp"]
