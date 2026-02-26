# Agent guidance for pull request updates

When you are asked to update an existing pull request, review open pull request review
threads/comments and resolve threads that are now outdated by your new changes.

## Policy
- Prefer semantic resolution over string matching: decide based on whether your latest
  code/work fully addresses the reviewer concern.
- Resolve only comments/threads that are actually satisfied by the current PR state.
- Leave comments open when feedback is still partially addressed, ambiguous, or needs
  human confirmation.

## Expected workflow
1. Identify the target PR and enumerate unresolved review threads/comments.
2. Map each thread to changed files/commits and decide if it is outdated.
3. If outdated and permissions are available, resolve the review thread through the
   GitHub API/tooling.
4. Post a brief follow-up note when helpful, referencing the commit or file that
   addressed the feedback.

## Permissions
- If repository/user permissions allow thread resolution, perform it directly.
- If permissions or tooling are missing, report exactly what blocked automatic
  resolution and which threads remain open.

## Safety
- Never auto-resolve broad design discussions unless the conversation explicitly
  indicates closure.
- Never resolve threads that request external validation you did not perform.
