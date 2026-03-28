# DSL Engine Technical Review

## Status

Post-implementation review of the v3 DSL engine as built. Companion to
`architecture-v3.md` (design intent) and `dsl-v0.md` (language spec).

This document evaluates design decisions, implementation quality, architectural
debt, and missing capabilities based on a full read of the engine source
(~5,500 lines across 17 files).

---

## Scope

Covers `scripts/generator/dportsv3/engine/` and its integration surface:

- Lexer, parser, AST
- Semantic analysis
- Plan compilation
- Apply pipeline and executors
- Makefile CST parser and rewrite primitives
- FileTransaction and oracle

Does not cover compose pipeline, migration tooling, or tracker.

---

## What Works Well

### Pipeline decomposition

The lex -> parse -> semantic -> plan -> apply pipeline is cleanly separated
with well-defined intermediate representations at each boundary. Each stage is
independently testable and produces structured diagnostics. This is textbook
language implementation and it works.

### `on-missing` mechanism

Ports change between quarterly branches. A `.dops` file targeting `@any` will
encounter Makefiles where a variable exists in one branch but not another.
Rather than requiring per-branch overlay files, `on-missing warn|noop|error`
lets a single file express "try this, but don't die if it's gone." This is the
domain-specific escape valve that makes the DSL practical.

### Target scoping

`@any` -> `@main` -> `@2025Q1` with operations bound to targets at the
semantic stage. The apply stage runs `@any` ops first, then matching target
ops, and skips the rest. No complex inheritance or override rules. Covers the
real use cases without overengineering.

### Makefile CST parser

Does not try to fully understand BSD make semantics -- just identifies
assignments, targets, recipe lines, directives, and includes at a structural
level. Handles line continuations, builds indexes for fast lookup, and
preserves raw text for round-tripping. Right level of ambition for a tool that
rewrites Makefiles without evaluating them.

### FileTransaction

Simple dict-based staging with commit/rollback. Provides read-through cache so
if an earlier operation wrote to a file, a later operation reads the staged
version. Operations compose correctly (e.g., `mk set` then `mk add` on the
same Makefile). ~80 lines, does exactly what is needed.

### Lexer quality

Cursor-based, handles heredocs correctly (including `<<'TAG'` quoting
convention), supports line continuations, produces accurate source spans. The
heredoc implementation handles edge cases well -- empty bodies, EOF before
terminator, trailing whitespace after the tag. Error recovery on unrecognized
syntax (e.g., bare single quotes) emits a diagnostic and continues.

### Parser quality

Clean recursive descent. Each production is a method, error recovery syncs to
the next newline, `_finish_statement()` enforces one-statement-per-line. The
code is repetitive (each `_parse_*` method follows the same
expect-validate-construct pattern) but that is inherent to hand-written parsers
and makes each production independently readable.

---

## Design Issues

### 1. The planner is a pass-through, not a planner

`compile_plan()` does almost no transformation. It walks `scoped_ops`, maps
each AST node to a `PlanOp` with a `kind` string and a `payload` dict, and
that is it. No optimization, no reordering, no deduplication, no conflict
detection. If a `.dops` file says `mk set VAR "a"` followed by `mk set VAR "b"`,
the planner emits both ops and the apply stage will execute both sequentially
(the second silently overwrites the first).

The `Plan` model is essentially a serializable version of the AST with targets
baked in. The `PlanOp.payload` is an untyped `dict[str, Any]`, which means all
the type information from the AST nodes (`MkOpNode.var`, `MkOpNode.value`,
etc.) gets erased into string-keyed dictionaries that every executor has to
defensively validate.

This makes the plan stage feel like an unnecessary serialization boundary
rather than a meaningful compilation step. The AST + scoped ops could be
passed directly to the apply stage with the same effect and stronger types.

### 2. Validation is repeated across three stages

The semantic analyzer checks that `mk set` has `var` and `value`. The planner
checks the same. The executor checks that `name` and `value` are strings. Each
stage independently validates essentially the same invariants, because the
untyped `payload` dict forces downstream consumers to re-verify what upstream
already checked.

If the AST types flowed through to execution (or if `PlanOp` were a union of
typed dataclasses rather than a generic bag), most of these checks could be
eliminated. Roughly 40% of executor code is defensive `isinstance` validation
that can never fail if the pipeline is used correctly.

### 3. Makefile CST parser has a subtle misparse risk

The target regex:

```python
_TARGET_RE = re.compile(r"^\s*([^\s:#][^:]*)\s*:\s*(.*)$")
```

can misidentify lines as targets. A line like `${VAR:S/old/new/}= value` with
a colon in a variable modifier will match as a target rather than an assignment
if the assignment regex fails first.

The assignment regex character class `[A-Za-z0-9_.$(){}\-/]` does not include
`:`, so variable names with modifiers will not match. The parser tries
assignment first, which is correct, but the fallthrough to target matching
creates a misparse for this case.

In practice this is probably rare in the ports tree, but it is a latent
parsing bug for exotic variable names.

### 4. The oracle is positioned wrong in the pipeline

The oracle runs after all operations are applied, materializes the entire
modified tree to a temp directory, and runs `bmake -n`. If the oracle fails,
the transaction rolls back in strict/CI mode. In non-strict mode, oracle
failures produce warnings but the writes still commit.

The problem is that the oracle validates the final state but cannot tell you
which operation broke the Makefile. If you have 15 operations and the oracle
fails, you get "bmake failed" with no indication of which op caused it.

A per-operation oracle check (at least for `mk.*` operations) would be more
useful for debugging, though obviously more expensive.

Additionally, `patch.apply` operations execute directly via `subprocess` and
bypass the `FileTransaction` entirely -- their changes are already on disk. So
the oracle's materialized tree is partially redundant for ports that use
patches: it copies the already-patched files from disk, then overlays the
transaction's staged writes on top.

### 5. `patch.apply` breaks the transaction model

Every other operation uses `FileTransaction` -- reads go through
`txn.read_text()`, writes go through `txn.stage_write()`. But `patch.apply`
shells out to `patch(1)` which writes directly to the filesystem.

Consequences:

- If a subsequent operation fails and the transaction rolls back, patch changes
  are NOT rolled back.
- In dry-run mode, `patch --dry-run` is used, which is correct but inconsistent
  with how other operations handle dry-run (they skip `txn.commit()`).
- The `txn` parameter is received but explicitly ignored (`_ = txn`).

This is a fundamental model break. The engine implicitly relies on the compose
pipeline running against a freshly-seeded copy of the port tree, so "rollback"
means "throw away the whole directory." Within the engine's own abstraction,
it is a hole.

### 6. Error codes are stringly-typed and lack a registry

Diagnostics use error codes like `E_PARSE_UNTERMINATED_STRING`,
`E_SEM_INVALID_TARGET_SCOPE`, `E_APPLY_AMBIGUOUS_MATCH`, etc. These are plain
strings with no central registry, no documentation, and no guarantee of
uniqueness. Nothing prevents two different sites from using the same code with
different meanings, and nothing helps a consumer enumerate all possible errors.

---

## Implementation Observations

### Executors are thorough but verbose

Every executor follows the same pattern: validate payload -> resolve path ->
load Makefile -> find node -> check missing/ambiguous -> compute replacement ->
stage write. The boilerplate of payload extraction + path resolution + Makefile
loading is ~15 lines per executor that could be factored into a decorator or
helper. The explicitness does make each executor independently debuggable.

### `_replace_line_range` is the universal rewrite primitive

All Makefile mutations go through this function, which replaces lines
`[start, end]` with a new set of lines. This is simple and predictable but has
a limitation: it does not preserve whitespace style. If the original assignment
was `VAR=\tvalue` (tab-separated), the replacement will be `VAR= value` (space
after `=`). Similarly, multi-line continued assignments (`VAR= foo \\\n\tbar`)
get collapsed to single lines.

### `makefile_rewrite.py` has an identity crisis

Its functions return `EditIntent` objects -- non-executing descriptions of what
would happen. But the executors do not use them as intents; they use them as
query results (checking `node_indices` and `ambiguous`) and then do their own
rewriting. The `EditIntent.payload` field (which carries the operation
parameters) is never read by any consumer. The module is effectively a query
layer, not a rewrite layer, despite its name.

---

## What Is Missing

### No static analysis across operations

The semantic stage validates each operation in isolation. It cannot detect:

- Two `mk set` operations targeting the same variable (conflict)
- A `mk remove` followed by `mk add` on the same variable/token (possible no-op)
- A `file remove` followed by a `text` operation on the same file (guaranteed
  failure)
- An `mk set` on a variable already set to the requested value (no-op)

### No conditional operations

The DSL has `on-missing` for graceful degradation, but no way to say "only do
X if Y is true." For example, "add this CFLAGS token only if the Makefile uses
GCC" or "remove this patch only if the upstream version is >= 3.0." Target
scoping partially addresses this (different branches get different ops), but
within a single target there is no conditional logic.

### No operation for `.include` lines

You cannot add, remove, or replace `.include` directives via the DSL. The CST
parser recognizes them (`IncludeNode`) and they are used for insert-position
logic, but there is no `mk include add/remove/replace` operation. If a port
needs to change its includes, `text replace-once` is the only option, which is
fragile.

### No glob/wildcard support for file operations

`file copy` and `file remove` take single paths. If you need to remove
`files/patch-*` (a common pattern when replacing upstream patches), you need
one `file remove` per file, each with `on-missing noop`.

---

## Summary Assessment

The DSL solves a real problem -- declarative port overlay management -- and the
core pipeline is well-structured. The lexer and parser are production-quality.
The domain-specific features (`on-missing`, target scoping, Makefile CST) are
the right abstractions for the domain.

The main weaknesses are architectural: the plan stage does not earn its keep as
an untyped serialization boundary, validation is repeated across stages because
of this, and `patch.apply` breaks the transactional model. These are not
blocking issues -- the system works and the test suite confirms it -- but they
represent technical debt that will compound as the DSL grows.

The implementation is at ~5,500 lines for a single-file-scope, line-oriented
DSL with ~18 operations. That is reasonable for the feature set, especially
given the Makefile CST parser and oracle infrastructure. The code is explicit,
consistent, and well-tested (219 tests). The main cost is verbosity from
defensive validation that the type system could prevent.
