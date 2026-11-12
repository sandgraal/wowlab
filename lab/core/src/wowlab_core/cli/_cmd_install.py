"""`wowlab install show` (M16-111)."""

from wowlab_core.cli._common import (
    JsonOpt,
    RootOpt,
    _discover,
    _emit,
    _handled,
    _not_taken_lines,
    _say,
    install_app,
)

# ─── install show ────────────────────────────────────────────────────────────


@install_app.command("show", short_help="The install folder, its flavors and their versions.")
@_handled
def install_show(root: RootOpt = None, json_out: JsonOpt = False) -> None:
    """The install root, its flavors and its .build.info rows.

    \f
    JSON: install.Install."""
    # No install found: the error's notes list every location checked
    # (`_fail`), in the order they were checked (M16-112).
    inst, found_by = _discover(root)
    if json_out:
        _emit(inst)
        return
    _say(f"Install: {inst.root} (found by {found_by})")
    if not inst.flavors:
        _say("No flavor folders (a folder named _..._ holding .flavor.info).")
    for f in inst.flavors:
        _say(f"Flavor {f.folder}")
        _say(f"  product: {f.product or '(none in .flavor.info)'}")
        _say(f"  version: {f.version or '(no .build.info row for this product)'}")
        if f.build is not None:
            _say(f"  build:   {f.build}")
        if f.matching_rows > 1:
            _say(f"  {f.matching_rows} .build.info rows name this product; the first active one")
    _say(".build.info rows (a row does not show that its flavor folder exists):")
    for row in inst.products:
        active = "active" if row.is_active else "inactive"
        _say(f"  {row.product or '(no product)'}  {row.version or '(no version)'}  {active}")
    for line in _not_taken_lines(inst.not_taken, inst.other_dirs):
        _say(line)
    if inst.decode_errors:
        _say("Some bytes in .build.info or .flavor.info are not UTF-8; shown replaced.")
