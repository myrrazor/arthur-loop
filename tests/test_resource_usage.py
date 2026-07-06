from __future__ import annotations

import unittest

from arthur_loop.resource_usage import classify_remaining, parse_codexbar_usage


class ResourceUsageTests(unittest.TestCase):
    def test_classifies_five_percent_reserve(self) -> None:
        self.assertEqual(classify_remaining(20), ("GREEN", True))
        self.assertEqual(classify_remaining(6), ("GREEN", True))
        self.assertEqual(classify_remaining(5), ("YELLOW", False))
        self.assertEqual(classify_remaining(4), ("RED", False))

    def test_parses_useful_output_despite_nonzero_exit(self) -> None:
        output = """
Codex session: 99% left
Codex weekly: 61% left
Codex pace: 22% in reserve
Claude session: 100% left
Gemini error: not logged in
"""

        snapshot = parse_codexbar_usage(output, returncode=1)

        self.assertEqual(len(snapshot.metrics), 4)
        self.assertEqual(snapshot.metrics[0].provider, "Codex")
        self.assertEqual(snapshot.metrics[0].label, "session")
        self.assertEqual(snapshot.metrics[0].status, "GREEN")
        self.assertTrue(any("Gemini error" in item for item in snapshot.warnings))
        self.assertTrue(any("exited 1" in item for item in snapshot.warnings))


if __name__ == "__main__":
    unittest.main()

