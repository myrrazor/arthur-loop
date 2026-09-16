from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "menubar/install.sh"


class ArthurBarInstallTests(unittest.TestCase):
    def test_installer_is_macos_only_source_build(self) -> None:
        self.assertTrue(SCRIPT.is_file())
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("Darwin", text)
        self.assertIn("macOS 14+", text)
        self.assertIn("swift build -c release", text)
        self.assertIn("pipx / uv / ./install.sh do not install this", text)
        self.assertIn("$HOME/.local/bin", text)
        self.assertIn("ArthurBar --root", text)
        subprocess.run(["sh", "-n", str(SCRIPT)], check=True)


if __name__ == "__main__":
    unittest.main()
