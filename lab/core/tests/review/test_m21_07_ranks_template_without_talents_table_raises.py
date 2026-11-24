# Probe from review of m21/07-talent-link; reproduces links.build raising KeyError for a {ranks} template the sites file accepted without [talents]
"""`LinkSites._consistent` asks for `[talents]` when an address holds
`{class}` or `{race}`, but not `{ranks}`, which `_FIXED` also lets through;
`_talent_values` then returns early ("no template here then holds one") and
`build`, documented "Never raises", raises `KeyError: 'ranks'` filling the
address. Either the file check or the builder should refuse it (a
`LinkSitesError`, or a `NoLink`). The positive control is the shipped file.
Constructed sites file (L8, a boundary input): the shipped one with
`[talents]` removed and the template's class and race made literal.
"""

from __future__ import annotations

import importlib.resources

from wowlab_core import links

VERSION = "1.60.1.70888"


def _shipped() -> str:
    return (importlib.resources.files("wowlab_core") / "link_sites.toml").read_text(
        encoding="utf-8"
    )


def _without_talents() -> str:
    text = _shipped()
    start = text.index("[talents]\nranks")
    end = text.index("[talents.races]")
    end = text.index("\n\n", end)
    text = text[:start] + text[end:]
    old = 'optional = ["race"]\nrooms = { forever = "/talents/{class}?b={ranks}&race={race}" }'
    assert text.count(old) == 1
    return text.replace(old, 'rooms = { forever = "/talents/warlock?b={ranks}" }')


def test_positive_control_the_shipped_file_builds_a_link() -> None:
    made = links.build("talents", version=VERSION, class_id=9, race_id=5, ranks="022")
    assert isinstance(made, links.Link)


def test_a_ranks_template_without_talents_is_refused_not_raised() -> None:
    try:
        sites = links.parse_link_sites(_without_talents())
    except links.LinkSitesError:
        return  # refused by the file check: fixed
    made = links.build("talents", version=VERSION, ranks="022", sites=sites)
    assert isinstance(made, (links.Link, links.NoLink))
