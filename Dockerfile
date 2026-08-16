FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AGENT_PLATFORM_PROJECT_ROOT=/app \
    AGENT_PLATFORM_CONTAINER_MODE=true \
    ALLOW_LIVE_TRADING=false

RUN groupadd --gid 10001 agent && useradd --uid 10001 --gid agent --create-home agent

WORKDIR /app
COPY requirements.lock pyproject.toml README.md ./
COPY src ./src
COPY Scripts ./Scripts
COPY tests/fixtures ./tests/fixtures
COPY Workflow ./Workflow
COPY docs ./docs
COPY Rule ./Rule
COPY Skill ./Skill
COPY MCP ./MCP
COPY SubAgents ./SubAgents
COPY ROADMAP.md SPEC.md checklist.json progress.txt dev-map.md ./

RUN python -m pip install --requirement requirements.lock \
    && python -m pip install --no-build-isolation --no-deps . \
    && mkdir -p /app/.runtime \
    && chown -R agent:agent /app/.runtime

USER 10001:10001
EXPOSE 8765

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=4 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2).read()"

CMD ["agent-platform", "dashboard", "--host", "0.0.0.0", "--port", "8765", "--no-browser", "--no-key-prompt"]
