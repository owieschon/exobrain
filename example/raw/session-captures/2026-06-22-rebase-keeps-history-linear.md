# Rebase a feature branch before merging to keep history linear

**Why it matters:** A linear history is far easier to read and bisect than one
braided with merge commits.

**Source session:** example-0001
**Source turn:** discussion of how to integrate a finished feature branch
**Suggested domain:** example

## Lesson

Before merging a short-lived feature branch, rebase it onto the latest main so
its commits replay on top of current work. The result is a straight line of
commits with no merge bubble, which keeps `git log` readable and `git bisect`
cheap. Only rebase branches that have not been shared, since rebasing rewrites
commit hashes; for shared branches, prefer a merge so you do not invalidate
other people's checkouts.
