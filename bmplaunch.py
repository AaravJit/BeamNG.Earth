"""BeamMP (multiplayer) started by the BeamNG.Earth app (the user, 2026-10-24: "bundle BeamMP into the earth
launcher", the official BeamMP, unmodified). The official BeamMP launcher is found, or on Windows downloaded from
BeamMP's GitHub releases and checked against the .sha256 published with it, then started with --no-launch before the
game: it installs or updates BeamMP's mod in the game's mods/multiplayer and waits for the game, which connects to it
by itself (port 4444). It is stopped when the game closes. BeamMP's code (AGPL-3.0) runs as BeamMP ships it; nothing of
it is changed or kept in this project. Linux: BeamMP publishes no Linux release, but builds one on every change to its
master branch (GitHub Actions, cmake-linux.yml); BeamNG.Earth's release workflow attaches the newest successful one to
each BeamNG.Earth release (BeamMP-Launcher-linux, its .sha256, and BeamMP's source commit), and it comes from there.
One built from source is used when found (BEAMMP_LAUNCHER, ~/BeamMP-Launcher, a BeamMP-Launcher on the PATH).
Whether BeamMP starts with BeamNG.Earth is the mod's setting beammpOnLaunch (the settings page; on_launch below).
"""
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time

IS_WIN = os.name == "nt"
CORE_PORT = 4444                # the BeamMP launcher's port for the game (its Launcher.cfg default)
READY_S = 120                   # its mod update (a few MB) and start, at most this long
RELEASES = "https://api.github.com/repos/BeamMP/BeamMP-Launcher/releases/latest"
OUR_RELEASES = "https://api.github.com/repos/TecnaGamer/BeamNG.Earth/releases/latest"   # (Linux: BeamMP's build)
LINUX_ASSET = "BeamMP-Launcher-linux"
EXE = "BeamMP-Launcher.exe" if IS_WIN else "BeamMP-Launcher"
SETTINGS = "beammp.json"        # in the app's data folder: {"declined": true} once the player said no


def _settings(data_dir):
    try:
        with open(os.path.join(data_dir, SETTINGS), encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data_dir, **kw):
    d = _settings(data_dir)
    d.update(kw)
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, SETTINGS), "w", encoding="utf-8") as f:
        json.dump(d, f)


def declined(data_dir):
    return bool(_settings(data_dir).get("declined"))


# The mod's settings file in the game's user folder (settings.lua M.FILE): beammpOnLaunch, the settings page's "Start
# BeamMP with BeamNG.Earth" (default on). "No, and don't ask again" at the install question turns it off; turning it on
# again in the game asks again at the next start.
MOD_SETTINGS = os.path.join("settings", "beamngEarth.json")
ON_LAUNCH = "beammpOnLaunch"


def _mod_settings(user_dir):
    try:
        with open(os.path.join(user_dir, MOD_SETTINGS), encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def on_launch(user_dir, data_dir=None):
    """Start BeamMP with the game? The mod's setting; an older app's "never ask" (beammp.json declined) counts as off
    until the setting is written."""
    v = _mod_settings(user_dir).get(ON_LAUNCH)
    if isinstance(v, bool):
        return v
    return not (data_dir and declined(data_dir))


def set_on_launch(user_dir, value):
    d = _mod_settings(user_dir)
    d[ON_LAUNCH] = bool(value)
    path = os.path.join(user_dir, MOD_SETTINGS)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


def candidates(data_dir, env=None, home=None):
    """Where a BeamMP launcher may be, in order: BEAMMP_LAUNCHER, BeamMP's own installer's folder (Windows), ours,
    a build from source in the home folder, the PATH."""
    env = os.environ if env is None else env
    home = home or os.path.expanduser("~")
    out = []
    if env.get("BEAMMP_LAUNCHER"):
        out.append(env["BEAMMP_LAUNCHER"])
    if IS_WIN:
        appdata = env.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        out.append(os.path.join(appdata, "BeamMP-Launcher", EXE))
    out.append(os.path.join(data_dir, "beammp", EXE))
    if not IS_WIN:
        out += [os.path.join(home, "BeamMP-Launcher", "bin", EXE), os.path.join(home, EXE),
                os.path.join(home, ".local", "bin", EXE)]
        out += sorted(p for p in (os.path.join(home, d, "bin", EXE) for d in _listdir(home)
                                  if d.startswith("BeamMP-Launcher")) if p not in out)
    on_path = shutil.which(EXE)
    if on_path:
        out.append(on_path)
    return out


def _listdir(d):
    try:
        return os.listdir(d)
    except OSError:
        return []


def find(data_dir, env=None):
    """The launcher to start: BEAMMP_LAUNCHER when set, else the newest one found (by file time: an old build next to a
    new one cannot talk to BeamMP's current mod, which every launcher downloads; a v2.7.0 left in ~/BeamMP-Launcher was
    picked over a v2.8.1 build, 2026-10-24, and the game reconnected every 2 s: "Invalid lua communication")."""
    env = os.environ if env is None else env
    found = [p for p in candidates(data_dir, env=env) if os.path.isfile(p) and (IS_WIN or os.access(p, os.X_OK))]
    if not found:
        return None
    if env.get("BEAMMP_LAUNCHER") and found[0] == env["BEAMMP_LAUNCHER"]:
        return found[0]
    return max(found, key=lambda p: os.path.getmtime(p))


VERSION_RE = re.compile(r"BeamMP Launcher v([0-9][0-9.]*)")
TOO_OLD = "Invalid lua communication"     # its log, again and again: the launcher cannot talk to BeamMP's mod


def read_log(log_path):
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def log_version(text):
    """The launcher's version from its log ("BeamMP Launcher v2.8.1"), or None."""
    m = VERSION_RE.search(text or "")
    return m.group(1) if m else None


def log_too_old(text, at_least=3):
    """Its log says it cannot talk to the game's BeamMP mod (a launcher older than the mod it downloaded)."""
    return (text or "").count(TOO_OLD) >= at_least


def running(port=CORE_PORT):
    """A BeamMP launcher already listens (started by the player, or left over)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _get(url, user_agent, timeout=60):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "application/octet-stream, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def pick_assets(release, name="BeamMP-Launcher.exe"):
    """(file url, sha256 url, version) of asset name (and name.sha256) in a GitHub release's JSON, or None."""
    urls = {a.get("name"): a.get("browser_download_url") for a in (release or {}).get("assets") or []}
    exe, sha = urls.get(name), urls.get(name + ".sha256")
    if not exe or not sha:
        return None
    return exe, sha, str(release.get("tag_name") or "")


def parse_sha256(text):
    """The hex digest in a .sha256 file ("<hex>" or "<hex>  name")."""
    for tok in text.replace("*", " ").split():
        t = tok.strip().lower()
        if len(t) == 64 and all(c in "0123456789abcdef" for c in t):
            return t
    return None


def download(data_dir, user_agent, say=print, win=None):
    """The BeamMP launcher into <data>/beammp, checked against its published SHA-256: on Windows BeamMP's latest
    release, on Linux BeamMP's master build attached to BeamNG.Earth's latest release. Returns its path, or None (no
    network, none published yet, a bad checksum: nothing is kept)."""
    win = IS_WIN if win is None else win
    try:
        release = json.loads(_get(RELEASES if win else OUR_RELEASES, user_agent, 30).decode("utf-8"))
        picked = pick_assets(release, "BeamMP-Launcher.exe" if win else LINUX_ASSET)
        if not picked:
            say("BeamMP: no launcher download in " + ("its" if win else "BeamNG.Earth's") + " latest release")
            return None
        exe_url, sha_url, version = picked
        want = parse_sha256(_get(sha_url, user_agent, 30).decode("utf-8", "replace"))
        blob = _get(exe_url, user_agent, 300)
    except Exception as e:                  # (no network, GitHub's rate limit...)
        say(f"BeamMP: the download failed ({e})")
        return None
    got = hashlib.sha256(blob).hexdigest()
    if not want or got != want:
        say(f"BeamMP: the download's checksum does not match the one BeamMP published ({got} != {want}); not used")
        return None
    dest_dir = os.path.join(data_dir, "beammp")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, EXE)
    tmp = dest + ".part"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, dest)
    if not win:
        os.chmod(dest, 0o755)
    say(f"BeamMP: launcher {version} installed ({max(1, len(blob) // 1048576)} MB, checksum verified)")
    return dest


def game_user_path(user_dir):
    """The user folder BeamMP wants (the folder above the game version's, as --user-path)."""
    d = os.path.normpath(user_dir)
    base = os.path.basename(d)
    if base == "current" or base[:1].isdigit():
        return os.path.dirname(d)
    return d


def system_too_old(exe, run=None):
    """Linux: why this system can't run the launcher (BeamMP's build needs glibc 2.38 and GCC 13's libstdc++, Ubuntu
    24.04 or newer, SteamOS 3.6 or newer), or None. Asks the system's loader, by starting it with --help."""
    if IS_WIN:
        return None
    run = run or subprocess.run
    try:
        r = run([exe, "--help"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None                     # (it started: a slow --help is not the loader's refusal)
    err = (r.stderr or "") + (r.stdout or "")
    m = re.search(r"version `(GLIBC(?:XX)?_[0-9.]+)' not found", err)
    return f"this system's libraries are too old for it ({m.group(1)} missing)" if m else None


def start(exe, user_dir, log_path):
    """The BeamMP launcher, without starting the game itself (the app does), its output to log_path."""
    args = [exe, "--no-launch"]
    if not IS_WIN:
        # (a build from source: its self-update would fetch the Windows launcher; the game's folder named, as BeamMP
        # cannot find a Linux one by itself)
        args += ["--no-update", "--user-path", game_user_path(user_dir)]
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    log = open(log_path, "w", encoding="utf-8", errors="replace")
    kw = {"cwd": os.path.dirname(exe), "stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if IS_WIN:
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    else:
        kw["start_new_session"] = True
        kw["env"] = dict(os.environ, TERM=os.environ.get("TERM") or "xterm")
    return subprocess.Popen(args, **kw)


def wait_ready(proc, timeout=READY_S):
    """Until it listens for the game (its mod updated); False if it ended or took too long."""
    end = time.time() + timeout
    while time.time() < end:
        if running():
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.5)
    return False


def stop(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


if __name__ == "__main__":                  # (a check by hand: where a launcher is found)
    dd = sys.argv[1] if len(sys.argv) > 1 else "."
    print(find(dd), "running" if running() else "not running")
