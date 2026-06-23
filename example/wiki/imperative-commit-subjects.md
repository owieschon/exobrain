# Write the commit subject as a command

Phrase the first line of a commit message in the imperative mood: "Add retry to
the uploader", not "Added retry" or "Adds retry". The subject should complete
the sentence "If applied, this commit will ___".

## Detail
The imperative mood matches the messages Git itself generates (for example
"Merge branch ..." and "Revert ..."), so the history reads consistently. It also
keeps subjects short and action-oriented, which makes a log skimmable.

Conventions that travel well:
- Keep the subject under ~50 characters; put detail in the body after a blank
  line.
- Describe *what and why*, not *how* — the diff already shows how.

## See also
- [[atomic-commits]]

## Sources
- Established practice; widely documented in Git style guides.
