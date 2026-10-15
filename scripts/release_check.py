"""Release checks for `.github/workflows/release.yml` (M13-03, ADR-0031).

    python scripts/release_check.py version --tag v0.1.0 [--pyproject PATH] [--output PATH]
    python scripts/release_check.py dist --dir DIST --version 0.1.0
    python scripts/release_check.py notes --tag v0.1.0 --version 0.1.0 \\
        --repository OWNER/NAME --template PATH --out PATH

`version` fails unless the tag is exactly `v` plus the version of
`wowlab-core` in `lab/core/pyproject.toml` (docs/LAB_PLAN.md §15.3, E5). The
version has to be a PEP 440 public version already in canonical form
(`0.1.0`, `0.2.0rc1`, `1.0.0.post1`, `0.3.0.dev2`; no epoch, no local
label), so that the tag, the wheel's file name and the version the wheel
reports are one string. A version with a pre-release or development segment
is published as a GitHub pre-release. With `--output` (the job's
`$GITHUB_OUTPUT`) it appends `version`, `prerelease`, `wheel` and `sdist`.

`dist` fails unless the directory holds exactly the wheel and the sdist that
version names, the metadata inside each states that name and version, both
carry the same non-empty LICENSE (the wheel's with a `License-File: LICENSE`
line), and the sdist holds no test suite. Nothing is extracted; named members
are read.

`notes` fills the release-notes template: `{{TAG}}`, `{{VERSION}}` and
`{{INSTALL}}`, the one `uv tool install` line the README gives
(`install_line`). It fails if a placeholder is left.

Standard library only. Reads the files it is given; `notes` writes `--out`
and `version` appends to `--output`, nothing else. Exit 0 when the check
holds, 1 when it does not (one line on stderr), 2 on a usage error.
"""

from __future__ import annotations

import argparse
import email.parser
import re
import sys
import tarfile
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "lab" / "core" / "pyproject.toml"

PROJECT = "wowlab-core"
DIST_NAME = "wowlab_core"  # the project name as wheel and sdist file names spell it
INSTALL_PYTHON = "3.12"  # the interpreter CI runs the suite on

_NUM = r"(?:0|[1-9][0-9]*)"
CANONICAL = re.compile(
    rf"(?P<release>{_NUM}(?:\.{_NUM})*)"
    rf"(?:(?P<pre>a|b|rc){_NUM})?"
    rf"(?:\.post{_NUM})?"
    rf"(?:\.dev(?P<dev>{_NUM}))?"
)
"""A PEP 440 public version in canonical form, without an epoch."""

REPOSITORY = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+")
PLACEHOLDER = re.compile(r"\{\{[A-Z_]+\}\}")


class ReleaseError(Exception):
    """The check does not hold; the message says why in one line."""


@dataclass(frozen=True)
class Release:
    version: str
    prerelease: bool

    @property
    def tag(self) -> str:
        return f"v{self.version}"

    @property
    def wheel(self) -> str:
        return f"{DIST_NAME}-{self.version}-py3-none-any.whl"

    @property
    def sdist(self) -> str:
        return f"{DIST_NAME}-{self.version}.tar.gz"


def parse_version(version: str) -> Release:
    """`version` as a release, or ReleaseError when it is not canonical PEP 440."""
    m = CANONICAL.fullmatch(version)
    if m is None:
        raise ReleaseError(
            f"package version {version!r} is not a canonical PEP 440 public version "
            "(examples: 0.1.0, 0.2.0rc1, 0.3.0.dev2; no epoch, no local label)"
        )
    return Release(version=version, prerelease=m["pre"] is not None or m["dev"] is not None)


def project_version(pyproject: Path) -> str:
    """The static `project.version` of `wowlab-core` in `pyproject`."""
    with pyproject.open("rb") as fh:
        data = tomllib.load(fh)
    project = data.get("project")
    if not isinstance(project, dict) or project.get("name") != PROJECT:
        raise ReleaseError(f"{pyproject} does not declare the project {PROJECT}")
    version = project.get("version")
    if not isinstance(version, str):
        raise ReleaseError(f"{pyproject} has no static project.version")
    return version


def check_tag(tag: str, pyproject: Path = PYPROJECT) -> Release:
    """The release `tag` names, or ReleaseError unless it is exactly `v` + the version."""
    release = parse_version(project_version(pyproject))
    if tag != release.tag:
        raise ReleaseError(
            f"tag {tag!r} is not {release.tag!r}, the version of {PROJECT} in {pyproject.name}; "
            "nothing is published"
        )
    return release


def _metadata(text: bytes, where: str) -> tuple[str, str, list[str]]:
    msg = email.parser.BytesHeaderParser().parsebytes(text)
    name, version = msg.get("Name"), msg.get("Version")
    if name is None or version is None:
        raise ReleaseError(f"{where} states no Name or no Version")
    return str(name), str(version), [str(f) for f in msg.get_all("License-File", [])]


def _wheel_member(whl: zipfile.ZipFile, wheel: str, member: str) -> bytes:
    try:
        return whl.read(member)
    except KeyError:
        raise ReleaseError(f"{wheel} has no {member}") from None


def _sdist_member(sdist: tarfile.TarFile, name: str, member: str) -> bytes:
    try:
        info = sdist.getmember(member)
    except KeyError:
        raise ReleaseError(f"{name} has no {member}") from None
    fh = sdist.extractfile(info) if info.isfile() else None
    if fh is None:
        raise ReleaseError(f"{name}:{member} is not a regular file")
    with fh:
        return fh.read()


def check_dist(dist: Path, version: str) -> Release:
    """ReleaseError unless `dist` holds exactly the wheel and sdist of `version`.

    Both state that name and version, both carry the same non-empty LICENSE
    (the wheel as `dist-info/licenses/LICENSE` with a `License-File: LICENSE`
    line), and the sdist holds no test suite.
    """
    release = parse_version(version)
    found = sorted(p.name for p in dist.iterdir())
    expected = sorted([release.wheel, release.sdist])
    if found != expected:
        raise ReleaseError(f"{dist} holds {found}, not {expected}")

    info = f"{DIST_NAME}-{version}.dist-info"
    with zipfile.ZipFile(dist / release.wheel) as whl:
        meta = _wheel_member(whl, release.wheel, f"{info}/METADATA")
        wheel_license = _wheel_member(whl, release.wheel, f"{info}/licenses/LICENSE")
    name, stated, license_files = _metadata(meta, f"{release.wheel}:{info}/METADATA")
    if (name, stated) != (PROJECT, version):
        raise ReleaseError(f"{release.wheel} states {(name, stated)}, not {(PROJECT, version)}")
    if license_files != ["LICENSE"]:
        raise ReleaseError(f"{release.wheel} states License-File {license_files}, not ['LICENSE']")

    top = f"{DIST_NAME}-{version}"
    with tarfile.open(dist / release.sdist, "r:gz") as sdist:
        pkg_info = _sdist_member(sdist, release.sdist, f"{top}/PKG-INFO")
        sdist_license = _sdist_member(sdist, release.sdist, f"{top}/LICENSE")
        tests = [
            m for m in sdist.getnames() if m == f"{top}/tests" or m.startswith(f"{top}/tests/")
        ]
    name, stated, _ = _metadata(pkg_info, f"{release.sdist}:{top}/PKG-INFO")
    if (name, stated) != (PROJECT, version):
        raise ReleaseError(f"{release.sdist} states {(name, stated)}, not {(PROJECT, version)}")
    if not wheel_license.strip() or wheel_license != sdist_license:
        raise ReleaseError(f"{release.wheel} and {release.sdist} do not carry the same LICENSE")
    if tests:
        raise ReleaseError(f"{release.sdist} holds the test suite ({len(tests)} entries)")
    return release


def install_line(repository: str, release: Release) -> str:
    """The one `uv tool install` line that installs `release`'s wheel from its GitHub Release."""
    if REPOSITORY.fullmatch(repository) is None:
        raise ReleaseError(f"{repository!r} is not an OWNER/NAME repository")
    url = f"https://github.com/{repository}/releases/download/{release.tag}/{release.wheel}"
    return f"uv tool install --python {INSTALL_PYTHON} {url}"


def render_notes(template: str, repository: str, release: Release) -> str:
    """`template` with its placeholders filled; ReleaseError if any is left."""
    text = (
        template.replace("{{TAG}}", release.tag)
        .replace("{{VERSION}}", release.version)
        .replace("{{INSTALL}}", install_line(repository, release))
    )
    left = sorted(set(PLACEHOLDER.findall(text)))
    if left:
        raise ReleaseError(f"the release-notes template has unknown placeholders: {left}")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("version", help="the tag is exactly v + the package version")
    v.add_argument("--tag", required=True)
    v.add_argument("--pyproject", type=Path, default=PYPROJECT)
    v.add_argument("--output", type=Path, help="append key=value lines ($GITHUB_OUTPUT)")
    d = sub.add_parser("dist", help="the built files are the wheel and sdist of the version")
    d.add_argument("--dir", type=Path, required=True)
    d.add_argument("--version", required=True)
    n = sub.add_parser("notes", help="fill the release-notes template")
    n.add_argument("--tag", required=True)
    n.add_argument("--version", required=True)
    n.add_argument("--repository", required=True)
    n.add_argument("--template", type=Path, required=True)
    n.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "version":
            release = check_tag(args.tag, args.pyproject)
            lines = [
                f"version={release.version}",
                f"prerelease={'true' if release.prerelease else 'false'}",
                f"wheel={release.wheel}",
                f"sdist={release.sdist}",
            ]
            print(" ".join(lines))
            if args.output is not None:
                with args.output.open("a", encoding="utf-8") as fh:
                    fh.write("".join(f"{line}\n" for line in lines))
        elif args.command == "dist":
            release = check_dist(args.dir, args.version)
            print(f"{release.wheel} and {release.sdist} state {PROJECT} {release.version}")
        else:
            release = parse_version(args.version)
            if args.tag != release.tag:
                raise ReleaseError(f"tag {args.tag!r} is not {release.tag!r}")
            text = render_notes(args.template.read_text(encoding="utf-8"), args.repository, release)
            args.out.write_text(text, encoding="utf-8")
            print(install_line(args.repository, release))
    except (
        ReleaseError,
        OSError,
        tomllib.TOMLDecodeError,
        zipfile.BadZipFile,
        tarfile.TarError,
    ) as exc:
        print(f"release check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
