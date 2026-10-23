# Remote (HTTP) Garmin Connect MCP server — official-API-only, no Garmin
# password ever enters this image. See src/garmin_mcp/remote_server.py and
# the "Remote server (phone-reachable)" section of README.md for the
# required environment variables (GARMIN_PERSONAL_API_KEY,
# GARMIN_MCP_PUBLIC_URL, GARMIN_MCP_ACCESS_PASSWORD).
#
# The *local* hybrid server (garmin-mcp-server, stdio, 34 tools) is not
# meant to run from this image — it's for Claude Desktop/Cursor on your
# own machine. This image only ever runs garmin-mcp-remote-server.

FROM python:3.12-slim

WORKDIR /app

# The official Garmin client (garmin-connect-personal-api-python) needs
# Python 3.12+; the unofficial fallback isn't installed/used here at all.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir .

# Railway sets PORT itself; 8000 is just the local-run default.
ENV PORT=8000
EXPOSE 8000

CMD ["garmin-mcp-remote-server"]
