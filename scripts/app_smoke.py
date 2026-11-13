"""Smoke test of a frozen wowlab build (ADR-0038 item 4, M17-08).

    python scripts/app_smoke.py --exe wowlab.app/Contents/MacOS/wowlab --version 0.2.0

Runs the built executable as a player's machine would, with nothing of the
repository on its path, and checks, in order:

1. `--version` prints `wowlab <version>`, the tag's version;
2. `-P -m wowlab_core --version` prints the same: the form `wowlab ui` starts
   its runs with, which the frozen launcher has to accept;
3. `ui` serves: it is started with `BROWSER` set to `scripts/record_url.py`,
   which writes the URL it is given to a file and exits 0, so no real browser
   starts. The URL, boot key included, is read from that file; the page it
   opens is fetched (200, the page shell with its session key); a wrong boot
   key is refused (403); and one command, `doctor --json`, runs
   through the page's own run endpoint, which exercises step 2's form from
   inside the server. Then the server is stopped with Ctrl-C (SIGINT) and has
   to exit within a few seconds.

On macOS `ui` is passed explicitly: started with no arguments and no
terminal, the launcher would hand off to Terminal instead (ADR-0038 item 3),
which the owner checks by opening the app once.

The run gets a home folder of its own in a temporary directory and
`WOWLAB_WOW_ROOT` pointing at an empty folder, so it never reads a game
install or writes into the user's own wowlab data. Standard library only.
Exit 0 when every check passes; otherwise the first failure is printed and
the exit is 1.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

RECORDER = Path(__file__).resolve().with_name("record_url.py")
URL = re.compile(r"http://127\.0\.0\.1:(?P<port>\d{1,5})/\?boot=(?P<boot>[A-Za-z0-9_-]{20,})")
SESSION = re.compile(r'<meta id="wowlab-session" content="(?P<key>[A-Za-z0-9_-]{20,})">')
WAIT_S = 60.0
STOP_S = 15.0


class SmokeError(Exception):
    """A check failed; the message says which."""


def _env(home: Path, wow_root: Path, url_file: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("WOWLAB_", "PYTHON"))}
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_DATA_HOME": str(home / "data"),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_CACHE_HOME": str(home / "cache"),
            "LOCALAPPDATA": str(home / "local"),
            "APPDATA": str(home / "roaming"),
            "WOWLAB_WOW_ROOT": str(wow_root),
            "BROWSER": str(RECORDER),
            "WOWLAB_SMOKE_URL_FILE": str(url_file),
        }
    )
    return env


def check_version(exe: Path, version: str, env: dict[str, str]) -> None:
    expected = f"wowlab {version}"
    for args in (["--version"], ["-P", "-m", "wowlab_core", "--version"]):
        done = subprocess.run(
            [str(exe), *args],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        got = done.stdout.strip()
        if done.returncode != 0 or got != expected:
            raise SmokeError(
                f"{' '.join(args)}: exit {done.returncode}, printed {got!r}, "
                f"expected {expected!r}; stderr: {done.stderr.strip()!r}"
            )
        print(f"ok: {' '.join(args)} -> {got}")


def _open(request: urllib.request.Request | str) -> tuple[int, bytes]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=120) as reply:
            return int(reply.status), bytes(reply.read())
    except urllib.error.HTTPError as refused:
        return int(refused.code), bytes(refused.read())


def _wait_for_url(url_file: Path, server: subprocess.Popen[bytes]) -> str:
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        if url_file.exists():
            return url_file.read_text(encoding="utf-8").strip()
        if server.poll() is not None:
            raise SmokeError(f"wowlab ui exited ({server.returncode}) before opening a browser")
        time.sleep(0.2)
    raise SmokeError(f"wowlab ui opened no browser within {WAIT_S:.0f} s")


def check_ui(exe: Path, env: dict[str, str], url_file: Path) -> None:
    server = subprocess.Popen(
        [str(exe), "ui"],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        url = _wait_for_url(url_file, server)
        match = URL.fullmatch(url)
        if match is None:
            raise SmokeError(f"the browser was given {url!r}, not a 127.0.0.1 link with a key")
        print("ok: wowlab ui opened the browser with a 127.0.0.1 link and a boot key")
        base = f"http://127.0.0.1:{match['port']}"

        status, body = _open(url)
        session = SESSION.search(body.decode("utf-8", "replace"))
        if status != 200 or b"<title>wowlab</title>" not in body or session is None:
            raise SmokeError(f"GET of the link: {status}, {len(body)} bytes, no page or no key")
        print(f"ok: GET of the link -> 200, the page ({len(body)} bytes) with its session key")

        status, _ = _open(f"{base}/?boot=not-the-key")
        if status != 403:
            raise SmokeError(f"a wrong boot key answered {status}, not 403")
        print("ok: a wrong boot key -> 403")

        request = urllib.request.Request(
            f"{base}/api/run",
            data=json.dumps({"command": ["doctor"], "params": {}}).encode(),
            headers={"Content-Type": "application/json", "X-Wowlab-Session": session["key"]},
            method="POST",
        )
        status, body = _open(request)
        if status != 200:
            raise SmokeError(f"POST /api/run doctor: {status} {body[:300]!r}")
        reply = json.loads(body)
        try:
            report = json.loads(reply["stdout"])
        except (KeyError, TypeError, ValueError):
            report = None
        if not isinstance(report, dict) or "install_root" not in report:
            raise SmokeError(f"doctor through the page printed no report: {body[:500]!r}")
        print(f"ok: doctor ran through the page (exit {reply.get('exit_code')})")
    except BaseException:
        _stop(server)
        raise
    code = _stop(server)
    if code != 0:
        raise SmokeError(f"wowlab ui exited {code} after Ctrl-C, not 0")
    print("ok: wowlab ui stopped on Ctrl-C (exit 0)")


def _stop(server: subprocess.Popen[bytes]) -> int | None:
    """Ctrl-C the server (SIGINT; a kill where there is none) and wait; its
    exit code, or None when it had to be killed. Its stderr is echoed."""
    code: int | None
    if server.poll() is None:
        if os.name == "nt":
            server.terminate()
        else:
            server.send_signal(signal.SIGINT)
        try:
            code = server.wait(STOP_S)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
            code = None
    else:
        code = server.returncode
    _, err = server.communicate()
    sys.stdout.write(err.decode("utf-8", "replace"))
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--exe", type=Path, required=True, help="the frozen wowlab executable")
    parser.add_argument("--version", required=True, help="the version the tag names")
    args = parser.parse_args(argv)
    exe = args.exe.resolve()
    if not exe.is_file():
        print(f"app_smoke: {exe} is not a file", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="wowlab-app-smoke-") as tmp:
        home = Path(tmp) / "home"
        wow_root = Path(tmp) / "no-install"
        home.mkdir()
        wow_root.mkdir()
        env = _env(home, wow_root, Path(tmp) / "url.txt")
        try:
            check_version(exe, args.version, env)
            check_ui(exe, env, Path(tmp) / "url.txt")
        except SmokeError as failed:
            print(f"app_smoke: FAILED: {failed}", file=sys.stderr)
            return 1
    print("app_smoke: every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
