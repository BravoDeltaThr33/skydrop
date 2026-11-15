"""3.14: the release files tools/build_release.py makes: what each archive holds, the
Windows scripts' line endings, the Unix scripts' permissions, and the .deb (where
dpkg-deb is installed)."""

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from skydrop import VERSION  # noqa: E402


class TestReleaseFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp(prefix="skydrop-dist-")
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_release.py"), "--out", cls.out],
                       check=True, stdout=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.out, name)

    def test_linux_archive(self):
        with tarfile.open(self.path(f"skydrop-{VERSION}-linux.tar.gz")) as tar:
            members = {m.name: m for m in tar.getmembers()}
        top = f"skydrop-{VERSION}"
        for name in ("install.sh", "uninstall.sh", "README.md", "LICENSE", "skydrop/__main__.py",
                     "skydrop/osdeps.py", "skydrop/fonts/DejaVuSans-Bold.ttf", "skydrop/fonts/LICENSE-DejaVu.txt"):
            self.assertIn(f"{top}/{name}", members)
        self.assertEqual(members[f"{top}/install.sh"].mode, 0o755)
        self.assertEqual(members[f"{top}/install.sh"].uname, "root")
        self.assertFalse([n for n in members if "__pycache__" in n or n.endswith(".pyc")])

    def test_macos_archive(self):
        with zipfile.ZipFile(self.path(f"skydrop-{VERSION}-macos.zip")) as zf:
            info = {i.filename: i for i in zf.infolist()}
        top = f"skydrop-{VERSION}-macos"
        self.assertIn(f"{top}/skydrop/term.py", info)
        self.assertEqual((info[f"{top}/install.sh"].external_attr >> 16) & 0o777, 0o755)

    def test_windows_archive(self):
        with zipfile.ZipFile(self.path(f"skydrop-{VERSION}-windows.zip")) as zf:
            names = set(zf.namelist())
            top = f"skydrop-{VERSION}-windows"
            for name in ("install.cmd", "install.ps1", "uninstall.cmd", "uninstall.ps1"):
                data = zf.read(f"{top}/{name}")
                self.assertIn(b"\r\n", data, name)
                self.assertNotIn(b"\n", data.replace(b"\r\n", b""), name)     # every line ends in CRLF
                data.decode("ascii")                                         # PowerShell 5.1 reads ANSI
            self.assertIn(f"{top}/skydrop/fonts/DejaVuSans.ttf", names)
            self.assertNotIn(f"{top}/install.sh", names)

    def test_checksums(self):
        import hashlib
        with open(self.path("SHA256SUMS.txt"), encoding="utf-8") as fh:
            lines = [ln.split() for ln in fh if ln.strip()]
        self.assertGreaterEqual(len(lines), 3)
        for digest, name in lines:
            with open(self.path(name), "rb") as fh:
                self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), digest)

    @unittest.skipUnless(shutil.which("dpkg-deb"), "dpkg-deb is Debian's")
    def test_deb(self):
        deb = self.path(f"skydrop_{VERSION}_all.deb")
        info = subprocess.run(["dpkg-deb", "-f", deb], capture_output=True, text=True, check=True).stdout
        self.assertIn(f"Version: {VERSION}", info)
        self.assertIn("python3-pil", info)
        listing = subprocess.run(["dpkg-deb", "-c", deb], capture_output=True, text=True, check=True).stdout
        self.assertIn("./usr/bin/skydrop", listing)
        self.assertIn("./usr/lib/skydrop/skydrop/osdeps.py", listing)
        self.assertNotIn("fonts/DejaVuSans", listing)            # the system's (fonts-dejavu-core)
        self.assertNotIn("__pycache__", listing)


if __name__ == "__main__":
    unittest.main()
