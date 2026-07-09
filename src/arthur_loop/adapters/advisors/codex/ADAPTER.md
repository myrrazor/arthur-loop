# Advisor: codex

A Codex CLI session plays the planner/reviewer role headlessly — useful when Codex is your main (or only) agent and you want it wearing both hats, or as the reviewer in a pair setup.

## Submit + poll in one step

Codex answers synchronously, so submit and capture collapse into one pass:

    codex exec "$(cat <rendered-prompt>.md)" > /tmp/advisor-reply.md
    arthur queue submit --job-id <JOB> --keep-lock
    arthur capture --project-id <ID> --job-id <JOB> --kind <kind> \
      --source-chat-title codex --source-file /tmp/advisor-reply.md
    arthur queue poll-result --job-id <JOB> --marker-found true --status completed

Use the same prompt pack and control blocks as the other advisors — only the transport differs. Keep one session (or `--cd` directory) per project so planning context doesn't cross streams. If your Codex version lacks `exec`, run interactively and paste the prompt; the artifact contract is identical.

## Pair note

When Codex reviews another agent's work, keep the reviewer session separate from any Codex executor session — the reviewer should only ever see packets and artifacts, never the working tree mid-implementation.
