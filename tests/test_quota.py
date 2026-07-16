from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arthur_loop.cli import main
from arthur_loop.quota import fetch_quota_payload, resolve_provider


SAMPLE_PAYLOAD = [
    {
        "provider": "codex",
        "usage": {"primary": {"usedPercent": 40, "windowMinutes": 300}},
    }
]


def _config(**quota: object) -> dict:
    return {"quota": quota}


class ResolveProviderTests(unittest.TestCase):
    def test_auto_prefers_codexbar_when_installed(self) -> None:
        with patch("arthur_loop.quota.shutil.which", return_value="/usr/local/bin/codexbar"):
            self.assertEqual(resolve_provider(_config(provider="auto")), "codexbar")

    def test_auto_degrades_to_none_without_codexbar(self) -> None:
        with patch("arthur_loop.quota.shutil.which", return_value=None):
            self.assertEqual(resolve_provider(_config(provider="auto")), "none")

    def test_unknown_provider_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_provider(_config(provider="telepathy"))


class FetchTests(unittest.TestCase):
    def test_none_provider_returns_no_payload_with_guidance(self) -> None:
        result = fetch_quota_payload(_config(provider="none"))

        self.assertIsNone(result.payload)
        self.assertEqual(result.source, "none")
        self.assertTrue(any("quota" in warning for warning in result.warnings))

    def test_file_provider_reads_codexbar_schema_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quota.json"
            path.write_text(json.dumps(SAMPLE_PAYLOAD), encoding="utf-8")

            result = fetch_quota_payload(_config(provider="file", path=str(path)))

            self.assertEqual(result.source, f"file:{path}")
            self.assertEqual(result.payload[0]["provider"], "codex")

    def test_file_provider_without_path_is_a_clear_error(self) -> None:
        with self.assertRaises(ValueError):
            fetch_quota_payload(_config(provider="file"))

    def test_command_provider_runs_argv_and_parses_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            emitter = Path(tmp) / "emit_quota.py"
            payload = Path(tmp) / "payload.json"
            payload.write_text(json.dumps(SAMPLE_PAYLOAD), encoding="utf-8")
            emitter.write_text(
                "import pathlib, sys; print(pathlib.Path(sys.argv[1]).read_text())",
                encoding="utf-8",
            )

            result = fetch_quota_payload(
                _config(provider="command", command=f"{sys.executable} {emitter} {payload}")
            )

            self.assertEqual(result.source, "command")
            self.assertEqual(result.payload[0]["usage"]["primary"]["usedPercent"], 40)

    def test_explicit_input_json_bypasses_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quota.json"
            path.write_text(json.dumps(SAMPLE_PAYLOAD), encoding="utf-8")

            result = fetch_quota_payload(_config(provider="none"), input_json=str(path))

            self.assertIsNotNone(result.payload)


class SnapshotCliTests(unittest.TestCase):
    def test_snapshot_uses_configured_file_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload_path = root / "quota.json"
            payload_path.write_text(json.dumps(SAMPLE_PAYLOAD), encoding="utf-8")
            (root / "config").mkdir()
            (root / "config/arthur-loop.json").write_text(
                json.dumps({"quota": {"provider": "file", "path": str(payload_path)}}),
                encoding="utf-8",
            )

            code = main(["usage", "snapshot", "--root", str(root), "--snapshot-id", "s1"])

            self.assertEqual(code, 0)
            lines = (root / "usage/snapshots.jsonl").read_text(encoding="utf-8").splitlines()
            record = json.loads(lines[0])
            self.assertEqual(record["snapshot_id"], "s1")
            self.assertEqual(record["primary_left_percent"], 60)

    def test_snapshot_without_any_provider_exits_3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config/arthur-loop.json").write_text(
                json.dumps({"quota": {"provider": "none"}}), encoding="utf-8"
            )

            code = main(["usage", "snapshot", "--root", str(root), "--snapshot-id", "s1"])

            self.assertEqual(code, 3)
            self.assertFalse((root / "usage/snapshots.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
