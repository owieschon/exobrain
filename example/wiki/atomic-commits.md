# Make each commit one logical change

A commit should capture a single, self-contained change: one bug fix, one
refactor, one feature step. If you cannot describe a commit in a single sentence
without using "and", it is probably two commits.

## Detail
Atomic commits make history reviewable and reversible. A reviewer can read one
commit and understand it in isolation. `git bisect` can pinpoint the exact
change that introduced a bug, because each commit moves the tree by exactly one
idea. `git revert` can undo one change without dragging unrelated work with it.

Practical habits:
- Stage selectively (`git add -p`) so unrelated edits do not ride along.
- Separate mechanical changes (formatting, renames) from behavior changes, so
  the behavior change is easy to read.
- Commit early and often; squash later if a branch needs tidying.

## See also
- [[imperative-commit-subjects]]

## Sources
- Established practice; see the Git project's own contribution guidelines.
