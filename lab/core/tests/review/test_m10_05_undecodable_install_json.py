# Probe from review of m10/05-install-discovery; reproduces an Install that
# discover() returns without error but that cannot be serialized to JSON.
"""`install._decode` keeps undecodable `.build.info` / `.flavor.info` bytes as
lone surrogates (surrogateescape). Pydantic's JSON serializer refuses lone
surrogates, so `Install.model_dump_json()` raises `PydanticSerializationError`
for an install that discovery accepted and returned. `docs/LAB_PLAN.md` §6.11
gives `wowlab install show` a `--json` flag, and M10-14's acceptance
requires every data command's `--json` output to validate against its
Pydantic model, so a single stray byte would crash the command.

Every input here is constructed (hostile-input case): one byte of the real
macOS `.build.info` fixture is replaced with an invalid UTF-8 byte.
"""

from __future__ import annotations

from pathlib import Path

from wowlab_core.install import Install, read_install

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "macos"
BUILD_INFO = (FIXTURES / ".build.info").read_bytes()
FLAVOR_INFO = (FIXTURES / "forever" / ".flavor.info").read_bytes()


def _install(tmp_path: Path, build_info: bytes, flavor_info: bytes) -> Path:
    root = tmp_path / "World of Warcraft"
    (root / "_classic_beta_").mkdir(parents=True)
    (root / ".build.info").write_bytes(build_info)
    (root / "_classic_beta_" / ".flavor.info").write_bytes(flavor_info)
    return root


def test_positive_control_real_install_dumps_and_loads_json(tmp_path: Path) -> None:
    got = read_install(_install(tmp_path, BUILD_INFO, FLAVOR_INFO))
    assert Install.model_validate_json(got.model_dump_json()) == got


def test_undecodable_build_info_install_still_dumps_json_constructed(tmp_path: Path) -> None:
    data = BUILD_INFO.replace(b"tpr/wow", b"tpr/\xff")
    assert data != BUILD_INFO
    got = read_install(_install(tmp_path, data, FLAVOR_INFO))

    Install.model_validate_json(got.model_dump_json())  # raises today


def test_undecodable_flavor_info_install_still_dumps_json_constructed(tmp_path: Path) -> None:
    got = read_install(
        _install(tmp_path, BUILD_INFO, FLAVOR_INFO.replace(b"beta\n", b"beta\xff\n"))
    )

    Install.model_validate_json(got.model_dump_json())  # raises today
