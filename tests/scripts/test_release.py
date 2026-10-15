"""Grades for the release pipeline (M13-03, ADR-0031).

Two halves. The workflow tests read `.github/workflows/release.yml` as data
and pin what `security-reviewer` signs off on: the trigger, the token's
reach (read-only everywhere, `contents: write` on the release job only),
action commits pinned, no secret but the job token, and the version check
standing between a tag and a release. The script tests grade
`scripts/release_check.py`, which does the checking.

Every archive and `pyproject.toml` built here is `constructed` (L8, boundary
cases): a wheel or sdist holding only the one metadata member the check
reads, in `tmp_path`. The real `lab/core/pyproject.toml` and the real
release-notes template are read as they are.
"""

from __future__ import annotations

import io
import re
import tarfile
import tomllib
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_check_wiki import _load

rc = _load("release_check")

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
TEMPLATE = ROOT / ".github" / "release-notes.md"
PINNED = re.compile(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}")

# The line the README gives (M13-10 copies it byte for byte) for v0.1.0.
INSTALL_V010 = (
    "uv tool install --python 3.12 "
    "https://github.com/sandgraal/wowlab/releases/download/v0.1.0/"
    "wowlab_core-0.1.0-py3-none-any.whl"
)


# ─── the workflow ─────────────────────────────────────────────────────────────


def _workflow() -> dict[str, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _jobs() -> dict[str, Any]:
    jobs = _workflow()["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def _steps() -> list[tuple[str, dict[str, Any]]]:
    return [(name, step) for name, job in _jobs().items() for step in job["steps"]]


def test_the_only_trigger_is_a_pushed_v_tag() -> None:
    data = _workflow()
    triggers = data.get("on", data.get(True))  # YAML 1.1 reads `on` as true
    assert triggers == {"push": {"tags": ["v*"]}}


def test_the_token_is_read_only_except_on_the_release_job() -> None:
    assert _workflow()["permissions"] == {"contents": "read"}
    jobs = _jobs()
    assert set(jobs) == {"version", "test", "build", "release"}
    assert {n: j.get("permissions") for n, j in jobs.items()} == {
        "version": None,
        "test": None,
        "build": None,
        "release": {"contents": "write"},
    }


def test_every_action_is_pinned_to_a_commit() -> None:
    uses = [step["uses"] for _, step in _steps() if "uses" in step]
    assert uses
    assert all(PINNED.fullmatch(u) for u in uses), uses
    for _, step in _steps():
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False
        if step.get("uses", "").startswith("astral-sh/setup-uv@"):
            # A cache written by another ref never reaches a release build.
            assert step["with"]["enable-cache"] is False


def test_no_secret_but_the_job_token_and_only_where_it_publishes() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in text
    holders = [
        (job, step.get("name"))
        for job, step in _steps()
        if any("github.token" in str(v) for v in step.get("env", {}).values())
    ]
    assert holders == [("release", "Create the release")]
    for name, job in _jobs().items():
        assert "github.token" not in str(job.get("env", {})), name


def test_no_expression_is_spliced_into_a_shell_script() -> None:
    # Tag names and job outputs reach `run` through `env`, never as `${{ }}`.
    for job, step in _steps():
        assert "${{" not in step.get("run", ""), (job, step.get("name"))


def test_the_release_job_runs_no_repository_code() -> None:
    release = _jobs()["release"]
    assert release["needs"] == ["version", "test", "build"]
    assert "if" not in release  # the implicit success(): any failure publishes nothing
    uses = [s["uses"].split("@")[0] for s in release["steps"] if "uses" in s]
    assert uses == ["actions/download-artifact"]
    runs = " ".join(s.get("run", "") for s in release["steps"])
    assert not re.search(r"\buv\b|\bpython3?\b|\bscripts/|\bgit\b|\bmake\b", runs)


def test_the_release_publishes_the_three_checked_files() -> None:
    release = _jobs()["release"]
    steps = release["steps"]
    verify = next(s for s in steps if s.get("name") == "The files are the ones the build checked")
    assert "sha256sum -c SHA256SUMS" in verify["run"]
    create = next(s for s in steps if s.get("name") == "Create the release")
    run = create["run"]
    assert create["env"] == {"GH_TOKEN": "${{ github.token }}"}
    assert 'gh release create "$TAG"' in run
    assert "--verify-tag" in run
    assert "--notes-file release/notes.md" in run
    assert run.rstrip().endswith('"release/$WHEEL" "release/$SDIST" release/SHA256SUMS')
    assert 'if [ "$PRERELEASE" = "true" ]; then flags+=(--prerelease); fi' in run
    assert release["env"]["PRERELEASE"] == "${{ needs.version.outputs.prerelease }}"
    assert release["env"]["TAG"] == "${{ github.ref_name }}"


def test_the_version_check_gates_every_other_job() -> None:
    jobs = _jobs()
    version = jobs["version"]
    check = next(s for s in version["steps"] if s.get("id") == "check")
    assert check["env"] == {"TAG": "${{ github.ref_name }}"}
    assert 'scripts/release_check.py version --tag "$TAG" --output "$GITHUB_OUTPUT"' in " ".join(
        check["run"].split()
    )
    for name in ("test", "build"):
        assert jobs[name]["needs"] == "version", name
        assert "if" not in jobs[name], name
    # The tagged commit is on main: a release is built from reviewed code only.
    runs = [s.get("run", "") for s in version["steps"]]
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" origin/main' in runs
    checkout = next(s for s in version["steps"] if "uses" in s)
    assert checkout["with"]["fetch-depth"] == 0


def test_the_build_checks_the_files_sums_them_and_runs_the_installed_tool() -> None:
    steps = _jobs()["build"]["steps"]
    runs = [" ".join(s.get("run", "").split()) for s in steps]
    order = [
        "uv build --package wowlab-core --no-sources --no-create-gitignore "
        "--build-constraints lab/core/build-constraints.txt --require-hashes "
        '--out-dir "$RUNNER_TEMP/dist"',
        'scripts/release_check.py dist --dir "$RUNNER_TEMP/dist" --version "$VERSION"',
        'sha256sum -- "$WHEEL" "$SDIST" > SHA256SUMS',
        'uv tool install --python "$PYTHON_VERSION" "release/$WHEEL"',
        'scripts/release_check.py notes --tag "$TAG" --version "$VERSION"',
    ]
    at = [next(i for i, r in enumerate(runs) if needle in r) for needle in order]
    assert at == sorted(at)
    joined = " ".join(runs)
    assert '"$UV_TOOL_BIN_DIR/wowlab" --version)" = "wowlab $VERSION"' in joined
    assert '"$UV_TOOL_BIN_DIR/wowlab" doctor --offline --json' in joined
    upload = next(s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["with"]["path"] == "release/"


def test_the_build_backend_is_pinned_with_hashes() -> None:
    text = (ROOT / "lab" / "core" / "build-constraints.txt").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line and not line.startswith("#")]
    # One requirement per block: its first line starts at column 0, its hashes are indented.
    reqs = re.split(r"\n(?=\S)", "\n".join(lines))
    pinned = {b.split("==")[0]: b for b in reqs}
    assert "hatchling" in pinned
    for name, block in pinned.items():
        assert re.match(rf"{re.escape(name)}==[\w.]+ \\\n", block), block
        assert re.search(r"--hash=sha256:[0-9a-f]{64}", block), block
    backend = tomllib.loads((ROOT / "lab" / "core" / "pyproject.toml").read_text("utf-8"))
    assert backend["build-system"]["requires"] == ["hatchling"]


def test_the_notes_template_has_a_section_for_json_model_changes() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "\n## Changes to `--json` output\n" in text
    assert "ADR-0031" in text
    assert "{{INSTALL}}" in text
    assert "sha256sum -c SHA256SUMS" in text


# ─── scripts/release_check.py ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("version", "prerelease"),
    [
        ("0.1.0", False),
        ("1.0", False),
        ("1.0.0.post1", False),
        ("0.2.0a1", True),
        ("0.2.0b3", True),
        ("0.2.0rc1", True),
        ("0.3.0.dev2", True),
        ("0.2.0rc1.post1.dev0", True),
    ],
)
def test_a_canonical_version_is_accepted_and_its_kind_named(version: str, prerelease: bool) -> None:
    assert rc.parse_version(version) == rc.Release(version=version, prerelease=prerelease)


@pytest.mark.parametrize(
    "version",
    [
        "0.2.0-rc1",
        "0.2.0RC1",
        "0.2.0rc",
        "0.2.0c1",
        "0.2.0.rc1",
        "01.0",
        "1.00",
        "1!1.0",
        "0.1.0+local",
        "v0.1.0",
        " 0.1.0",
        "0.1.0\n",
        "0.1.0.post",
        "",
    ],
)
def test_constructed_a_version_not_in_canonical_form_is_refused(version: str) -> None:
    with pytest.raises(rc.ReleaseError, match="canonical PEP 440"):
        rc.parse_version(version)


def test_the_regex_agrees_with_packaging() -> None:
    packaging_version = pytest.importorskip("packaging.version")
    candidates = [
        "0.1.0",
        "1.0",
        "0.2.0rc1",
        "0.2.0-rc1",
        "0.2.0c1",
        "0.3.0.dev2",
        "1.0.0.post1",
        "1.0.0-1",
        "01.0",
        "1!1.0",
        "0.1.0+local",
        "0.2.0alpha1",
    ]
    for v in candidates:
        parsed = packaging_version.Version(v)
        canonical = str(parsed) == v and parsed.epoch == 0 and parsed.local is None
        try:
            release = rc.parse_version(v)
        except rc.ReleaseError:
            assert not canonical, v
        else:
            assert canonical, v
            assert release.prerelease == parsed.is_prerelease, v


def _pyproject(tmp_path: Path, version: str, name: str = "wowlab-core") -> Path:
    path = tmp_path / "pyproject.toml"
    path.write_text(f'[project]\nname = "{name}"\nversion = "{version}"\n', encoding="utf-8")
    return path


def test_the_tag_for_the_real_package_version() -> None:
    version = rc.project_version(rc.PYPROJECT)
    release = rc.check_tag(f"v{version}")
    assert release.version == version
    assert release.wheel == f"wowlab_core-{version}-py3-none-any.whl"
    assert release.sdist == f"wowlab_core-{version}.tar.gz"


@pytest.mark.parametrize(
    "tag", ["0.1.0", "v0.1", "v0.1.0.0", "V0.1.0", "v0.1.0 ", "v0.1.0-rc1", "v00.1.0", "v0.1.1"]
)
def test_constructed_a_tag_that_is_not_v_plus_the_version_publishes_nothing(
    tmp_path: Path, tag: str
) -> None:
    pyproject = _pyproject(tmp_path, "0.1.0")
    out = tmp_path / "out"
    with pytest.raises(rc.ReleaseError, match="nothing is published"):
        rc.check_tag(tag, pyproject)
    code = rc.main(["version", "--tag", tag, "--pyproject", str(pyproject), "--output", str(out)])
    assert code == 1
    assert not out.exists()


def test_constructed_a_matching_tag_writes_the_job_outputs(tmp_path: Path) -> None:
    out = tmp_path / "github_output"
    out.write_text("earlier=1\n", encoding="utf-8")
    pyproject = _pyproject(tmp_path, "0.2.0rc1")
    code = rc.main(
        ["version", "--tag", "v0.2.0rc1", "--pyproject", str(pyproject), "--output", str(out)]
    )
    assert code == 0
    assert out.read_text(encoding="utf-8") == (
        "earlier=1\n"
        "version=0.2.0rc1\n"
        "prerelease=true\n"
        "wheel=wowlab_core-0.2.0rc1-py3-none-any.whl\n"
        "sdist=wowlab_core-0.2.0rc1.tar.gz\n"
    )


def test_constructed_a_non_canonical_or_foreign_pyproject_fails(tmp_path: Path) -> None:
    with pytest.raises(rc.ReleaseError, match="canonical PEP 440"):
        rc.check_tag("v0.2.0-rc1", _pyproject(tmp_path, "0.2.0-rc1"))
    with pytest.raises(rc.ReleaseError, match="does not declare the project wowlab-core"):
        rc.check_tag("v0.1.0", _pyproject(tmp_path, "0.1.0", name="other"))
    dynamic = tmp_path / "dynamic.toml"
    dynamic.write_text('[project]\nname = "wowlab-core"\ndynamic = ["version"]\n', "utf-8")
    with pytest.raises(rc.ReleaseError, match=r"no static project\.version"):
        rc.check_tag("v0.1.0", dynamic)


def _metadata(name: str, version: str) -> bytes:
    return f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n\nbody\n".encode()


def _dist(
    tmp_path: Path,
    version: str = "0.1.0",
    wheel_version: str | None = None,
    sdist_version: str | None = None,
) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    with zipfile.ZipFile(dist / f"wowlab_core-{version}-py3-none-any.whl", "w") as whl:
        whl.writestr(
            f"wowlab_core-{version}.dist-info/METADATA",
            _metadata("wowlab-core", wheel_version or version),
        )
    with tarfile.open(dist / f"wowlab_core-{version}.tar.gz", "w:gz") as sdist:
        data = _metadata("wowlab-core", sdist_version or version)
        info = tarfile.TarInfo(f"wowlab_core-{version}/PKG-INFO")
        info.size = len(data)
        sdist.addfile(info, io.BytesIO(data))
    return dist


def test_constructed_the_wheel_and_sdist_of_the_version_pass(tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    assert rc.check_dist(dist, "0.1.0") == rc.Release("0.1.0", prerelease=False)
    assert rc.main(["dist", "--dir", str(dist), "--version", "0.1.0"]) == 0


def test_constructed_a_stray_or_missing_file_fails(tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    (dist / "wowlab_core-0.0.9-py3-none-any.whl").write_bytes(b"")
    with pytest.raises(rc.ReleaseError, match="holds"):
        rc.check_dist(dist, "0.1.0")
    (dist / "wowlab_core-0.0.9-py3-none-any.whl").unlink()
    (dist / "wowlab_core-0.1.0.tar.gz").unlink()
    with pytest.raises(rc.ReleaseError, match="holds"):
        rc.check_dist(dist, "0.1.0")


@pytest.mark.parametrize(("wheel", "sdist"), [("0.1.1", None), (None, "0.1.0rc1")])
def test_constructed_metadata_naming_another_version_fails(
    tmp_path: Path, wheel: str | None, sdist: str | None
) -> None:
    dist = _dist(tmp_path, wheel_version=wheel, sdist_version=sdist)
    with pytest.raises(rc.ReleaseError, match="states"):
        rc.check_dist(dist, "0.1.0")
    assert rc.main(["dist", "--dir", str(dist), "--version", "0.1.0"]) == 1


def test_the_install_line_the_readme_gives() -> None:
    release = rc.parse_version("0.1.0")
    assert rc.install_line("sandgraal/wowlab", release) == INSTALL_V010
    with pytest.raises(rc.ReleaseError, match="OWNER/NAME"):
        rc.install_line("sandgraal/wowlab; rm -rf ~", release)


def test_the_real_template_renders_with_the_install_line(tmp_path: Path) -> None:
    out = tmp_path / "notes.md"
    code = rc.main(
        [
            "notes",
            "--tag",
            "v0.1.0",
            "--version",
            "0.1.0",
            "--repository",
            "sandgraal/wowlab",
            "--template",
            str(TEMPLATE),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert f"\n{INSTALL_V010}\n" in text
    assert "{{" not in text
    assert "\n## Changes to `--json` output\n" in text


def test_constructed_an_unknown_placeholder_or_a_wrong_tag_fails(tmp_path: Path) -> None:
    release = rc.parse_version("0.1.0")
    with pytest.raises(rc.ReleaseError, match=r"\{\{DATE\}\}"):
        rc.render_notes("{{VERSION}} {{DATE}}", "sandgraal/wowlab", release)
    out = tmp_path / "notes.md"
    args = ["notes", "--tag", "v0.1.1", "--version", "0.1.0", "--repository", "sandgraal/wowlab"]
    assert rc.main([*args, "--template", str(TEMPLATE), "--out", str(out)]) == 1
    assert not out.exists()
