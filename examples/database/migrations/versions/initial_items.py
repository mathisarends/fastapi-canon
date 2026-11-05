"""Create items."""

import sqlalchemy as sa
from alembic import op

__all__ = [  # smelt: ignore[SMT330] -- Alembic loads revision-module entrypoints.
    "downgrade",
    "upgrade",
]

revision = "20261102_items"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("items", sa.Column("name", sa.String(80), primary_key=True))


def downgrade() -> None:
    op.drop_table("items")
