# DeltaPorts DSL v0 (Normative)

## Status

Normative language specification for `overlay.dops` in DeltaPorts v3.

This document is the single source of truth for DSL grammar and semantics.

---

## Purpose

`overlay.dops` is the contributor-facing source format. The runtime compiles
DSL input into a normalized in-memory plan and executes that plan directly.

No persisted transition ops file is required.

---

## File and Scope Model

- File name: `overlay.dops`
- One origin per file; `port <category/name>` is required exactly once
- Multiple `target @...` blocks are allowed in one file
- `type` is file-global (`port|mask|dport|lock`), not target-scoped
- No implicit target; operations must appear under explicit `target @...`

Supported targets:

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

target_directive  = "target" target ;
port_directive    = "port" origin ;
type_directive    = "type" port_type ;
reason_directive  = "reason" string ;
maintainer_directive = "maintainer" string ;

target            = "@main" | "@" year "Q" quarter ;
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
                  | "mk" "replace-if" "from" string "to" string [contains] [on_missing] ;

mk_target_op      = "mk" "target" "set" ident heredoc
                  | "mk" "target" "append" ident heredoc
                  | "mk" "target" "remove" ident [on_missing]
                  | "mk" "target" "rename" ident "->" ident [on_missing] ;

file_op           = "file" "copy" path "->" path
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

- `target` sets active target scope for all following operations until the
  next `target`
- `port` declares origin and is mandatory exactly once
- `type` defaults to `port` if omitted
- `reason` and `maintainer` are optional metadata fields

---

## Operation Forms

### Makefile variable ops

```text
mk set <VAR> "<value>" [on-missing error|warn|noop]
mk unset <VAR> [on-missing error|warn|noop]
mk add <VAR> <token> [on-missing error|warn|noop]
mk remove <VAR> <token> [on-missing error|warn|noop]
```

### Makefile conditional/block ops

```text
mk disable-if condition "<expr>" [contains "<anchor>"] [on-missing ...]
mk replace-if from "<expr>" to "<expr>" [contains "<anchor>"] [on-missing ...]
```

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

### File/text ops

```text
file copy <src> -> <dst>
file remove <path> [on-missing ...]

text line-remove file <path> exact "<line>" [on-missing ...]
text line-insert-after file <path> anchor "<line>" line "<line>" [on-missing ...]
text replace-once file <path> from "<needle>" to "<replacement>" [on-missing ...]
```

### Patch fallback

```text
patch apply <path>
```

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
- reapplying the same plan must be idempotent

---

## Compile Mapping to Normalized In-Memory Plan

- each DSL operation compiles to one normalized operation record
- `target @...` maps to per-operation `target`
- mapping of forms to operation kinds:
  - `mk set` -> `mk.var.set`
  - `mk unset` -> `mk.var.unset`
  - `mk add` -> `mk.var.token_add`
  - `mk remove` -> `mk.var.token_remove`
  - `mk disable-if` -> `mk.block.disable`
  - `mk replace-if` -> `mk.block.replace_condition`
  - `mk target set|append|remove|rename` -> `mk.target.*`
  - `file copy|remove` -> `file.copy|file.remove`
  - `text ...` -> `text.*`
  - `patch apply` -> `patch.apply`
- directive mapping:
  - `type` -> `plan.type`
  - `reason` -> `plan.reason`
  - `maintainer` -> `plan.maintainer`
  - `port` -> `plan.port`

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
target @2025Q2
port security/dsniff
type port
reason "DragonFly-specific adjustments"

mk remove USES linux on-missing warn
mk add USES ssl
mk set BROKEN_DragonFly "fails with old SSL API"

mk target set dfly-patch <<'MK'
	${REINPLACE_CMD} -e 's/foo/bar/' ${WRKSRC}/file
MK

patch apply dragonfly/@2025Q2/patch-src_main.c
```

### Invalid (missing `port`)

```text
target @main
mk set BROKEN "missing origin"
```

### Invalid (operation outside target scope)

```text
port category/name
mk add USES ssl
```

### Invalid (bad target)

```text
target @2025Q5
port category/name
```
