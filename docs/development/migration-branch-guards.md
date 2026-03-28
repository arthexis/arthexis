# Migration branch guards (retired)

`BranchSplinterOperation`, `BranchMergeOperation`, and `BranchTagOperation`
remain in the repository for historical context only.

The current migration baseline reset workflow does not use branch-tag or
splinter/merge orchestration. New migration baselines are generated directly
in canonical `apps/*/migrations/` modules.
