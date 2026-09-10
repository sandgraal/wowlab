"""Structural checks for the local stack (M0-03).

`make up` does not run in CI (a SimC compile per run is not a CI budget) and
may not run on a contributor machine without the Compose plugin, so this pins
down the properties the ticket needs from `docker-compose.yml`, the two
Dockerfiles, `.env.example` and the Makefile. It complements `make up`; it
does not replace it.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO / "docker-compose.yml"
ENV_EXAMPLE = REPO / ".env.example"
MAKEFILE = REPO / "Makefile"
DOCKERFILES = {"api": REPO / "api" / "Dockerfile", "worker": REPO / "worker" / "Dockerfile"}
ENTRYPOINT = REPO / "worker" / "entrypoint.sh"
SETUP_DOC = REPO / "docs" / "SETUP.md"

# One variable, defaulted in one place, read by every URL and by the container.
PASSWORD = "${POSTGRES_PASSWORD:-bronze}"
CONTAINER_DATABASE_URL = f"postgresql+psycopg://bronze:{PASSWORD}@postgres:5432/bronze"

DIGEST = r"@sha256:[0-9a-f]{64}"
# (service, Makefile variable, container port) — the per-worktree port block.
PORT_BLOCK = [
    ("postgres", "DB_PORT", 5432),
    ("redis", "REDIS_PORT", 6379),
    ("api", "API_PORT", 8000),
]


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    loaded = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


@pytest.fixture(scope="module")
def services(compose: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = compose["services"]
    return result


# ─── the four services ──────────────────────────────────────────────────────


def test_stack_has_exactly_the_four_services(services: dict[str, Any]) -> None:
    assert set(services) == {"postgres", "redis", "api", "worker"}


def test_no_obsolete_top_level_version_key(compose: dict[str, Any]) -> None:
    assert "version" not in compose


def test_postgres_is_16_pinned_by_digest(services: dict[str, Any]) -> None:
    assert re.fullmatch(rf"postgres:16(\.\d+)*{DIGEST}", services["postgres"]["image"])


def test_redis_is_pinned_by_digest(services: dict[str, Any]) -> None:
    assert re.fullmatch(rf"redis:\S+{DIGEST}", services["redis"]["image"])


def test_api_and_worker_build_from_their_dockerfiles(services: dict[str, Any]) -> None:
    for name, dockerfile in DOCKERFILES.items():
        build = services[name]["build"]
        assert build["context"] == ".", "root context: the uv lockfile lives there"
        assert (REPO / build["dockerfile"]).resolve() == dockerfile
        assert dockerfile.is_file()


# ─── per-worktree isolation (two checkouts run `make up` at once) ───────────


@pytest.mark.parametrize(("service", "variable", "container_port"), PORT_BLOCK)
def test_host_ports_come_from_the_makefile_variables(
    services: dict[str, Any], service: str, variable: str, container_port: int
) -> None:
    """Loopback only: a bare host port binds 0.0.0.0, and Docker's iptables rules
    bypass ufw/firewalld on Linux, which would expose Postgres, Redis (no auth in
    the stock image) and the API to the LAN."""
    assert services[service]["ports"] == [
        f"127.0.0.1:${{{variable}:-{container_port}}}:{container_port}"
    ]


def test_worker_publishes_no_host_port(services: dict[str, Any]) -> None:
    assert "ports" not in services["worker"]


def test_no_service_fixes_a_container_name(services: dict[str, Any]) -> None:
    offenders = [name for name, svc in services.items() if "container_name" in svc]
    assert not offenders, f"container_name collides across worktrees: {offenders}"


def test_postgres_data_lives_in_a_named_volume(
    compose: dict[str, Any], services: dict[str, Any]
) -> None:
    assert services["postgres"]["volumes"] == ["pgdata:/var/lib/postgresql/data"]
    assert "pgdata" in compose["volumes"], "named volumes are prefixed per project"


def test_makefile_exports_the_variables_compose_reads() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    for variable in ("COMPOSE_PROJECT_NAME", "DB_PORT", "REDIS_PORT", "API_PORT"):
        assert re.search(rf"^export {variable}\s+\?=", makefile, re.M), variable
    assert re.search(r"^export POSTGRES_PASSWORD\s+\?= bronze$", makefile, re.M)
    assert re.search(
        r"^export DATABASE_URL\s+\?=.*bronze:\$\(POSTGRES_PASSWORD\)@localhost:\$\(DB_PORT\)",
        makefile,
        re.M,
    )
    assert re.search(r"^export REDIS_URL\s+\?=.*\$\(REDIS_PORT\)", makefile, re.M)


# ─── `make up` reaches a healthy state ──────────────────────────────────────


def test_make_up_waits_for_health() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    assert re.search(r"^up:.*\n(\t.*\n)*\tdocker compose up .*--wait", makefile, re.M)


def test_stock_images_declare_a_healthcheck(services: dict[str, Any]) -> None:
    for name in ("postgres", "redis"):
        assert services[name]["healthcheck"]["test"], name


def test_built_images_declare_a_healthcheck() -> None:
    for name, dockerfile in DOCKERFILES.items():
        assert re.search(r"^HEALTHCHECK ", dockerfile.read_text(encoding="utf-8"), re.M), name


def test_api_and_worker_wait_for_healthy_backing_services(services: dict[str, Any]) -> None:
    for name in ("api", "worker"):
        depends_on = services[name]["depends_on"]
        assert depends_on["postgres"]["condition"] == "service_healthy", name
        assert depends_on["redis"]["condition"] == "service_healthy", name


def test_api_and_worker_address_services_by_compose_name(services: dict[str, Any]) -> None:
    for name in ("api", "worker"):
        env = services[name]["environment"]
        assert env["DATABASE_URL"] == CONTAINER_DATABASE_URL
        assert env["REDIS_URL"] == "redis://redis:6379/0"


def test_postgres_credentials_match_the_urls(services: dict[str, Any]) -> None:
    """The password is parameterised once, so a copy-pasted deployment cannot
    inherit the literal default; the literal never appears outside the `:-`."""
    env = services["postgres"]["environment"]
    assert (env["POSTGRES_USER"], env["POSTGRES_PASSWORD"], env["POSTGRES_DB"]) == (
        "bronze",
        PASSWORD,
        "bronze",
    )
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "bronze:bronze@" not in text, "hard-coded password in a URL"
    assert text.count(PASSWORD) == 3, "postgres env + api URL + worker URL"


def test_env_file_is_optional_so_a_fresh_clone_still_starts(services: dict[str, Any]) -> None:
    assert services["api"]["env_file"] == [{"path": ".env", "required": False}]


def test_worker_does_not_read_env_credentials(services: dict[str, Any]) -> None:
    """No use for BNET_* / WCL_* until M2, so the worker does not receive them."""
    assert "env_file" not in services["worker"]


# ─── Dockerfiles ────────────────────────────────────────────────────────────


def test_dockerfiles_pin_every_image_by_digest() -> None:
    for name, dockerfile in DOCKERFILES.items():
        text = dockerfile.read_text(encoding="utf-8")
        froms = re.findall(r"^FROM\s+(\S+)", text, re.M)
        assert froms, name
        external = froms + [ref for ref in re.findall(r"--from=(\S+)", text) if "/" in ref]
        unpinned = [ref for ref in external if not re.search(DIGEST, ref)]
        assert not unpinned, f"{name}: {unpinned}"


def test_dockerfiles_run_as_a_non_root_user() -> None:
    for name, dockerfile in DOCKERFILES.items():
        text = dockerfile.read_text(encoding="utf-8")
        users = re.findall(r"^USER\s+(\S+)", text, re.M)
        assert users and users[-1] != "root", name


def test_uv_in_images_matches_the_ci_toolchain_pin() -> None:
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    match = re.search(r'UV_VERSION:\s*"([\d.]+)"', ci)
    assert match
    for name, dockerfile in DOCKERFILES.items():
        text = dockerfile.read_text(encoding="utf-8")
        assert f"ghcr.io/astral-sh/uv:{match.group(1)}@sha256:" in text, name


def test_api_image_installs_from_the_frozen_lockfile() -> None:
    text = DOCKERFILES["api"].read_text(encoding="utf-8")
    assert "COPY pyproject.toml uv.lock" in text
    assert re.search(r"uv sync --frozen --no-dev\b", text)


def test_worker_pins_simc_to_a_full_commit_sha() -> None:
    text = DOCKERFILES["worker"].read_text(encoding="utf-8")
    assert re.search(r"^ARG SIMC_REF=[0-9a-f]{40}$", text, re.M)
    assert re.search(r"^ENV SIMC_REF=\$\{SIMC_REF\}", text, re.M), (
        "exported for sim_jobs.simc_version"
    )
    assert "simulationcraft/simc" in text


def test_worker_checks_the_pin_out_onto_a_named_branch() -> None:
    """SimC's CMake compiles `git rev-parse --abbrev-ref HEAD` into the binary
    (version banner, json2 `git_branch`); a detached FETCH_HEAD embeds `HEAD`."""
    text = DOCKERFILES["worker"].read_text(encoding="utf-8")
    assert re.search(r"^ARG SIMC_BRANCH=[A-Za-z0-9._-]+$", text, re.M)
    assert 'git checkout -q -b "${SIMC_BRANCH}" FETCH_HEAD' in text
    assert not re.search(r"git checkout -q FETCH_HEAD", text)
    assert re.search(r"^\s*SIMC_BRANCH=\$\{SIMC_BRANCH\}", text, re.M), "exported with SIMC_REF"


def test_worker_builds_only_the_cli() -> None:
    text = DOCKERFILES["worker"].read_text(encoding="utf-8")
    configure = re.search(r"cmake -S \. -B build (.*?)\\?\n", text)
    assert configure
    for flag in ("-DBUILD_GUI=OFF", "-DSC_NO_NETWORKING=ON", "-DBUILD_TESTING=OFF"):
        assert flag in configure.group(1), flag


def test_worker_entrypoint_is_copied_and_present() -> None:
    assert ENTRYPOINT.is_file()
    assert "worker/entrypoint.sh" in DOCKERFILES["worker"].read_text(encoding="utf-8")


def test_worker_health_requires_spell_data_not_just_exit_zero() -> None:
    """`spell_query` that matches nothing still exits 0; the marker must depend
    on a real row (spell 133 is Fireball at the pinned ref)."""
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert "spell_query=spell.id=133" in text
    grep = text.index('grep -q Fireball "$SMOKE_LOG"')
    marker = text.index('touch "$MARKER"')
    assert grep < marker, "data check must precede the healthy marker"
    assert 'head -n 1 "$SMOKE_LOG"' in text, "SimC identity banner echoed to the logs"


def test_setup_doc_consumes_all_of_make_env() -> None:
    """`make env` prints two lines; `head -1` closes the pipe early and make
    reports SIGPIPE (`Broken pipe: 13`) on every run."""
    text = SETUP_DOC.read_text(encoding="utf-8")
    assert "make -s env | head" not in text
    assert re.search(r"make -s env \| sed -n 's/\.\*API_PORT=", text)


# ─── `make env` (run by every `make up`) never echoes a password ────────────

# Scrubbed before `make` runs: the CI test job exports DATABASE_URL itself.
_URL_VARIABLES = ("POSTGRES_PASSWORD", "DATABASE_URL", "REDIS_URL")


def _make_env(**overrides: str) -> str:
    env = {key: value for key, value in os.environ.items() if key not in _URL_VARIABLES}
    env.update(overrides)
    completed = subprocess.run(
        ["make", "-s", "env"], cwd=REPO, env=env, capture_output=True, text=True, check=True
    )
    return completed.stdout


def test_make_env_masks_the_postgres_password() -> None:
    """The exported DATABASE_URL keeps the password for host tools; only the
    echo is masked, anchored on `:<password>@` so `bronze` survives as the
    user and database name."""
    out = _make_env(POSTGRES_PASSWORD="fakepw-xyz")
    assert "fakepw-xyz" not in out
    assert re.search(r"DATABASE_URL=postgresql\+psycopg://bronze:\*\*\*@localhost:\d+/bronze ", out)
    assert len(out.splitlines()) == 2, "SETUP.md's sed consumes exactly two lines"


def test_make_env_masks_a_foreign_database_url_password() -> None:
    """A DATABASE_URL exported by the shell does not carry POSTGRES_PASSWORD,
    so the mask must not depend on knowing the password."""
    out = _make_env(DATABASE_URL="postgresql+psycopg://alice:s3cret-abc@db.example:5432/prod")
    assert "s3cret-abc" not in out
    assert "DATABASE_URL=postgresql+psycopg://alice:***@db.example:5432/prod " in out


def test_make_env_masks_a_redis_password() -> None:
    out = _make_env(REDIS_URL="redis://:redispw-abc@cache.example:6379/0")
    assert "redispw-abc" not in out
    assert out.rstrip().endswith("REDIS_URL=redis://:***@cache.example:6379/0")


def test_make_env_leaves_a_passwordless_redis_url_alone() -> None:
    out = _make_env()
    assert re.search(r" REDIS_URL=redis://localhost:\d+/0$", out.rstrip())


def test_dockerignore_allow_lists_the_context() -> None:
    lines = [
        line.strip()
        for line in (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines[0] == "*", "deny everything first; worktrees under .claude/ are huge"
    allowed = {line[1:] for line in lines if line.startswith("!")}
    assert {
        "pyproject.toml",
        "uv.lock",
        "api/pyproject.toml",
        "api/src",
        "worker/entrypoint.sh",
    } <= allowed
    assert not {".git", ".venv", ".env", ".claude"} & allowed


# ─── .env.example documents every variable ──────────────────────────────────


def test_env_example_covers_every_setting() -> None:
    from bronze_api.config import Settings

    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for field in Settings.model_fields:
        assert re.search(rf"^#? ?{field.upper()}=", text, re.M), field


def test_env_example_covers_the_compose_variables() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for variable in (
        "COMPOSE_PROJECT_NAME",
        "DB_PORT",
        "REDIS_PORT",
        "API_PORT",
        "POSTGRES_PASSWORD",
        "SIMC_BUILD_JOBS",
    ):
        assert re.search(rf"^#? ?{variable}=", text, re.M), variable


def test_env_example_explains_every_variable() -> None:
    """Each blank-line-separated block that assigns a variable also comments on it."""
    assignment = re.compile(r"^#? ?[A-Z][A-Z0-9_]*=")
    for block in ENV_EXAMPLE.read_text(encoding="utf-8").split("\n\n"):
        lines = [line for line in block.splitlines() if line.strip()]
        if not any(assignment.match(line) for line in lines):
            continue
        prose = [line for line in lines if line.startswith("#") and not assignment.match(line)]
        assert prose, f"undocumented block:\n{block}"


def test_env_example_holds_no_secret_values() -> None:
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if re.match(r"^[A-Z0-9_]*(SECRET|KEY|TOKEN|PASSWORD)[A-Z0-9_]*=", line):
            assert line.endswith("="), line
