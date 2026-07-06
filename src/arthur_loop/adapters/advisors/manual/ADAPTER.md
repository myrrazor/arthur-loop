# Advisor: manual

A human (or any external tool) plays the planner. No automation required — this is also the adapter to start with when trying Arthur Loop for the first time, and it doubles as the reference implementation of the advisor contract.

## Contract

- Submit: write the rendered prompt to `queue/manual/pending/<job_id>.md`, then `arthur queue submit --job-id <JOB>`.
- You (or your tool) write the reply — ending with a standard control block — to `queue/manual/done/<job_id>.md`.
- Poll: check whether `done/<job_id>.md` exists.
  - exists → `arthur capture --project-id <ID> --job-id <JOB> --kind <kind> --source-chat-title manual --source-file queue/manual/done/<job_id>.md`, then `arthur queue poll-result --job-id <JOB> --marker-found true --status completed`
  - not yet → `arthur queue poll-result --job-id <JOB> --marker-found false --status waiting_for_chatgpt`

The browser lock still serializes access; with a human advisor that just means one pending question at a time — which is a feature.
