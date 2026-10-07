# nzERP PHP8 MySQL8 Baseline

Load this file when working in:

- `/opt/homebrew/var/www/yesgooo/nzERP/backend`
- `/opt/homebrew/var/www/yesgooo/nzERP/service`
- `/opt/homebrew/var/www/yesgooo/nzERP/base`

## Runtime Baseline

- All three repos declare `php >=8.0` in `composer.json`.
- That means new shared code must be safe on PHP 8.0 unless deployment is explicitly confirmed higher.
- Do not assume PHP 8.1+ syntax is safe just because the local machine may support it.

## Existing PHP 8 Usage In The Repos

- `#[Inject]` attribute-based DI is already common in all three repos.
- Typed properties are already common in services, interfaces, and RO or VO classes.
- `match (...)` is already used in `backend` and `service`.
- `Db::transaction(...)` is already used in service and business flows.

So the direction is not "introduce PHP 8 style from scratch"; it is "apply it more consistently and avoid older loose patterns in new code".

## MySQL Baseline In Current Config

Across these repos, `config/database.php` currently uses MySQL settings like:

- `driver = mysql`
- `charset = utf8`
- `collation = utf8_unicode_ci`
- `strict = false`

Implications:

- Do not rely on strict SQL mode to reject invalid data.
- Validate numeric, enum-like, and required fields before the write.
- Be cautious about full emoji or `utf8mb4` assumptions in new schema or text features.
- If a task truly needs `utf8mb4` or stricter SQL behavior, treat that as an explicit config or migration change, not an invisible assumption.

## Data Access Patterns Already In Use

- Query Builder and model wrappers are the default path for most business queries.
- `Db::table(...)`, joins, `selectRaw(...)`, and `Db::raw(...)` already appear in real service code.
- Some older code still uses string-built SQL for setup or import flows.

Preferred direction for new code:

1. Use model or Query Builder first.
2. Use explicit selected columns.
3. Use transactions for multi-step mutations.
4. Use raw SQL only when it is clearly the better tool.

## Practical Language Guidance

- Prefer typed method signatures when the shape is known.
- Prefer typed public properties for RO or VO classes.
- Prefer `match` for branch-to-value mapping.
- Prefer nullsafe and null-coalescing operators when they genuinely simplify the code.
- Prefer constructor promotion for small classes you own, but keep using `#[Inject]` for container-managed services when matching repo style.

## Practical SQL Guidance

- Avoid `select('*')` on joined or public list queries unless the table is narrow and the shape is intentional.
- Avoid unsafe string interpolation in SQL.
- Use `Db::transaction(...)` for state changes that touch multiple tables or side effects.
- Use MySQL 8 features like CTEs or window functions when they reduce post-processing and remain understandable.
- Check performance-sensitive queries with indexes and, when needed, `EXPLAIN` rather than assuming MySQL 8 will optimize everything.

## Compatibility Guardrail

When in doubt:

- choose the PHP 8.0-compatible feature
- choose the clearer query-builder path
- choose explicit validation over implicit database behavior

That bias is safer for shared code across `backend`, `service`, and `base`.
