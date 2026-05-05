from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_passthrough_profile"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite does not support ALTER COLUMN directly; use batch mode to recreate
    with op.batch_alter_table("profiles") as batch_op:
        batch_op.alter_column(
            "ffmpeg_args",
            existing_type=sa.JSON(),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("profiles") as batch_op:
        batch_op.alter_column(
            "ffmpeg_args",
            existing_type=sa.JSON(),
            nullable=False,
        )
