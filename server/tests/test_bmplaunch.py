"""bmplaunch.py: the official BeamMP launcher found or (Windows) downloaded with its published SHA-256, the game's user
folder for it, a launcher already running."""
import hashlib
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bmplaunch  # noqa: E402

RELEASE = {"tag_name": "v2.8.1", "assets": [
    {"name": "BeamMP-Launcher.exe", "browser_download_url": "https://example.invalid/BeamMP-Launcher.exe"},
    {"name": "BeamMP-Launcher.exe.sha256", "browser_download_url": "https://example.invalid/BeamMP-Launcher.exe.sha256"},
]}


class Assets(unittest.TestCase):
    def test_pick_and_parse(self):
        self.assertEqual(bmplaunch.pick_assets(RELEASE)[2], "v2.8.1")
        self.assertIsNone(bmplaunch.pick_assets({"assets": [RELEASE["assets"][0]]}), "no checksum: no download")
        h = "ab" * 32
        self.assertEqual(bmplaunch.parse_sha256(h.upper() + "  BeamMP-Launcher.exe\n"), h)
        self.assertEqual(bmplaunch.parse_sha256(h), h)
        self.assertIsNone(bmplaunch.parse_sha256("not a hash"))

    def test_user_path(self):
        self.assertEqual(bmplaunch.game_user_path("/home/u/.local/share/BeamNG/BeamNG.drive/current"),
                         os.path.normpath("/home/u/.local/share/BeamNG/BeamNG.drive"))
        self.assertEqual(bmplaunch.game_user_path("/x/BeamNG.drive/0.40"), os.path.normpath("/x/BeamNG.drive"))
        self.assertEqual(bmplaunch.game_user_path("/x/BeamNG.drive"), os.path.normpath("/x/BeamNG.drive"))


class Download(unittest.TestCase):
    def fake_get(self, blob, published):
        def get(url, ua, timeout=60):
            self.assertNotIn("@", ua, "no e-mail in the User-Agent")
            if url == bmplaunch.RELEASES:
                return json.dumps(RELEASE).encode()
            if url.endswith(".sha256"):
                return (published + "  BeamMP-Launcher.exe").encode()
            return blob
        return get

    def test_checksum_matches(self):
        blob = b"MZ fake launcher"
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(bmplaunch, "_get", self.fake_get(blob, hashlib.sha256(blob).hexdigest())):
            p = bmplaunch.download(d, "BeamNG.Earth/test", say=lambda m: None)
            self.assertEqual(p, os.path.join(d, "beammp", bmplaunch.EXE))
            self.assertEqual(Path(p).read_bytes(), blob)

    def test_checksum_mismatch_keeps_nothing(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(bmplaunch, "_get", self.fake_get(b"x", "0" * 64)):
            self.assertIsNone(bmplaunch.download(d, "BeamNG.Earth/test", say=lambda m: None))
            self.assertFalse(os.path.exists(os.path.join(d, "beammp", bmplaunch.EXE)))

    def test_no_network(self):
        def boom(*a, **k):
            raise OSError("offline")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(bmplaunch, "_get", boom):
            self.assertIsNone(bmplaunch.download(d, "BeamNG.Earth/test", say=lambda m: None))


class Finding(unittest.TestCase):
    def test_env_first_and_declined(self):
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "mine", bmplaunch.EXE)
            os.makedirs(os.path.dirname(exe))
            Path(exe).write_bytes(b"x")
            os.chmod(exe, 0o755)
            self.assertEqual(bmplaunch.candidates(d, env={"BEAMMP_LAUNCHER": exe}, home=d)[0], exe)
            self.assertFalse(bmplaunch.declined(d))
            bmplaunch.save_settings(d, declined=True)
            self.assertTrue(bmplaunch.declined(d))

    def test_newest_found_unless_named(self):
        with tempfile.TemporaryDirectory() as d:
            old_ = os.path.join(d, "BeamMP-Launcher", "bin", bmplaunch.EXE)
            new_ = os.path.join(d, "BeamMP-Launcher-2.8.1", "bin", bmplaunch.EXE)
            for p, t in ((old_, 1_000_000), (new_, 2_000_000)):
                os.makedirs(os.path.dirname(p))
                Path(p).write_bytes(b"x")
                os.chmod(p, 0o755)
                os.utime(p, (t, t))
            with mock.patch("os.path.expanduser", return_value=d), mock.patch.object(bmplaunch.shutil, "which",
                                                                                     return_value=None):
                if os.name != "nt":
                    self.assertEqual(bmplaunch.find(os.path.join(d, "data"), env={}), new_, "the newest build")
                    self.assertEqual(bmplaunch.find(os.path.join(d, "data"), env={"BEAMMP_LAUNCHER": old_}), old_,
                                     "the one named in BEAMMP_LAUNCHER")

    def test_log_reading(self):
        log = "[INFO] BeamMP Launcher v2.7.0\n" + "[ERROR] (Core) Invalid lua communication\n" * 3
        self.assertEqual(bmplaunch.log_version(log), "2.7.0")
        self.assertTrue(bmplaunch.log_too_old(log))
        self.assertFalse(bmplaunch.log_too_old("[INFO] Game Connected!"))

    def test_running(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        try:
            self.assertTrue(bmplaunch.running(s.getsockname()[1]))
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
