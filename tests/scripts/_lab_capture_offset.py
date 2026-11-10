"""The fixed time-shift offset the `scripts/lab_capture.py` suites run with.

conftest.py makes every `draw_time_shift` under test return DEFAULT_OFFSET.
The value lives here, not in conftest.py, because `from conftest import
DEFAULT_OFFSET` got whichever `conftest` module pytest had imported first:
collected together with lab/core/tests/review, that was the review tree's
conftest.py, and collection failed (M16-57). No other test tree has a module
of this name, so the import always resolves here.
"""

from __future__ import annotations

DEFAULT_OFFSET = 123 * 86400 + 4567  # invented; inside SHIFT_MIN..SHIFT_MAX
