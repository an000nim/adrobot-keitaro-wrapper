from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "creations",
        sa.Column("request_id", sa.String(36), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("alias", sa.String(64), nullable=False, unique=True),
        sa.Column("campaign_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "drafts",
        sa.Column("stream_id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("baseline", sa.JSON(), nullable=False),
        sa.Column("offers", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.create_index("ix_drafts_campaign_id", "drafts", ["campaign_id"])


def downgrade():
    op.drop_index("ix_drafts_campaign_id", table_name="drafts")
    op.drop_table("drafts")
    op.drop_table("creations")
