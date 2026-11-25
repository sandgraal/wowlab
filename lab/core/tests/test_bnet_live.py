"""The owner's check of `wowlab blizzard` against Blizzard's real API (M22-11).

Never run in CI or by an agent (ADR-0012): it asks Blizzard, with the
owner's own API client (M22-09), for one Forever character. Run it by hand
once the client is kept (`wowlab blizzard login`):

    WOWLAB_BNET_LIVE_NAME="First-Second" WOWLAB_BNET_LIVE_REGION=us \\
        uv run pytest -m live lab/core/tests/test_bnet_live.py -q

`WOWLAB_BNET_LIVE_NAME` is a character of level 10 or more (one below has
no profile). It answers the ticket's [verify] items, which the code takes
from community forum notes (threads 59774 and 60334):
- whether Blizzard serves Forever characters at all, under the namespace
  `profile-forever-<region>` and the path `/profile/wow/character/<first>-<second>`
  (`bnet_games.toml`); if the profile is not found, Forever is not served
  there, and the ticket stops (`docs/BACKLOG.md` M22-11);
- whether the profile carries `surname` and `ruleset` and no realm, and in
  what shape `ruleset` comes (it feeds the Raider.IO link);
- whether the guild's address is one wowlab follows (`/data/wow/guild/...`
  on the API host), and whether the portrait's addresses are on
  `render.worldofwarcraft.com`.

The secret is never printed: the test prints the profile's fields only.
Nothing is written but one saved fetch in wowlab's own data folder.
"""

from __future__ import annotations

import os

import pytest

from wowlab_core import bnet


@pytest.mark.live
def test_live_forever_profile_is_served() -> None:
    raw_name = os.environ.get("WOWLAB_BNET_LIVE_NAME", "").strip()
    raw_region = os.environ.get("WOWLAB_BNET_LIVE_REGION", "us").strip()
    if not raw_name:
        pytest.skip("set WOWLAB_BNET_LIVE_NAME to a Forever character of level 10 or more")
    creds = bnet.read_credentials()
    if creds is None:
        pytest.skip("no API client kept; run `wowlab blizzard login` first")
    games = bnet.load_games()
    game = next(g for g in games if g.label == "forever")
    region = bnet.check_region(raw_region)
    name = bnet.parse_name(raw_name)
    profile, raw = bnet.fetch_profile(creds, game, region, name)
    print(profile.model_dump_json(indent=2))
    print("profile keys:", sorted(raw.get("character", {})))
    assert profile.character_state.status == "ok", (
        f"Forever is not served at {game.namespace}: {profile.character_state.detail}"
    )
    c = profile.character
    assert c is not None and c.name and c.level is not None
    print("ruleset as sent:", repr(raw.get("character", {}).get("ruleset")))
    print("surname as sent:", repr(raw.get("character", {}).get("surname")))
    print("realm as sent:", repr(raw.get("character", {}).get("realm")))
    assert profile.equipment_state.status == "ok", profile.equipment_state.detail
    if profile.portrait_state.status == "ok":
        assert profile.portrait, "media answered, but no address was on the render host"
    bnet.save_profile(profile, raw)
