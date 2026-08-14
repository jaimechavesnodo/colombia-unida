"""Guardas append-only (§7.3-17, -20, -52: prohibir UPDATE/DELETE).

El rol de aplicación es dueño de las tablas, así que un REVOKE no basta;
se usan triggers. El borrado físico solo procede vía retention_jobs con
política aprobada, deshabilitando el trigger en esa transacción
(SET session_replication_role = replica) bajo manifiesto auditado.

Revision ID: 0002_append_only
Revises: 36c910f133c5
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_append_only"
down_revision: str | None = "36c910f133c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES = [
    "audit_events",
    "case_status_history",
    "need_status_history",
    "access_events",
    "human_confirmations",
]


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION forbid_update_delete() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'tabla append-only: % no permite %', TG_TABLE_NAME, TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
            """
        )


def downgrade() -> None:
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table};")
    op.execute("DROP FUNCTION IF EXISTS forbid_update_delete();")
