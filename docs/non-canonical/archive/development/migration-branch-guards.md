# Migration branch guards (retired)

> **Non-canonical reference:** This document is retained for internal or historical context and is not part of the canonical Arthexis documentation set.

`BranchSplinterOperation`, `BranchMergeOperation`, and `BranchTagOperation`
remain in the repository for historical context only.

The current migration baseline reset workflow does not use branch-tag or
splinter/merge orchestration. New migration baselines are generated directly
in canonical `apps/*/migrations/` modules.
