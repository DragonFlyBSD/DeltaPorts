---
triggers:
  intents: [bump_portrevision]
  flows: [patch]
tags: [portrevision]
priority: 50
---

# bump_portrevision — increment PORTREVISION

## When to use

The port already builds at this upstream version, but you changed
how it builds (added a patch, edited Makefile flags, etc.). Bumping
PORTREVISION signals to packagers that the binary package needs
rebuilding even though the upstream tarball is unchanged.

Do **not** use when:
- The upstream PORTVERSION changed → that's a separate edit (the
  overlay should already reflect the new version).
- This is the first time the port is being touched on this target
  — PORTREVISION is for *re*-builds, not initial introductions.

## Shape

```json
{ "type": "bump_portrevision" }
```

No fields. The intent emits `mk set PORTREVISION "1"` in
`overlay.dops`. The literal `"1"` is the current implementation —
it's a hardcoded floor, not a true increment of the existing
value. If the upstream Makefile already declares a higher
PORTREVISION, you'll want to express the bump explicitly via
`change_makefile(key="PORTREVISION", value="<N+1>", op="set")`
instead.

## Pairing with other intents

`bump_portrevision` should be the **last** intent in an attempt
that lands behavior changes. Bumping first and then realizing the
behavior change doesn't actually work creates a stray revision the
operator has to walk back.

## Failure modes

`bump_portrevision` doesn't fail at the intent layer — it always
appends the statement to the overlay. The bump becomes ineffective
only if compose can't apply it (e.g. ambiguous PORTREVISION
declarations in the source), which would surface as an apply-time
diagnostic in the next `materialize_dports` call.
