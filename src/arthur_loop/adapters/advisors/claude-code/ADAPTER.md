# Advisor: claude-code

Claude Code plays the planner/reviewer role headlessly.

## Submit + poll in one step

Claude answers synchronously, so submit and capture collapse into one pass:

    claude -p "$(cat <rendered-prompt>.md)" > /tmp/advisor-reply.md
    arthur queue submit --job-id <JOB> --keep-lock
    arthur capture --project-id <ID> --job-id <JOB> --kind <kind> \
      --source-chat-title claude-code --source-file /tmp/advisor-reply.md
    arthur queue poll-result --job-id <JOB> --marker-found true --status completed

Use the same prompt pack and control blocks as chatgpt-browser — only the transport differs. For conversational memory across a project's planning turns, keep one session per project (`--continue` or a per-project session id).
