# Advisor: grok

A [Grok Build](https://docs.x.ai/build) (`grok`) session plays the planner/reviewer role. This is a shipped pack — `arthur init --preset solo --main-agent grok` and the pair preset can wire it honestly.

## Submit + poll in one step

Grok answers in-process, so submit and capture collapse into one pass. Prefer a non-interactive prompt flag when your build has one (`grok -p` is the usual shape, same idea as `claude -p` / `codex exec`). If your `grok` build only has an interactive TUI, paste the rendered prompt and save the reply yourself — the artifact contract does not change.

    grok -p "$(cat <rendered-prompt>.md)" > /tmp/advisor-reply.md
    arthur queue submit --job-id <JOB> --keep-lock
    arthur capture --project-id <ID> --job-id <JOB> --kind <kind> \
      --source-chat-title grok --source-file /tmp/advisor-reply.md
    arthur queue poll-result --job-id <JOB> --marker-found true --status completed

Use the same prompt pack and control blocks as the other advisors — only the transport differs. Keep one working directory (or session) per project so planning context does not cross streams.

## Pair note

When Grok reviews another agent's work, keep the reviewer session away from the working tree mid-implementation. The reviewer should only see packets and artifacts.

## Agent integrate

`arthur integrations install grok` writes the worker skill plus a project MCP entry (portable tool names: `arthur_status`, not `arthur.status` — Grok skips dotted MCP names). Restart Grok after install. File presence is not "connected".
