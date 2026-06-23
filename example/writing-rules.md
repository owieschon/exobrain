# Writing rules (example domain)

How wiki pages in this domain are written. The goal is a set of short, atomic,
cross-linked pages that a reader can scan quickly.

## Page shape
Every `wiki/*.md` page has:

1. **A title** — `# Imperative claim` (one concept per page).
2. **An opening claim** — the takeaway in one or two sentences, first.
3. **A body** — the reasoning and any detail, under `## Detail` or prose.
4. **`## See also`** — links to related pages as `[[slug]]` (the filename
   without `.md`). Cross-domain links use `[[domain/slug]]`.
5. **`## Sources`** — where the claim comes from (a URL, a book, a captured
   session). A page with no source is a candidate, not an established note.

## Provenance taxonomy
Tag the strength of a claim so a reader knows how much to trust it:

- **established** — confirmed by a primary source or repeated practice.
- **seed** — a working belief worth recording, not yet confirmed.
- **unvalidated-external** — pulled from an outside source and not yet vetted;
  must say so loudly and never silently override an established note.

## One canonical home per concept
Each concept lives on exactly one page. Other pages link to it; they never copy
its content.
