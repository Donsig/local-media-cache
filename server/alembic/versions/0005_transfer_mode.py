from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_transfer_mode"
down_revision: str | None = "0004_pipeline_status_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "server_state",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "transfer_mode",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'running'"),
        ),
        sa.CheckConstraint("id = 1", name="ck_server_state_single_row"),
    )
    op.execute("INSERT OR IGNORE INTO server_state (id, transfer_mode) VALUES (1, 'running')")


def downgrade() -> None:
    op.drop_table("server_state")
