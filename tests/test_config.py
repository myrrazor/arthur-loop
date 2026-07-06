from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from arthur_loop.config import load_config


class ConfigTests(unittest.TestCase):
    def test_missing_file_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(Path(tmp))

            self.assertEqual(config["advisor"]["adapter"], "chatgpt-browser")
            self.assertEqual(config["tracker"]["adapter"], "none")
            self.assertTrue(config["components"]["resource_governor"])
            self.assertEqual(config["polling_policy"]["steady_poll_minutes"], 5)

    def test_file_overrides_merge_over_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config/arthur-loop.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "advisor": {"adapter": "manual"},
                        "components": {"resource_governor": False},
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(root)

            self.assertEqual(config["advisor"]["adapter"], "manual")
            self.assertFalse(config["components"]["resource_governor"])
            # untouched sections keep defaults
            self.assertTrue(config["components"]["heartbeat"])
            self.assertEqual(config["polling_policy"]["first_poll_minutes"], 1)

    def test_unknown_adapter_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config/arthur-loop.json").write_text(
                json.dumps({"advisor": {"adapter": "telepathy"}}), encoding="utf-8"
            )

            with self.assertRaises(ValueError):
                load_config(root)


if __name__ == "__main__":
    unittest.main()
