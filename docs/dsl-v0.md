# DeltaPorts DSL v0 (Normative)

## Status

Normative language specification for `overlay.dops` in DeltaPorts v3.

This document is the single source of truth for DSL grammar and semantics.

Operator workflows and migration patterns are documented in
`docs/dportsv3-user-guide.md`; this file defines language semantics.

---

## Purpose

`overlay.dops` is the contributor-facing source format. The runtime compiles
DSL input into a normalized in-memory plan and executes that plan directly.

No persisted transition ops file is required.

---

## File and Scope Model

- File name: `overlay.dops`
- One origin per file; `port <category/name>` is required exactly once
- Directives are file-global; operations inherit the active target scope
- Multiple `target ...` directives are allowed in one file
- `type` is file-global (`port|mask|dport|lock`), not target-scoped
- Initial active scope is implicit `@any` before the first explicit `target`
- `target` accepts one selector or a comma-separated selector list in one token,
  for example `target @main,@2026Q1` (no spaces)

Supported targets:

- `@any`
- `@main`
- `@YYYYQ1`, `@YYYYQ2`, `@YYYYQ3`, `@YYYYQ4`

---

## Lexical Rules

- `#` starts a comment outside heredoc bodies
- Strings are double-quoted with escapes: `\\`, `\"`, `\n`, `\t`
- Bare identifiers are space-free tokens; quote values with spaces
- `\` at end of line is a continuation marker for command forms that allow it
- Heredoc form for recipes: `<<'TAG'` ... `TAG`
- Heredoc body is preserved exactly, including leading tabs
- `target` selector lists are lexed as one word token; write
  `target @main,@2026Q1` (no spaces after commas)

---

## Grammar (EBNF)

```ebnf
file              = { line } ;
line              = blank | comment | directive | operation ;

directive         = target_directive
                  | port_directive
                  | type_directive
                  | reason_directive
                  | maintainer_directive ;

target_directive  = "target" target_list ;
target_list       = target { "," target } ;
port_directive    = "port" origin ;
type_directive    = "type" port_type ;
reason_directive  = "reason" string ;
maintainer_directive = "maintainer" string ;

target            = "@any" | "@main" | "@" year "Q" quarter ;
year              = digit digit digit digit ;
quarter           = "1" | "2" | "3" | "4" ;
origin            = ident "/" ident ;
port_type         = "port" | "mask" | "dport" | "lock" ;

operation         = mk_op | file_op | text_op | patch_op ;

mk_op             = mk_var_op | mk_block_op | mk_target_op ;

mk_var_op         = "mk" "set" var string [on_missing]
                  | "mk" "unset" var [on_missing]
                  | "mk" "add" var token [on_missing]
                  | "mk" "remove" var token [on_missing] ;

mk_block_op       = "mk" "disable-if" "condition" string [contains] [on_missing]
                  | "mk" "replace-if" "from" string "to" string [contains] [on_missing]
                  | "mk" "block" "set" "condition" string [contains] heredoc ;

mk_target_op      = "mk" "target" "set" ident heredoc
                  | "mk" "target" "append" ident heredoc
                  | "mk" "target" "remove" ident [on_missing]
                  | "mk" "target" "rename" ident "->" ident [on_missing] ;

file_op           = "file" "copy" path "->" path
                  | "file" "materialize" path "->" path
                  | "file" "remove" path [on_missing] ;

text_op           = "text" "line-remove" "file" path "exact" string [on_missing]
                  | "text" "line-insert-after" "file" path "anchor" string "line" string [on_missing]
                  | "text" "replace-once" "file" path "from" string "to" string [on_missing] ;

patch_op          = "patch" "apply" path ;

contains          = "contains" string ;
on_missing        = "on-missing" ("error" | "warn" | "noop") ;
```

---

## Directive Semantics

- `target` sets active target selectors for all following operations until the
  next `target`
- `port` declares origin and is mandatory exactly once
- `type` defaults to `port` if omitted
- `reason` and `maintainer` are optional metadata fields

### Target scope rules (normative)

- Active scope starts as `@any` before the first explicit `target`
- `target @a,@b,...` is a selector list; each following operation expands into
  one scoped operation per selector, in selector order
- `@any` MUST NOT be combined with explicit selectors in one `target` directive
  (for example, `target @any,@2026Q1` is invalid)
- For an apply run targeting `T`, operation evaluation order is:
  1) all `@any` operations in source order
  2) all `T` operations in source order
  3) all other target-scoped operations, marked skipped with target-mismatch

---

## Operation Forms

### Makefile variable ops

```text
mk set <VAR> "<value>" [on-missing error|warn|noop]
mk unset <VAR> [on-missing error|warn|noop]
mk add <VAR> <token> [on-missing error|warn|noop]
mk remove <VAR> <token> [on-missing error|warn|noop]
```

`mk set` v1 behavior:

- if `<VAR>` exists exactly once, replace that assignment with `<VAR>= <value>`
- if `<VAR>` does not exist, create a new top-level `<VAR>= <value>` assignment
  before the first target or `.include`, whichever appears first
- if `<VAR>` exists more than once, fail with an ambiguous-match error

`mk add` and `mk remove` still require an existing assignment.

### Makefile conditional/block ops

```text
mk disable-if condition "<expr>" [contains "<anchor>"] [on-missing ...]
mk replace-if from "<expr>" to "<expr>" [contains "<anchor>"] [on-missing ...]

mk block set condition "<expr>" [contains "<anchor>"] <<'MK'
	<block line>
MK
```

`mk block set` v1 behavior (normative):

- matches and rewrites only `.if ... .endif` regions whose `.if` condition equals
  `<expr>` (not `.elif`-only matches)
- `contains` filters candidate regions by block text substring
- one match: replace full region body
- no match: insert new `.if ... .endif` block before the last `.include` line
  when present, else append at EOF
- multiple matches: fail with ambiguous-match error
- `on-missing` is not allowed on `mk block set`

### Makefile target/recipe ops

```text
mk target set <name> <<'MK'
	<recipe line>
MK

mk target append <name> <<'MK'
	<recipe line>
MK

mk target remove <name> [on-missing ...]
mk target rename <old> -> <new> [on-missing ...]
```

`mk target set` v1 behavior:

- if `<name>` exists exactly once, replace that target block
- if `<name>` does not exist, insert a new target block before the last
  `.include` line when present, else append at EOF
- if `<name>` exists more than once, fail with an ambiguous-match error

### File/text ops

```text
file copy <src> -> <dst>
file materialize <src> -> <dst>
file remove <path> [on-missing ...]

text line-remove file <path> exact "<line>" [on-missing ...]
text line-insert-after file <path> anchor "<line>" line "<line>" [on-missing ...]
text replace-once file <path> from "<needle>" to "<replacement>" [on-missing ...]
```

### Patch fallback

```text
patch apply <path>
```

Path resolution semantics:

- `file copy`: `<src>` and `<dst>` are resolved relative to the port root
- `file materialize`: `<src>` is resolved relative to the `overlay.dops`
  directory (source root), `<dst>` is resolved relative to the port root
- `file remove`, `text ... file <path>`, and `patch apply <path>` resolve paths
  relative to the port root
- absolute paths and paths escaping the active root are invalid
- `file materialize` does not support wildcard source patterns in v1

`patch apply` applies immediately to the port root tree; it does not register a
build-time patch asset lane.

---

## Determinism and Failure Policy

Defaults:

- ambiguous match: `error`
- parse failure in targeted input: `error`
- missing subject with no override: `error`

`on-missing` values:

- `error`: fail operation
- `warn`: emit warning and continue
- `noop`: skip silently (discouraged outside migration)

Determinism:

- operations apply in source order after target scoping is resolved
- multi-selector target directives preserve selector order during scoped-op
  expansion (for example `@2026Q1,@2026Q2` expands in that order)
- reapplying the same plan must be idempotent

---

## Compile Mapping to Normalized In-Memory Plan

- each DSL operation compiles to one normalized operation record
- operations before first explicit `target` compile with target `@any`
- `target` selector lists map to per-operation target expansion in selector
  order
- mapping of forms to operation kinds:
  - `mk set` -> `mk.var.set`
  - `mk unset` -> `mk.var.unset`
  - `mk add` -> `mk.var.token_add`
  - `mk remove` -> `mk.var.token_remove`
  - `mk disable-if` -> `mk.block.disable`
  - `mk replace-if` -> `mk.block.replace_condition`
  - `mk block set` -> `mk.block.set`
  - `mk target set|append|remove|rename` -> `mk.target.*`
  - `file copy|materialize|remove` -> `file.copy|file.materialize|file.remove`
  - `text ...` -> `text.*`
  - `patch apply` -> `patch.apply`
- directive mapping:
  - `type` -> `plan.type`
  - `reason` -> `plan.reason`
  - `maintainer` -> `plan.maintainer`
  - `port` -> `plan.port`

---

## Conformance Anchors (Implementation References)

Normative behavior in this document is implemented and regression-tested in:

- parser: `scripts/generator/dportsv3/engine/parser.py`
- semantic scope analysis: `scripts/generator/dportsv3/engine/semantic.py`
- plan compilation: `scripts/generator/dportsv3/engine/planner.py`
- apply target ordering/skipping: `scripts/generator/dportsv3/engine/apply.py`
- tests: `scripts/generator/tests/test_dportsv3_parser.py`
- tests: `scripts/generator/tests/test_dportsv3_semantic.py`
- tests: `scripts/generator/tests/test_dportsv3_planner.py`
- tests: `scripts/generator/tests/test_dportsv3_apply.py`

---

## Diagnostics Contract

`dportsv3 dsl parse|check|plan` emit structured diagnostics with:

- `severity` (`error|warning|info`)
- `code` (stable machine-readable identifier)
- `message`
- source location (`path`, optional `line` and `column`)

Reserved baseline codes:

- `E_PARSE_*`: lexical/syntax failures
- `E_SEM_*`: semantic validation failures
- `E_PLAN_*`: plan construction failures
- `W_*`: non-fatal warnings

Bootstrap compatibility code:

- `E_NOT_IMPLEMENTED`: temporary stub response during engine bring-up

---

## Conformance Examples

### Valid

```text
port security/dsniff
type port
reason "DragonFly-specific adjustments"

mk add USES ssl
mk set BROKEN_DragonFly "baseline applies to all targets"

target @2025Q2
mk remove USES linux on-missing warn
mk set BROKEN_DragonFly "fails with old SSL API"

mk target set dfly-patch <<'MK'
	${REINPLACE_CMD} -e 's/foo/bar/' ${WRKSRC}/file
MK

patch apply dragonfly/@2025Q2/patch-src_main.c
```

### Valid (multi-selector target scope)

```text
target @2026Q1,@2026Q2
port category/name
mk set VAR "shared-quarter-change"
```

### Invalid (missing `port`)

```text
target @main
mk set BROKEN "missing origin"
```

### Invalid (mixed `@any` with explicit target)

```text
target @any,@2026Q1
port category/name
mk add USES ssl
```

### Invalid (bad target)

```text
target @2025Q5
port category/name
```

### Invalid (selector list with spaces)

```text
target @main, @2026Q1
port category/name
```
