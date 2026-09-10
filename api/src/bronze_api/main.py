"""FastAPI application entry point.

Routers are added per milestone under `bronze_api.routers`. The only endpoint
that exists before M1 is `/health`, which the local stack and CI use as a
readiness probe.
"""

from fastapi import FastAPI

from bronze_api import __version__
from bronze_api.config import get_settings

app = FastAPI(title="Bronze API", version=__version__)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness and version probe."""
    return {"status": "ok", "version": __version__, "env": get_settings().env}
