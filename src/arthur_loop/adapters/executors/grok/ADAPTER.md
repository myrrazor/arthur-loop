# Executor: grok

A [Grok Build](https://docs.x.ai/build) coding session implements one approved packet at a time.

- Plan requests use `prompts/plan-only.md` — the PLAN ONLY fence is load-bearing; the session must not touch files.
- Implementation requests use `prompts/implementation-handoff.md` and are only ever sent after `arthur gate implementation --project-id <ID>` prints GO.
- One sprint per handoff. The session runs tests, captures evidence, and ends with the executor implementation-handoff control block.
- Save the handoff text as an artifact (`arthur capture`) before sending it back to the advisor for review.

Headless shape (when your `grok` build supports it):

    grok --always-approve -p "$(cat <rendered-prompt>.md)" > runtime/executor-reply.md
    arthur capture --project-id <ID> --job-id <JOB> --kind plan|implementation-handoff \
      --source-chat-title grok --source-file runtime/executor-reply.md
