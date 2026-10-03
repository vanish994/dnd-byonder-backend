"""Strict source policy for the D&D 2024/5.5 rule knowledge search."""

STRICT_EDITION_SCOPE = ("2024", "2025")


def append_strict_source_policy(
    sql: str,
    args: list[object],
    requested_edition: str | None = None,
) -> tuple[str, list[object]]:
    """Add an allowlist and suppress non-canonical duplicate titles in SQL."""
    sql += " AND d.edition IN (?, ?)"
    args.extend(STRICT_EDITION_SCOPE)

    if requested_edition:
        sql += " AND d.edition = ?"
        args.append(requested_edition)

    sql += """
        AND (
            d.canonical_candidate = 1
            OR NOT EXISTS (
                SELECT 1
                FROM documents AS canonical
                WHERE canonical.title = d.title
                  AND canonical.edition = d.edition
                  AND canonical.canonical_candidate = 1
            )
        )
    """
    return sql, args
