"""The path rule `wowlab snap gc` keeps journal-named objects by (M11-31,
security review of #144, round 1).

Constructed (L8: boundary and hostile inputs to our own rule, not to a
client format): journal record paths as strings, including one with a very
large number of slashes. `cli._is_character_lab_file` decides from the path
alone whether a committed write's `after` is kept (True) or collected like
any object no snapshot refers to (False). The split stops after seven
separators, so a crafted path of any length is never split in full. The
graders of the rule end to end are in `test_snap_gc_lab_writes.py`.
"""

from __future__ import annotations

import time

from wowlab_core import cli

SEVEN = "WTF/Account/ACCT/Realm/Char/SavedVariables/WowLab.lua"


def test_the_keep_rule_takes_exactly_seven_parts_and_stops_splitting_a_long_path_constructed() -> (
    None
):
    cases = {
        # Seven parts, the four named ones matching case-folded: kept.
        SEVEN: True,
        "wtf/account/ACCT/Realm/Char/savedvariables/wowlab.lua": True,
        "WTF/ACCOUNT/ACCT/1/First-Second/SAVEDVARIABLES/WOWLAB.LUA": True,
        # Seven parts, a named part that does not match: collected.
        "WTF/Account/ACCT/Realm/Char/SavedVariables/WowLab.lua.bak": False,
        "WTF/Account/ACCT/Realm/Char/Other/WowLab.lua": False,
        "Interface/Account/ACCT/Realm/Char/SavedVariables/WowLab.lua": False,
        # Five parts (the account-wide file) and eight parts: collected.
        "WTF/Account/ACCT/SavedVariables/WowLab.lua": False,
        "WTF/Account/ACCT/Realm/Char/SavedVariables/x/WowLab.lua": False,
        f"{SEVEN}/extra": False,
        f"x/{SEVEN}": False,
    }
    for path, kept in cases.items():
        assert cli._is_character_lab_file(path) is kept, path

    # Many slashes: before a character's path, after it, and nothing else.
    many = 1_000_000
    for path in (f"{'x/' * many}{SEVEN}", f"{SEVEN}{'/' * many}", "/" * many):
        start = time.process_time()
        assert cli._is_character_lab_file(path) is False
        assert time.process_time() - start < 1.0, "the rule returns quickly on a long path"
