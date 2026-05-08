from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_pipeline_status_columns"
down_revision: str | None = "0003_assignment_progress"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("assignments") as batch_op:
        batch_op.add_column(sa.Column("bytes_downloaded_updated_at", sa.TIMESTAMP(), nullable=True))
        batch_op.add_column(sa.Column("last_confirm_error_at", sa.TIMESTAMP(), nullable=True))
        batch_op.add_column(sa.Column("last_confirm_error_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("assignments") as batch_op:
        batch_op.drop_column("last_confirm_error_reason")
        batch_op.drop_column("last_confirm_error_at")
        batch_op.drop_column("bytes_downloaded_updated_at")
