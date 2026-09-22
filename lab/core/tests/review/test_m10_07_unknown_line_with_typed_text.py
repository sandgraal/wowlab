# Probe from review of m10/07-wtf-text-formats; reproduces documents accepting
# an Unknown line whose text is a SET/bind/VER line, so the views disagree
# with the bytes and parse(doc.to_bytes()) != doc.
"""The module docstring promises that the typed line models validate
themselves "so a future writer can build lines and trust the document stays
well-formed", and the document validators reject line sequences that would
not re-parse (`test_constructed_documents_reject_a_line_sequence_that_would_not_reparse`).
`Unknown` validates nothing beyond line breaks, so a document built with
`Unknown(text=b'SET a "1"')` is accepted: its `cvars` view says `a` is not
set while the bytes it serialises set `a`, and parsing those bytes gives a
different document. The same holds for `bind` lines and for a `VER` header
outside a record.

Every input here is constructed (hostile model construction, L8).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from wowlab_core.wtfconfig import (
    BindingsDocument,
    BindLine,
    ConfigDocument,
    MacroBodyLine,
    MacroHeaderLine,
    MacrosDocument,
    SetLine,
    Unknown,
    parse_bindings,
    parse_config,
    parse_macros,
)


def test_positive_control_typed_lines_reparse_to_the_same_document_constructed() -> None:
    config = ConfigDocument(lines=(SetLine(text=b'SET a "1"', ending=b"\n"),))
    assert parse_config(config.to_bytes()) == config
    assert config.get("a") is not None
    bindings = BindingsDocument(lines=(BindLine(text=b"bind A B", ending=b"\r\n"),))
    assert parse_bindings(bindings.to_bytes()) == bindings
    macros = MacrosDocument(
        lines=(
            MacroHeaderLine(text=b'VER 3 01 "a" "1"', ending=b"\n"),
            MacroBodyLine(text=b"/x", ending=b"\n"),
        )
    )
    assert parse_macros(macros.to_bytes()) == macros


def test_config_document_rejects_unknown_line_with_set_text_constructed() -> None:
    with pytest.raises(ValidationError):
        ConfigDocument(lines=(Unknown(text=b'SET a "1"', ending=b"\n"),))


def test_bindings_document_rejects_unknown_line_with_bind_text_constructed() -> None:
    with pytest.raises(ValidationError):
        BindingsDocument(lines=(Unknown(text=b"bind A B", ending=b"\r\n"),))


def test_macros_document_rejects_unknown_ver_line_outside_a_record_constructed() -> None:
    with pytest.raises(ValidationError):
        MacrosDocument(
            lines=(
                Unknown(text=b'VER 3 01 "a" "1"', ending=b"\n"),
                Unknown(text=b"/x", ending=b"\n"),
            )
        )
