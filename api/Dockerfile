# Bronze API image (M0-03).
#
# Python 3.12 with uv pinned to the repo toolchain version (docs/SETUP.md,
# UV_VERSION in .github/workflows/ci.yml). Dependencies come from uv.lock and
# nothing else; the build context is the repo root because the lockfile lives
# there (uv workspace, ADR-0014). Base images are pinned by index digest.

FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
COPY --from=ghcr.io/astral-sh/uv:0.12.12@sha256:73d2665b478d8fa2de1cf105c6841f8e9cb6b09e568fc7700440c09f8fcd7ac4 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Third-party dependencies first, so editing api/src does not invalidate them.
COPY pyproject.toml uv.lock ./
COPY api/pyproject.toml api/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace

# Then the workspace member itself (editable, so the compose bind mount of
# ./api/src is what --reload watches).
COPY api/src api/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

RUN useradd --system --create-home --uid 1000 --user-group app
USER app

EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=5s --start-period=15s --retries=12 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
CMD ["/app/.venv/bin/uvicorn", "bronze_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
