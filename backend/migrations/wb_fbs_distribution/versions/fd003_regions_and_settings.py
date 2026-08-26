import sqlalchemy as sa
from alembic import op

from backend.modules.wb_fbs_distribution.domain import DEFAULT_REGIONS

revision = "fd003"
down_revision = "fd002"
branch_labels = None
depends_on = None

SCHEMA = "wb_fbs_distribution"


def upgrade() -> None:
    op.add_column(
        "seller_warehouses",
        sa.Column("participates", sa.Boolean(), server_default=sa.true(), nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "seller_warehouses",
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        schema=SCHEMA,
    )

    regions = op.create_table(
        "regions",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("share_bp", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("share_bp BETWEEN 0 AND 10000", name="ck_wb_fbs_region_share"),
        schema=SCHEMA,
    )
    # Порядок направлений подтверждён бизнесом и заводится сразу; доли остаются
    # нулевыми, пока логист не назовёт проценты — считать по выдуманным нельзя.
    op.bulk_insert(
        regions,
        [
            {"code": code, "title": title, "position": position, "share_bp": 0}
            for position, (code, title) in enumerate(DEFAULT_REGIONS)
        ],
    )

    op.create_table(
        "office_regions",
        sa.Column("office_id", sa.BigInteger(), primary_key=True),
        sa.Column("region_code", sa.String(32), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_fbs_office_regions_region", "office_regions", ["region_code"], schema=SCHEMA)

    settings = op.create_table(
        "distribution_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reserve_units", sa.Integer(), server_default="20", nullable=False),
        sa.Column("priority_regions", sa.Integer(), server_default="3", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_wb_fbs_settings_single_row"),
        sa.CheckConstraint("reserve_units >= 0", name="ck_wb_fbs_settings_reserve"),
        sa.CheckConstraint("priority_regions >= 1", name="ck_wb_fbs_settings_priority"),
        schema=SCHEMA,
    )
    op.bulk_insert(settings, [{"id": 1, "reserve_units": 20, "priority_regions": 3}])


def downgrade() -> None:
    op.drop_table("distribution_settings", schema=SCHEMA)
    op.drop_table("office_regions", schema=SCHEMA)
    op.drop_table("regions", schema=SCHEMA)
    op.drop_column("seller_warehouses", "position", schema=SCHEMA)
    op.drop_column("seller_warehouses", "participates", schema=SCHEMA)
