"""SQLite handler — Tier 2: schema + sample rows."""
from __future__ import annotations
import sqlite3
import tempfile
import os

_MAX_ROWS = 5
_MAX_TABLES = 20


def extract_schema(raw_bytes: bytes) -> str | None:
    tmp = None
    try:
        # sqlite3 requires a real file path
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            f.write(raw_bytes)
            tmp = f.name
        conn = sqlite3.connect(tmp)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [r[0] for r in cursor.fetchall()]

        parts = [f"SQLite database — {len(tables)} table(s):\n"]
        for table in tables[:_MAX_TABLES]:
            cursor.execute(f'PRAGMA table_info("{table}")')  # noqa: S608
            cols = cursor.fetchall()
            col_names = [c[1] for c in cols]
            col_defs = [f"{c[1]} {c[2]}" for c in cols]
            parts.append(f"Table: {table}")
            parts.append("  Columns: " + ", ".join(col_defs))
            try:
                cursor.execute(f'SELECT * FROM "{table}" LIMIT {_MAX_ROWS}')  # noqa: S608
                rows = cursor.fetchall()
                if rows:
                    parts.append(f"  Sample rows ({len(rows)}):")
                    for row in rows:
                        parts.append("    " + str(dict(zip(col_names, row))))
            except Exception:
                pass
            parts.append("")
        conn.close()
        return "\n".join(parts)
    except Exception:
        return None
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except Exception:
                pass
