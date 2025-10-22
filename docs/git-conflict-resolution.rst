Resolving Detached HEAD Merge Conflicts
=======================================

If you find yourself detached from a branch with unfinished merge conflicts, use
this workflow to recover safely:

1. Inspect your repository status to see the in-progress operation and the
   conflicted files::

      git status

2. Either finish the operation by resolving the conflicts or abort it entirely.

   * To finish, edit each conflicted file to the desired content, stage it with
     ``git add`` and continue the operation (for example ``git rebase
     --continue``).
   * To abort the operation, run the matching ``git <operation> --abort`` command
     (``git rebase --abort``, ``git merge --abort``, etc.).

3. Once the working tree is clean and ``git status`` reports no pending
   operations, switch back to the branch that tracks ``origin``::

      git switch <branch>

   Re-running ``./upgrade.sh --latest`` works as well because it calls
   ``git switch`` under the hood.

4. Only after the tree is clean should you use ``git stash`` or any other
   commands that assume a completed merge; otherwise Git will refuse to proceed
   until conflicts are resolved.

Following these steps ensures you do not lose work while recovering from an
interrupted upgrade.

Control and Watchtower nodes now automate this recovery during
``./upgrade.sh`` by aborting incomplete rebases/merges and realigning to the
latest ``origin/<branch>`` commit before pulling. The manual steps above remain
helpful for Terminal nodes or when running Git commands outside the upgrade
script.
