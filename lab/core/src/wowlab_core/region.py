"""region: the client's region, from the `portal` CVar (M21-02).

A Forever character has no realm: the game names it by region, first name
and second name (docs/GLOSSARY.md, "Region (portal)" and "Second name").
The region is the `portal` CVar in the flavor's `WTF/Config.wtf`, read
through the WTF reader (`wtfconfig.read_config`, read-only, L1): the last
`SET portal` line in the file, its name compared without ASCII case.

`region` is one of `us`, `eu`, `kr`, `tw`, the value with its ASCII case
folded (the launch client writes `"US"`, the beta wrote `"test"`; real
fixtures `macos-70888` and `macos`). Anything else (`test`, an empty value,
a value with a space, a control byte or a byte that is not ASCII, a region
this list does not name) is no region: `region` is None and `absent` says
why in the player's words, quoting the value only when it is a short run
of letters, digits, `-` and `_`. No file, no `portal` line, or a file that
cannot be read is no region too, with its reason. Nothing is written.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, model_validator

from wowlab_core import wtfconfig
from wowlab_core.errors import WowlabError

Region = Literal["us", "eu", "kr", "tw"]
REGIONS: tuple[Region, ...] = get_args(Region)

PORTAL_CVAR = "portal"
# The machine-wide config file, relative to the flavor folder.
CONFIG_PARTS: tuple[str, ...] = ("WTF", "Config.wtf")
CONFIG_REL = "/".join(CONFIG_PARTS)

# A value short and plain enough to quote back in a reason.
_QUOTABLE = re.compile(r"[A-Za-z0-9_-]{1,16}")
_ASCII_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


class RegionReading(BaseModel):
    """The flavor's region (`region`, None when there is none) and, when
    there is none, why (`absent`). Exactly one of the two is set."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    region: Region | None
    absent: str | None

    @model_validator(mode="after")
    def _one_of(self) -> RegionReading:
        if (self.region is None) == (self.absent is None):
            raise ValueError("exactly one of region and absent is set")
        return self


def region_of(portal: str | None) -> Region | None:
    """The region a `portal` value names, or None. Only the ASCII case is
    folded; nothing is trimmed."""
    if portal is None:
        return None
    folded = portal.translate(_ASCII_FOLD)
    for region in REGIONS:
        if folded == region:
            return region
    return None


def _not_a_region(value: str) -> str:
    if value == "":
        return f"the {PORTAL_CVAR} setting in {CONFIG_REL} is empty"
    if _QUOTABLE.fullmatch(value):
        return f'the {PORTAL_CVAR} setting in {CONFIG_REL} is "{value}", not one of ' + ", ".join(
            REGIONS
        )
    return f"the {PORTAL_CVAR} setting in {CONFIG_REL} is not one of " + ", ".join(REGIONS)


def from_config(doc: wtfconfig.ConfigDocument) -> RegionReading:
    """The region a parsed `Config.wtf` names."""
    cvar = doc.get(PORTAL_CVAR)
    if cvar is None:
        return RegionReading(region=None, absent=f"{CONFIG_REL} has no {PORTAL_CVAR} setting")
    region = region_of(cvar.value)
    if region is None:
        return RegionReading(region=None, absent=_not_a_region(cvar.value))
    return RegionReading(region=region, absent=None)


def read(flavor_path: Path | str, *, root: Path | str | None = None) -> RegionReading:
    """The region of the flavor folder at `flavor_path`, from its
    `WTF/Config.wtf`. With `root` (the install root), the file is opened
    from there one folder at a time, so a link on the way is refused
    (`wtfconfig.read_config`). Never raises for a missing or unreadable
    file: the reading says why there is no region."""
    path = Path(flavor_path).joinpath(*CONFIG_PARTS)
    try:
        doc = wtfconfig.read_config(path, root=root)
    except FileNotFoundError:
        return RegionReading(region=None, absent=f"there is no {CONFIG_REL}")
    except wtfconfig.WtfLimitError:
        return RegionReading(
            region=None, absent=f"{CONFIG_REL} is larger than wowlab reads, so it was not read"
        )
    except (OSError, WowlabError, ValueError) as exc:
        # The OS's words when it gave some; never a class name (player copy).
        why = f" ({exc.strerror})" if isinstance(exc, OSError) and exc.strerror else ""
        return RegionReading(region=None, absent=f"{CONFIG_REL} could not be read{why}")
    return from_config(doc)
