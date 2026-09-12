## What does this change?

<!-- A sentence or two. Link any issue it fixes, e.g. "Fixes #12". -->

## Why?

<!-- What was awkward or broken before? -->

## How did you test it?

<!-- Ideally against a real Plex library. What show/feed, what did you see
     happen (files on disk, tags, the Plex scan, deletions if relevant)?
     Redact your token and personal paths. -->

## Checklist

- [ ] `pip install -r requirements.txt && python3 -m compileall -q .` passes
- [ ] `python3 fetch.py --help` still works
- [ ] Tested against a real or test Plex library, not just read through
- [ ] No secrets, tokens or personal paths in the diff
- [ ] If this touches `prune()` (deletion logic), I've explained the new
      behaviour clearly in this PR
- [ ] README / `.example` files updated if behaviour or settings changed
