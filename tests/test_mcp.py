from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from arthur_loop.cli import main
from arthur_loop.mcp import (
    MessageReader,
    canonical_tool_name,
    dispatch_tool,
    handle_rpc,
    public_tool_name,
    serve,
    tool_catalog,
)
from arthur_loop.queue_ledger import QueueLedger


def _init(tmp: str) -> None:
    code = main(
        [
            "init", "--root", tmp, "--yes", "--main-agent", "none",
            "--advisor", "manual", "--executor", "manual", "--tracker", "none",
            "--no-governor", "--no-integrations",
        ]
    )
    assert code == 0


class ToolNameTests(unittest.TestCase):
    def test_portable_names_round_trip(self) -> None:
        self.assertEqual(public_tool_name("arthur.status", "portable"), "arthur_status")
        self.assertEqual(public_tool_name("arthur.loop.create", "portable"), "arthur_loop_create")
        self.assertEqual(canonical_tool_name("arthur_status"), "arthur.status")
        self.assertEqual(canonical_tool_name("arthur_loop_create"), "arthur.loop.create")
        self.assertEqual(canonical_tool_name("arthur.queue.claim"), "arthur.queue.claim")

    def test_catalog_hides_dots_for_grok(self) -> None:
        names = [tool["name"] for tool in tool_catalog("portable")]
        self.assertIn("arthur_status", names)
        self.assertIn("arthur_loop_create", names)
        self.assertIn("arthur_gate_implementation", names)
        self.assertTrue(all("." not in name for name in names))


class McpDispatchTests(unittest.TestCase):
    def test_status_tick_queue_gate_decision_and_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            root = Path(tmp)
            status = dispatch_tool(root, "arthur.status")
            self.assertIn("structuredContent", status)
            self.assertIn("state", status["structuredContent"])

            created = dispatch_tool(
                root,
                "arthur.loop.create",
                {
                    "project_id": "MCP_APP",
                    "advisor": "manual",
                    "executor": "manual",
                    "tracker": "none",
                    "actor": "agent:test",
                    "reason": "unit test",
                },
            )
            job = created["structuredContent"]["job"]
            self.assertEqual(job["project_id"], "MCP_APP")
            self.assertTrue(job["idempotency_key"].startswith("loop:"))

            due = dispatch_tool(root, "arthur.queue.due")
            self.assertEqual(due["structuredContent"][0]["job_id"], job["job_id"])

            gate = dispatch_tool(root, "arthur.gate.implementation", {"project_id": "MCP_APP"})
            self.assertFalse(gate["structuredContent"]["go"])

            opened = dispatch_tool(
                root,
                "arthur.decision.open",
                {"project_id": "MCP_APP", "title": "Scope?", "body": "24h or 7d"},
            )
            self.assertEqual(opened["structuredContent"]["status"], "OPEN")
            refused = dispatch_tool(root, "arthur.queue.claim", {"job_id": job["job_id"], "holder": "mcp-agent"})
            self.assertTrue(refused.get("isError"))
            self.assertIn("paused", refused["content"][0]["text"])

            dispatch_tool(root, "arthur.decision.answer", {"title": opened["structuredContent"]["title"], "answer": "7 days"})
            claimed = dispatch_tool(root, "arthur.queue.claim", {"job_id": job["job_id"], "holder": "mcp-agent"})
            self.assertEqual(claimed["structuredContent"]["status"], "claimed")
            submitted = dispatch_tool(root, "arthur.queue.submit", {"job_id": job["job_id"], "holder": "mcp-agent"})
            self.assertEqual(submitted["structuredContent"]["status"], "submitted")
            dispatch_tool(
                root,
                "arthur.decision.open",
                {"project_id": "MCP_APP", "title": "Pause poll?", "body": "yes"},
            )
            refused_poll = dispatch_tool(
                root,
                "arthur.queue.poll_result",
                {"job_id": job["job_id"], "marker_found": True, "holder": "mcp-agent"},
            )
            self.assertTrue(refused_poll.get("isError"))
            self.assertIn("paused", refused_poll["content"][0]["text"])

    def test_unknown_tool_and_empty_idempotency_are_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            with self.assertRaises(KeyError):
                dispatch_tool(Path(tmp), "arthur.nope")
            created = dispatch_tool(
                Path(tmp),
                "arthur.queue.create",
                {
                    "job_id": "BQ-X-001",
                    "project_id": "X",
                    "target_chat_title": "t",
                    "target_chat_url": "manual",
                    "idempotency_key": "   ",
                },
            )
            self.assertTrue(created.get("isError"))

    def test_file_arguments_cannot_escape_instance_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside_tmp:
            _init(tmp)
            outside = Path(outside_tmp) / "outside.md"
            outside.write_text("private", encoding="utf-8")
            captured = dispatch_tool(
                Path(tmp),
                "arthur.capture",
                {
                    "project_id": "MCP_APP",
                    "job_id": "BQ-MCP_APP-001",
                    "kind": "plan",
                    "source_chat_title": "test",
                    "source_file": str(outside),
                },
            )
            self.assertTrue(captured.get("isError"))
            self.assertIn("escapes the instance root", captured["content"][0]["text"])

            created = dispatch_tool(
                Path(tmp),
                "arthur.queue.create",
                {
                    "job_id": "BQ-MCP_APP-001",
                    "project_id": "MCP_APP",
                    "target_chat_title": "test",
                    "target_chat_url": "manual",
                    "prompt_path": str(outside),
                },
            )
            self.assertTrue(created.get("isError"))
            self.assertIn("escapes the instance root", created["content"][0]["text"])


class McpProtocolTests(unittest.TestCase):
    def test_initialize_tools_list_and_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            from arthur_loop.mcp import McpContext

            ctx = McpContext(Path(tmp), tool_name_style="portable")
            init = handle_rpc(ctx, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            self.assertEqual(init["result"]["serverInfo"]["name"], "arthur-loop")
            listed = handle_rpc(ctx, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            names = [tool["name"] for tool in listed["result"]["tools"]]
            self.assertIn("arthur_status", names)
            prompt = handle_rpc(
                ctx,
                {"jsonrpc": "2.0", "id": 3, "method": "prompts/get", "params": {"name": "arthur-loop"}},
            )
            text = prompt["result"]["messages"][0]["content"]["text"]
            self.assertIn("Advisor", text)
            self.assertIn("arthur.loop.create", text)

    def test_serve_ndjson_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            stdin = io.StringIO(
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
                + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n"
            )
            stdout = io.StringIO()
            serve(Path(tmp), stdin=stdin, stdout=stdout, tool_name_style="dotted")
            lines = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
            self.assertEqual(lines[0]["id"], 1)
            self.assertIn("arthur.status", [t["name"] for t in lines[1]["result"]["tools"]])

    def test_content_length_reader(self) -> None:
        body = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"})
        framed = f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}"
        reader = MessageReader(io.StringIO(framed))
        message = reader.read()
        self.assertEqual(message["method"], "ping")
        self.assertEqual(reader.framing, "content-length")

    def test_content_length_counts_utf8_bytes_without_consuming_next_frame(self) -> None:
        first = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "écho"}, ensure_ascii=False)
        second = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        framed = (
            f"Content-Length: {len(first.encode('utf-8'))}\r\n\r\n{first}"
            f"Content-Length: {len(second.encode('utf-8'))}\r\n\r\n{second}"
        )
        reader = MessageReader(io.StringIO(framed))
        self.assertEqual(reader.read()["method"], "écho")
        self.assertEqual(reader.read()["method"], "ping")

    def test_serve_reports_invalid_content_length_instead_of_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            hello = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            stdin = io.StringIO(
                "Content-Length: abc\r\n\r\n"
                "Content-Length: -1\r\n\r\n"
                + hello + "\n"
            )
            stdout = io.StringIO()
            self.assertEqual(serve(Path(tmp), stdin=stdin, stdout=stdout, tool_name_style="dotted"), 0)
            raw = stdout.getvalue()
            self.assertIn('"code":-32700', raw.replace(" ", ""))
            self.assertIn("invalid Content-Length: 'abc'", raw)
            self.assertIn("Content-Length must not be negative", raw)
            self.assertIn("arthur-loop", raw)

    def test_content_length_reader_rejects_non_integer_and_negative_lengths(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            MessageReader(io.StringIO("Content-Length: abc\r\n\r\n")).read()
        self.assertIn("invalid Content-Length: 'abc'", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            MessageReader(io.StringIO("Content-Length: -1\r\n\r\n")).read()
        self.assertIn("must not be negative", str(ctx.exception))


class McpCliTests(unittest.TestCase):
    def test_mcp_tools_cli(self) -> None:
        out = io.StringIO()
        from contextlib import redirect_stdout

        with redirect_stdout(out):
            code = main(["mcp", "tools", "--tool-name-style", "portable"])
        self.assertEqual(code, 0)
        catalog = json.loads(out.getvalue())
        self.assertTrue(any(item["name"] == "arthur_loop_create" for item in catalog))


if __name__ == "__main__":
    unittest.main()
