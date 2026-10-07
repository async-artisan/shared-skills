---
name: nzerp-php8-mysql8
description: Use this skill when editing PHP or MySQL-oriented data access in `/opt/homebrew/var/www/yesgooo/nzERP/backend`, `/opt/homebrew/var/www/yesgooo/nzERP/service`, or `/opt/homebrew/var/www/yesgooo/nzERP/base`, especially when choosing language features, shaping DTO or VO classes, writing query-builder or raw SQL, or reviewing code for PHP 8 and MySQL 8 compatibility.
---

# nzERP PHP8 MySQL8

Use this skill for shared PHP and MySQL coding rules across the nzERP Webman backends.

## Fast Path

- Treat the language baseline as `php >=8.0` unless the target runtime is explicitly confirmed higher.
- Treat MySQL as MySQL 8, but do not assume the app config gives you strict SQL behavior or `utf8mb4` safety by default.
- Read [project-baseline.md](references/project-baseline.md) when you need the exact repo-level constraints.

## PHP Rules

- Prefer declared types on new code:
  - parameter types
  - return types
  - typed properties
- Prefer typed RO, VO, and DTO classes over loose unshaped arrays when the payload shape is known.
- Match the repo's DI style for container-managed services: prefer `#[Inject]` plus typed properties where that is already the local pattern.
- In the `service` layer, avoid injecting another `service` when possible. Prefer injecting the needed model or lower-level dependency directly, or move cross-service orchestration up to `business`/`bll`.
- Prefer `match` over long `switch` or repetitive branching when mapping one input to one output.
- Prefer `??`, `??=`, and `?->` when they make null handling clearer and shorter.
- Prefer small private methods with explicit return types over large mixed-purpose methods.
- Keep arrays narrow. If a structure has a stable shape, normalize it once and stop passing ambiguous `array<string,mixed>` deeper than necessary.
- Use named arguments sparingly. They are acceptable for local helpers you own, but avoid binding new code to vendor or framework parameter names without a clear reason.

## PHP 8 Feature Boundary

- Default to PHP 8.0-safe features unless deployment is confirmed to be 8.1+ everywhere.
- Good default features for these repos:
  - attributes
  - union types
  - constructor property promotion
  - `match`
  - nullsafe operator
  - throw expressions when they stay readable
- Do not introduce 8.1+ syntax by default:
  - native `enum`
  - `readonly`
  - `never`
  - intersection types
  - first-class callable syntax

## MySQL Rules

- Prefer Model or Query Builder code for normal CRUD, filtering, joins, pagination, and updates.
- Use raw SQL only when it is actually the clearest tool:
  - DDL
  - bulk SQL import or migration
  - CTE or window-function queries
  - query-builder output that would be harder to read than the SQL itself
- Prefer explicit column lists over `select('*')`, especially on joins or list endpoints.
- Never interpolate untrusted input into SQL strings. Prefer query bindings or builder methods.
- Wrap multi-table writes, state transitions, and side-effect-heavy flows in `Db::transaction(...)`.
- Use row locking or equivalent transaction discipline when correctness depends on current row state.
- MySQL 8 features such as CTEs, window functions, and JSON helpers are allowed when they reduce PHP-side complexity and the query plan is still defensible.
- Do not rely on database strict mode to catch bad data for you. Validate types, lengths, decimals, and required fields in PHP or business logic first.
- Do not assume `utf8mb4` semantics in new text flows unless the config or schema change is part of the task.

## Review Checklist

1. Did new PHP code add types where the shape is already known?
2. Did the change use PHP 8 features that are compatible with the declared runtime floor?
3. Did new service wiring follow the repo's existing `#[Inject]` pattern where appropriate?
4. Did new queries avoid unsafe interpolation and broad `select('*')` usage?
5. Did multi-step writes get transaction boundaries?
6. Did the code rely on strict SQL mode, implicit casts, or charset assumptions that the current config does not guarantee?

## Good Fits

- Add or refactor PHP service, business, model, or interface code in `backend`, `service`, or `base`
- Review whether a change matches PHP 8 style and compatibility expectations
- Write or review MySQL-backed data access, joins, aggregates, and transactions
- Decide whether to use Query Builder or raw SQL for a new feature
- Tighten DTO or VO typing and reduce weakly-typed arrays

## Notes

- Pair with `webman-php` for framework-level Webman rules.
- Pair with `nzerp-local-mysql` when the task requires schema or data inspection.
- Pair with `webman-testing` when a data or transaction bug needs a local regression path.
