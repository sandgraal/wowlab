# Bronze sim worker image (M0-03).
#
# Stage 1 compiles SimulationCraft from a pinned commit. Stage 2 is the runtime
# the M2 job loop will live in: Python 3.12 with uv at the repo toolchain
# version, the same base as api/Dockerfile. SimC is GPL and runs as a separate
# process; we never link against it or vendor its source (.claude/rules/worker.md).
#
# SIMC_REF is the pinned SimC version. SimC stopped tagging releases after
# patch 8.3 (the last tag is release-830-01); current work lives on
# per-expansion branches (`midnight` for WoW 12.x) and ships as nightlies, so
# the pin is a commit SHA on that branch. It is exported as $SIMC_REF in the
# runtime image, becomes `sim_jobs.simc_version`, and is part of the sim cache
# key: bumping it is a deliberate change with an ADR amendment, never a side
# effect.

ARG SIMC_REF=ba1d6a064f8b5357b376d32fe915da63854cf2da

# ─── stage 1: build simc ────────────────────────────────────────────────────
FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171 AS simc-build
ARG SIMC_REF
# Parallel compile jobs. SimC's class modules need roughly 1.5 GB each to
# compile, so keep this at or below (VM memory / 2 GB). Override from compose
# with SIMC_BUILD_JOBS=1 on a small VM.
ARG SIMC_BUILD_JOBS=2

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git g++ cmake make \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src/simc
# Fetch exactly the pinned commit; GitHub serves reachable SHAs to shallow fetches.
RUN git init -q . \
 && git remote add origin https://github.com/simulationcraft/simc.git \
 && git fetch -q --depth 1 origin "${SIMC_REF}" \
 && git checkout -q FETCH_HEAD

# No GUI (Qt) and no networking (Armory import): workers only ever run
# profiles we hand them, and dropping libcurl keeps the runtime image lean.
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_GUI=OFF -DSC_NO_NETWORKING=ON \
 && cmake --build build --parallel "${SIMC_BUILD_JOBS}" --target simc \
 && strip build/simc

# ─── stage 2: runtime ───────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
COPY --from=ghcr.io/astral-sh/uv:0.12.12@sha256:73d2665b478d8fa2de1cf105c6841f8e9cb6b09e568fc7700440c09f8fcd7ac4 /uv /uvx /bin/
ARG SIMC_REF
ENV SIMC_REF=${SIMC_REF} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=simc-build /src/simc/build/simc /usr/local/bin/simc
COPY --chmod=0755 worker/entrypoint.sh /usr/local/bin/bronze-worker

RUN useradd --system --create-home --uid 1000 --user-group worker
USER worker
WORKDIR /home/worker

# The entrypoint writes the marker only after SimC actually ran (see entrypoint.sh).
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
  CMD ["test", "-f", "/tmp/bronze-simc-ok"]
ENTRYPOINT ["/usr/local/bin/bronze-worker"]
