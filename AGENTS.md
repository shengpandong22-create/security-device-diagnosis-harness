# Agent collaboration rules

- Codex must not repeatedly call a model or tool to poll CodeBuddy process, log, file, or
  heartbeat state.
- Local PowerShell orchestration owns process exit, timeout, activity and error detection.
- Codex may continue only after a terminal transition: `completed`, `failed`, `stalled`,
  `timeout`, `max_turns_exceeded`, or `takeover_required`.
- A normal `running` state or log growth must never wake Codex.
- Every review is a new `codex exec` process. Do not resume the planning or monitoring session.
- Do not pass complete CodeBuddy logs or polling history to review; pass stable file paths and
  the exact base/head commits.
