# Advisor: manual

A human (or any external tool) plays the planner. No automation required — this is also the adapter to start with when trying Arthur Loop for the first time, and it doubles as the reference implementation of the advisor contract.

## Contract

- Claim: `arthur queue claim --job-id <JOB>` (takes the advisor lease).
- Submit: render the prompt from `adapters/advisor/prompts/<kind>.md` (fill the `{{PLACEHOLDERS}}`), write it to `queue/manual/pending/<job_id>.md`, then `arthur queue submit --job-id <JOB>`. Nothing writes that file for you — the queue records that a prompt went out, it does not deliver it.
- You (or your tool) write the reply — ending with a standard control block — to `queue/manual/done/<job_id>.md`.
- Poll: check whether `done/<job_id>.md` exists.
  - exists → `arthur capture --project-id <ID> --job-id <JOB> --kind <kind> --source-chat-title manual --source-file queue/manual/done/<job_id>.md`, then `arthur queue poll-result --job-id <JOB> --marker-found true --status completed`
  - not yet → `arthur queue poll-result --job-id <JOB> --marker-found false --status waiting_for_chatgpt`
- `capture` exits `3` when the reply was quarantined or asked for a human; a decision is then open (`arthur decision list`) and the project is paused until it is answered.

The browser lock still serializes access; with a human advisor that just means one pending question at a time — which is a feature.
