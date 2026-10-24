#!/usr/bin/env python3
"""End to end: the real server and the real page in headless Chrome, on any OS with Chrome.

    python3 tests/e2e_browser.py            (from anywhere; CHROME=<path> if Chrome is not found)

It copies two rounds of examples/demo into a temporary folder, starts `signoff.py --no-open` on it, opens the
single-use start link in headless Chrome over the DevTools protocol, picks a non-recommended option, accepts
both rounds with real mouse clicks, presses Done, and checks the exit code, stdout and both answers files.

Not named test_*.py, so `unittest discover -s tests` does not run it: it needs a browser. It proves the server
and the page together in a browser; it is not a Claude Code session. Standard library only, Python 3.9+.
"""
import base64
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples" / "demo"
TOTAL_TIMEOUT = 150  # seconds for the whole run; a hang fails the job instead of eating minutes
WINDOWS = sys.platform == "win32"
PROCS = []  # everything to kill in every exit path
WORK = []  # the temporary folder


class E2EError(Exception):
    pass


def check(condition, message):
    if not condition:
        raise E2EError(message)


def find_chrome():
    candidates = [os.environ.get("CHROME", "")]
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if os.environ.get(var):
            candidates.append(os.path.join(os.environ[var], "Google", "Chrome", "Application", "chrome.exe"))
    candidates += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome",
                   "google-chrome-stable", "chromium", "chromium-browser", "chrome"]
    for c in candidates:
        if c and (os.path.isfile(c) or shutil.which(c)):
            return shutil.which(c) or c
    raise E2EError("Chrome not found; set CHROME to its path")


def kill(proc):
    """Kill a process and its children (Chrome has many); never raises."""
    try:
        if WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=20)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
        proc.wait(10)
    except Exception:
        pass


def cleanup():
    for p in PROCS:
        kill(p)
    for w in WORK:
        shutil.rmtree(str(w), ignore_errors=True)


def watchdog():
    print("FAIL: no result after %d s (hang); killing Chrome and the server" % TOTAL_TIMEOUT, file=sys.stderr, flush=True)
    cleanup()
    os._exit(1)


def spawn(argv, **kw):
    if not WINDOWS:
        kw["start_new_session"] = True  # own process group, so kill() takes the children too
    p = subprocess.Popen(argv, **kw)
    PROCS.append(p)
    return p


def pump(stream, sink):
    for line in stream:
        sink.append(line)


def wait_for(what, fn, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.1)
    raise E2EError("timed out waiting for " + what)


class Browser:
    """Just enough of the DevTools protocol: one page, commands and their results (as media/demo/record_page.py)."""

    def __init__(self, profile):
        argv = [find_chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                "--no-default-browser-check", "--lang=en-US", "--window-size=1280,900",
                "--user-data-dir=" + profile, "--remote-debugging-port=0", "about:blank"]
        if sys.platform.startswith("linux"):
            argv.insert(1, "--no-sandbox")  # CI containers and new Ubuntu block the sandbox; the page is our own, local
        self.proc = spawn(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        active = Path(profile) / "DevToolsActivePort"

        def port():
            if self.proc.poll() is not None:
                raise E2EError("Chrome exited before it listened (code %s)" % self.proc.returncode)
            try:
                return int(active.read_text().split()[0])
            except (OSError, ValueError, IndexError):
                return None
        self.port = wait_for("Chrome's DevTools port", port)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        pages = json.load(opener.open("http://127.0.0.1:%d/json" % self.port, timeout=15))
        path = urllib.parse.urlparse([p for p in pages if p["type"] == "page"][0]["webSocketDebuggerUrl"]).path
        self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(("GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, self.port, key)).encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            check(chunk, "Chrome closed the DevTools socket")
            self.buf += chunk
        self.buf = self.buf.split(b"\r\n\r\n", 1)[1]
        self.mid = 0

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 20)
            check(chunk, "Chrome closed the DevTools socket")
            self.buf += chunk
        data, self.buf = self.buf[:n], self.buf[n:]
        return data

    def _message(self):
        data = b""
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            data += self._read(n)
            if b1 & 0x80:
                return json.loads(data)

    def call(self, method, **params):
        self.mid += 1
        payload = json.dumps({"id": self.mid, "method": method, "params": params}).encode()
        n, mask = len(payload), os.urandom(4)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n)
                                if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
        while True:
            msg = self._message()
            if msg.get("id") == self.mid:
                check("error" not in msg, "DevTools %s failed: %s" % (method, msg.get("error")))
                return msg.get("result", {})

    def js(self, expression):
        r = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        check("exceptionDetails" not in r, "page script failed: %s" % r.get("exceptionDetails"))
        return r["result"].get("value")

    def center(self, selector):
        """Scroll the element into view and return where its middle is."""
        box = self.js("(function () { var e = document.querySelector(%s); if (!e) return null; "
                      "e.scrollIntoView({block: 'center'}); var r = e.getBoundingClientRect(); "
                      "return [r.x + r.width / 2, r.y + r.height / 2, r.width, r.height]; })()" % json.dumps(selector))
        check(box and box[2] > 0 and box[3] > 0, "no visible element %s" % selector)
        return box[0], box[1]

    def click(self, selector):
        """A real mouse move, press and release, so focus lands where it does for a person."""
        wait_for("%s on the page" % selector, lambda: self.js("!!document.querySelector(%s)" % json.dumps(selector)))
        x, y = self.center(selector)
        time.sleep(0.2)
        x, y = self.center(selector)  # again: scrolling may have moved a sticky bar over it
        hit = self.js("(function () { var e = document.querySelector(%s), h = document.elementFromPoint(%f, %f); "
                      "return !!h && (e === h || e.contains(h) || h.contains(e) || "
                      "(!!h.closest('label') && h.closest('label').contains(e))); })()" % (json.dumps(selector), x, y))
        check(hit, "something else covers %s at (%d, %d)" % (selector, x, y))
        self.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        for kind in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left",
                      buttons=1 if kind == "mousePressed" else 0, clickCount=1)
        time.sleep(0.3)


def run():
    work = Path(tempfile.mkdtemp(prefix="signoff-e2e-"))
    WORK.append(work)
    topic = work / "2026-01-05-settings"
    topic.mkdir()
    shutil.copytree(str(DEMO / "mockups"), str(topic / "mockups"))
    shutil.copyfile(str(DEMO / "round-02.json"), str(topic / "round-01.json"))
    shutil.copyfile(str(DEMO / "round-04.json"), str(topic / "round-02.json"))
    rounds = [json.loads((topic / ("round-0%d.json" % n)).read_text(encoding="utf-8")) for n in (1, 2)]

    # The pick: the first single-choice item of round 1, and an option that is not the recommended one.
    items = rounds[0]["items"]
    index = next(i for i, it in enumerate(items) if it.get("select", "one") == "one")
    recommended = items[index]["recommended"]
    option = next(k for k, o in enumerate(items[index]["options"]) if o["id"] != recommended)
    pick_id, pick_item = items[index]["options"][option]["id"], items[index]["id"]

    server = spawn([sys.executable, "-I", "-B", str(ROOT / "plugin" / "signoff" / "signoff.py"), "--no-open", "--timeout", "3",
                    str(topic)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                   encoding="utf-8", errors="replace")
    out, err = [], []
    for stream, sink in ((server.stdout, out), (server.stderr, err)):
        threading.Thread(target=pump, args=(stream, sink), daemon=True).start()
    try:
        def start_link():
            check(server.poll() is None, "Signoff exited early: %s" % "".join(err))
            return next((m.group(0) for m in (re.search(r"http://\S+", l) for l in err) if m), None)
        link = wait_for("the start link on stderr", start_link)
        browser = Browser(str(work / "profile"))
        browser.call("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)
        browser.call("Page.enable")
        browser.call("Page.navigate", url=link)

        # The page rendered: a tab per round plus the Summary.
        tabs = wait_for("the tabs", lambda: browser.js("document.querySelectorAll('#tabs [role=tab]').length") or None)
        check(tabs == 3, "expected 3 tabs (2 rounds and the Summary), found %s" % tabs)
        check(browser.js("document.getElementById('title').textContent") == rounds[0]["title"],
              "the page title is not round 1's title")

        browser.click("#r0-i%d-o%d" % (index, option))
        check(browser.js("document.getElementById('r0-i%d-o%d').checked" % (index, option)),
              "the picked option %s is not checked after the click" % pick_id)
        browser.click("#accept-0")
        wait_for("round 2 to open", lambda: browser.js("!document.getElementById('panel-1').hidden"))
        browser.click("#accept-1")
        wait_for("Done to be ready", lambda: browser.js("!document.getElementById('done').disabled"))
        browser.click("#done")

        try:
            code = server.wait(timeout=60)
        except subprocess.TimeoutExpired:
            raise E2EError("Signoff did not exit after Done; stderr: %s" % "".join(err))
        time.sleep(0.5)  # let the pump threads drain
        check(code == 0, "Signoff exited %s, expected 0; stderr: %s" % (code, "".join(err)))

        listed = [l.strip() for l in out if l.strip()]
        expected = [topic / "round-01.answers.json", topic / "round-02.answers.json"]
        check(len(listed) == len(expected), "stdout should list %d answers paths and nothing else, got %r" % (len(expected), out))
        for line, want in zip(listed, expected):
            check(os.path.basename(line) == want.name and os.path.exists(line) and os.path.samefile(line, str(want)),
                  "stdout line %r is not %s" % (line, want.name))
        for n, want in enumerate(expected):
            answers = json.loads(want.read_text(encoding="utf-8"))
            check(answers.get("done") is True, "%s: done is %r" % (want.name, answers.get("done")))
            check(answers.get("accepted") is True, "%s: accepted is %r" % (want.name, answers.get("accepted")))
            if n == 0:
                got = answers["answers"][pick_item]
                check(got["choice"] == pick_id and got["source"] == "user",
                      "%s: %s is %r, expected the picked %s from the user" % (want.name, pick_item, got, pick_id))
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise


def main():
    timer = threading.Timer(TOTAL_TIMEOUT, watchdog)
    timer.daemon = True
    timer.start()
    try:
        run()
    except E2EError as e:
        print("FAIL: %s" % e, file=sys.stderr)
        return 1
    finally:
        timer.cancel()
        cleanup()
    print("PASS: 2 rounds, a non-recommended pick, both accepted, Done, exit 0, 2 answers paths on stdout")
    return 0


if __name__ == "__main__":
    sys.exit(main())
