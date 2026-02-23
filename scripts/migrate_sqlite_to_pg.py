#!/usr/bin/env python3
"""Migrate data from SQLite to PostgreSQL.

One-time migration script for existing Cube installations upgrading from SQLite.
Reads all tables from SQLite and inserts them into PostgreSQL.

Usage:
    python scripts/migrate_sqlite_to_pg.py \\
        --sqlite "sqlite:///data/vault.db" \\
        --pg "postgresql://vault:vault@localhost:5432/vault"

    # Dry run (shows row counts, no writes):
    python scripts/migrate_sqlite_to_pg.py \\
        --sqlite "sqlite:///data/vault.db" \\
        --pg "postgresql://vault:vault@localhost:5432/vault" \\
        --dry-run
"""

import argparse
import sqlite3
import sys

import psycopg2
from psycopg2.extras import execute_values


# Tables to migrate in dependency order (parents before children)
TABLES = [
    "users",
    "api_keys",
    "conversations",
    "messages",
    "training_jobs",
    "adapters",
    "eval_jobs",
    "audit_log",
    "system_config",
    "ldap_group_mappings",
    "quarantine_jobs",
    "quarantine_files",
    "update_jobs",
]

# Boolean columns that need "0"/"1" → True/False conversion
BOOL_COLUMNS = {
    "api_keys": {"is_active"},
    "conversations": {"archived"},
}

BATCH_SIZE = 500


def migrate_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn,
    table: str,
    dry_run: bool = False,
) -> int:
    """Migrate a single table from SQLite to PostgreSQL. Returns row count."""
    cursor = sqlite_conn.cursor()
    cursor.execute(f"SELECT * FROM {table}")  # noqa: S608
    rows = cursor.fetchall()

    if not rows:
        return 0

    # Get column names
    col_names = [desc[0] for desc in cursor.description]
    bool_cols = BOOL_COLUMNS.get(table, set())

    # Convert SQLite values to PostgreSQL-compatible types
    converted_rows = []
    for row in rows:
        new_row = []
        for col_name, value in zip(col_names, row):
            if col_name in bool_cols:
                # SQLite stores booleans as 0/1
                value = bool(int(value)) if value is not None else False
            new_row.append(value)
        converted_rows.append(tuple(new_row))

    if dry_run:
        return len(converted_rows)

    # Truncate target table
    pg_cursor = pg_conn.cursor()
    pg_cursor.execute(f"TRUNCATE TABLE {table} CASCADE")  # noqa: S608

    # Batch insert
    cols = ", ".join(col_names)
    template = "(" + ", ".join(["%s"] * len(col_names)) + ")"
    insert_sql = f"INSERT INTO {table} ({cols}) VALUES %s"  # noqa: S608

    for i in range(0, len(converted_rows), BATCH_SIZE):
        batch = converted_rows[i : i + BATCH_SIZE]
        execute_values(pg_cursor, insert_sql, batch, template=template)

    pg_conn.commit()
    return len(converted_rows)


def main():
    parser = argparse.ArgumentParser(description="Migrate Vault AI data from SQLite to PostgreSQL")
    parser.add_argument("--sqlite", required=True, help="SQLite URL (e.g. sqlite:///data/vault.db)")
    parser.add_argument("--pg", required=True, help="PostgreSQL URL (e.g. postgresql://vault:vault@localhost:5432/vault)")
    parser.add_argument("--dry-run", action="store_true", help="Show row counts without writing")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    # Parse SQLite path from URL
    sqlite_path = args.sqlite
    if sqlite_path.startswith("sqlite:///"):
        sqlite_path = sqlite_path[len("sqlite:///"):]
    elif sqlite_path.startswith("sqlite+aiosqlite:///"):
        sqlite_path = sqlite_path[len("sqlite+aiosqlite:///"):]

    # Parse PostgreSQL URL (strip async driver if present)
    pg_url = args.pg.replace("+asyncpg", "")

    print(f"Source:  {sqlite_path}")
    print(f"Target:  {pg_url}")
    print(f"Mode:    {'DRY RUN' if args.dry_run else 'LIVE MIGRATION'}")
    print()

    if not args.dry_run and not args.yes:
        confirm = input("This will TRUNCATE all target tables before inserting. Continue? [y/N] ")
        if confirm.lower() != "y":
            print("Aborted.")
            sys.exit(1)

    # Connect
    sqlite_conn = sqlite3.connect(sqlite_path)
    pg_conn = psycopg2.connect(pg_url)

    total = 0
    print(f"{'Table':<25} {'Rows':>8}")
    print("-" * 35)

    for table in TABLES:
        try:
            # Check if table exists in SQLite
            cursor = sqlite_conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            if not cursor.fetchone():
                print(f"{table:<25} {'(skip)':>8}  # not in SQLite")
                continue

            count = migrate_table(sqlite_conn, pg_conn, table, dry_run=args.dry_run)
            print(f"{table:<25} {count:>8}")
            total += count
        except Exception as e:
            print(f"{table:<25} {'ERROR':>8}  # {e}")
            if not args.dry_run:
                pg_conn.rollback()

    print("-" * 35)
    print(f"{'Total':<25} {total:>8}")

    sqlite_conn.close()
    pg_conn.close()

    if args.dry_run:
        print("\nDry run complete. No data was written.")
    else:
        print("\nMigration complete. Verify row counts match, then run:")
        print("  alembic stamp head  # to mark Alembic as current")


if __name__ == "__main__":
    main()
