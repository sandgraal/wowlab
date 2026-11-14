"""`wowlab ui` launch (M16-111)."""

import typer

from wowlab_core.cli._common import (
    _say_err,
    app,
)

# ─── ui ──────────────────────────────────────────────────────────────────────


# How long `wowlab ui` waits for `webbrowser.open` before it starts serving.
_BROWSER_WAIT_S = 1.0


@app.command("ui", short_help="Open the local UI in your browser.")
def ui_command() -> None:
    """Open the local UI in your browser and serve it until Ctrl-C.

    Reading never changes anything. Six commands can change game files;
    each shows a plan and runs only when you confirm, after a snapshot.

    The server listens on 127.0.0.1 only, at a port the system picks, and
    answers only requests that carry its link's random key, or the session
    your browser got from that link. The link, a reload and a second tab
    keep working until you stop wowlab ui; a new wowlab ui makes a new link,
    and the old one stops working.
    The page has five sections. Characters lists every character and shows
    one as the lab-addon last saved it. Backups shows your backups and takes one.
    Addons lists the addons the game may skip. Keybinds shows a character's
    key bindings. Advanced holds Explorer (the whole install's files, each
    with whether wowlab may edit it, and what a picked file is and who
    writes it), Gaps (the paths the file map does not describe), Commands
    (this wowlab's commands, run with --json) and Looks (the page that
    `wowlab looks page` writes).

    On the Commands tab, a command that asks first (--yes) runs as a plan
    that changes nothing, and runs for real only when you press Confirm
    under it. snap gc and doctor change only wowlab's own store. The six
    that change game files are snap restore, undo, profile apply, sv merge,
    addon install and addon remove, and `wowlab undo` reverses any of the
    six. undo and sv merge refuse when something changed after the plan;
    the others apply what is there when you confirm. ui itself, log tail
    --follow and looks page --out are not offered. If no browser opens,
    the URL is printed once on stdout."""
    # The browser is opened on a thread: `webbrowser` waits for a browser it
    # runs in the foreground (a text browser, or a `BROWSER` program), and
    # that browser's request must find the server already serving. What it
    # returned is announced as soon as it returns: here if within
    # `_BROWSER_WAIT_S`, else by the thread itself.
    import threading
    import webbrowser

    from wowlab_core import ui

    server = ui.make_server()

    def announce(opened: bool) -> None:
        if opened:
            _say_err(f"wowlab ui: serving on http://127.0.0.1:{server.port}/; Ctrl-C to stop")
        else:
            _say_err("wowlab ui: no browser opened; open this link:")
            typer.echo(server.url)

    lock = threading.Lock()
    outcome: list[bool] = []
    late: list[bool] = []

    def open_browser() -> None:
        try:
            opened = bool(webbrowser.open(server.url))
        except webbrowser.Error:
            opened = False
        with lock:
            outcome.append(opened)
            announce_here = bool(late)
        if announce_here:
            announce(opened)

    try:
        opener = threading.Thread(target=open_browser, name="wowlab-ui-browser", daemon=True)
        opener.start()
        opener.join(_BROWSER_WAIT_S)
        with lock:
            returned = list(outcome)
            if not returned:
                late.append(True)
        if returned:
            announce(returned[0])
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
