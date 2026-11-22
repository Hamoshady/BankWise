"""Add public account numbers without changing ledger keys or monetary values."""
from alembic import op
import sqlalchemy as sa

revision = '0011_account_numbers'
down_revision = '0010_onboarding'
branch_labels = depends_on = None


def upgrade():
    op.execute('CREATE SEQUENCE demo_account_number_seq AS bigint START WITH 100100000001 MAXVALUE 999999999999 NO CYCLE')
    op.add_column('demo_accounts', sa.Column('account_number', sa.String(12), nullable=True))
    op.execute("UPDATE demo_accounts SET account_number = nextval('demo_account_number_seq')::text")
    op.alter_column('demo_accounts', 'account_number', nullable=False,
                    server_default=sa.text("nextval('demo_account_number_seq')::text"))
    op.create_unique_constraint('uq_demo_account_number', 'demo_accounts', ['account_number'])
    op.create_check_constraint('ck_demo_account_number', 'demo_accounts', "account_number ~ '^[0-9]{12}$'")
    op.execute('ALTER SEQUENCE demo_account_number_seq OWNED BY demo_accounts.account_number')


def downgrade():
    op.drop_constraint('ck_demo_account_number', 'demo_accounts', type_='check')
    op.drop_constraint('uq_demo_account_number', 'demo_accounts', type_='unique')
    # Dropping the owning column also drops its sequence.
    op.drop_column('demo_accounts', 'account_number')
