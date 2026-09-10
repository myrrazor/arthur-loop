# Advisor: chatgpt-browser

Drives a logged-in ChatGPT (Pro) conversation through the browser UI. No API calls — that's the point: you use the subscription and the full model you already pay for.

## Requirements

- A browser with a logged-in ChatGPT session that your agent can control (browser extension tools, Playwright with an authenticated profile, or OS-level computer use).
- One dedicated conversation per project. Record its title and URL in `config/arthur-loop.json` under the project entry, and verify the title before typing into it.

## Submit

1. `arthur queue claim --job-id <JOB>` (takes the browser lock).
2. Open the project's conversation by URL; confirm the visible title matches `advisor_target_title` before sending anything.
3. Paste the rendered prompt as a single message (single-line if your input path is fragile). Every prompt must demand: reply ends with ONE fenced control block, echo the marker and IDEMPOTENCY_KEY, exact uppercase decision values, lowercase true/false.
4. `arthur queue submit --job-id <JOB>` (releases the lock by default; `--keep-lock` if you're polling right away).

## Poll

- First poll ~1 minute after submit, then every 5 (`arthur queue due` tells you when).
- Still thinking → `arthur queue poll-result --job-id <JOB> --marker-found false --status waiting_for_chatgpt`.
- Stopped with no visible output → `--status stopped_no_output`; retry once with the same idempotency key per polling policy, then fail cleanly.

## Capture

- Prefer copy-response, but verify the clipboard is actually non-empty — the copy control can silently fail. Fall back to reading the message text from the page or accessibility tree.
- Save BEFORE acting on it:

      arthur capture --project-id <ID> --job-id <JOB> --kind <kind> \
        --source-chat-title "<title>" --source-file <response.md>

- If `capture` exits `3`, the response was quarantined (`control_block_valid: false`) or said HUMAN_INPUT_REQUIRED: a human decision is already open for the project — never auto-approve from an invalid block, stop that project and move on.

## Failure modes seen in practice

- Copy-response populating an empty clipboard → always verify, keep the accessibility-text fallback.
- "Stopped thinking" with no output → transient; one retry usually lands.
- Automation checks on fresh browser profiles → use a profile you actually use, and check the terms of any service you automate. This transport is fragile by nature — browser UIs change without notice — and it is not exercised by the test suite. The `manual`, `codex`, and `claude-code` advisors exist so the core never depends on it.
