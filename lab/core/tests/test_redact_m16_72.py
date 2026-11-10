"""`wowlab_core.redact`: the two M16-72 leftovers from the M16-04 to M16-26
reviews.

- A long run of path separators is redacted in linear time; before M16-72 the
  home-folder shape was tried at every byte of the run and walked the rest of
  it each time, so 40 000 slashes took about twenty seconds.
- A `file://` URL keeps its scheme: `file:///home/<name>` became
  `fil<HOME>`, because the `e:` of `file:` was read as a drive letter.

Every input here is `constructed` (L8): runs of separators and home folders
spelled for the test, not read from any install.
"""

from __future__ import annotations

import time

import pytest

from wowlab_core import redact
from wowlab_core.redact import HOME

# Long enough that the quadratic walk takes minutes (about 20 s at 40 000
# before M16-72, growing with the square), short enough that a linear walk
# takes a few milliseconds on any runner.
RUN = 200_000
# Generous: a slow, loaded CI runner is still two orders of magnitude inside it.
LIMIT_SECONDS = 5.0


@pytest.mark.parametrize(
    "text",
    [
        "/" * RUN,
        "\\" * RUN,
        "C:" + "\\" * RUN,
        "file:" + "/" * RUN,
        "x" + "/\\" * (RUN // 2),
        "/" * RUN + "home",
        "/" * RUN + "home/",
    ],
    ids=["slashes", "backslashes", "drive", "file-url", "mixed", "home-no-sep", "home-no-name"],
)
def test_constructed_a_long_run_of_separators_is_redacted_in_linear_time(text: str) -> None:
    replace = redact.Redactor(users=["thrall"], homes=["/home/thrall"], characters=["Jaina"])
    start = time.perf_counter()
    out = replace(text)
    elapsed = time.perf_counter() - start
    assert out == text  # no home folder in it, nothing to replace
    assert elapsed < LIMIT_SECONDS, f"{elapsed:.2f} s for {len(text)} characters"


def test_constructed_a_long_run_still_finds_the_home_after_it() -> None:
    text = "/" * RUN + "home/thrall/wow"
    start = time.perf_counter()
    out = redact.Redactor()(text)
    assert time.perf_counter() - start < LIMIT_SECONDS
    assert out == f"{HOME}/wow"


@pytest.mark.parametrize(
    ("homes", "text", "expected"),
    [
        # The shape alone, with no known home.
        ((), "file:///home/thrall/wow.log", f"file://{HOME}/wow.log"),
        ((), "see file:///Users/thrall/x", f"see file://{HOME}/x"),
        ((), "FILE:///home/thrall", f"FILE://{HOME}"),
        ((), "file:///C:/Users/thrall/x", f"file:///{HOME}/x"),
        ((), "file://localhost/home/thrall/x", f"file://localhost{HOME}/x"),
        # The home the redactor knows, which is replaced first.
        (("/home/thrall",), "file:///home/thrall/wow.log", f"file://{HOME}/wow.log"),
        ((r"C:\Users\thrall",), "file:///C:/Users/thrall/x", f"file:///{HOME}/x"),
    ],
)
def test_constructed_a_file_url_keeps_its_scheme(
    homes: tuple[str, ...], text: str, expected: str
) -> None:
    assert redact.Redactor(homes=homes)(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A drive letter still counts after anything that is not a letter.
        (r"C:\Users\thrall\wow", rf"{HOME}\wow"),
        (r"path=C:\Users\thrall", f"path={HOME}"),
        (r"'C:\\Users\\thrall'", f"'{HOME}'"),
        (r"\\?\C:\Users\thrall\x", rf"\\?\{HOME}\x"),
        # A letter before it: not a drive, but the home folder still goes.
        (r"xC:\Users\thrall", f"xC:{HOME}"),
        ("Error:/home/thrall", f"Error:{HOME}"),
        ("ab://home/thrall", f"ab:{HOME}"),
        ("x://home/thrall", HOME),  # `x:` is a drive letter here
        ("//home/thrall", HOME),
    ],
)
def test_constructed_home_shapes_next_to_a_colon_are_still_replaced(
    text: str, expected: str
) -> None:
    assert redact.Redactor()(text) == expected
