"""Public transaction references; preserve all existing ledger values and keys."""
from alembic import op
import sqlalchemy as sa
revision = '0012_transaction_references'
down_revision = '0011_account_numbers'
branch_labels = depends_on = None


def upgrade():
    op.add_column('demo_transactions', sa.Column('reference', sa.String(24)))
    op.execute('''CREATE TABLE transaction_reference_counters (
        reference_date date PRIMARY KEY, last_value integer NOT NULL CHECK (last_value BETWEEN 1 AND 999999))''')
    op.execute('''INSERT INTO transaction_reference_counters
        SELECT (created_at AT TIME ZONE 'Africa/Cairo')::date, count(*)
        FROM demo_transactions GROUP BY 1''')
    op.execute('''WITH numbered AS (
        SELECT id, to_char(created_at AT TIME ZONE 'Africa/Cairo', 'YYYYMMDD') AS day,
        row_number() OVER (PARTITION BY (created_at AT TIME ZONE 'Africa/Cairo')::date ORDER BY created_at,id) AS n
        FROM demo_transactions)
        UPDATE demo_transactions t SET reference='BW-' || numbered.day || '-' || lpad(numbered.n::text,6,'0')
        FROM numbered WHERE numbered.id=t.id''')
    op.execute('''CREATE FUNCTION assign_transaction_reference() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE ref_day date; serial integer;
        BEGIN
            ref_day := (NEW.created_at AT TIME ZONE 'Africa/Cairo')::date;
            INSERT INTO transaction_reference_counters AS counters VALUES (ref_day,1)
            ON CONFLICT (reference_date) DO UPDATE SET last_value=counters.last_value+1
            RETURNING last_value INTO serial;
            NEW.reference := 'BW-' || to_char(ref_day,'YYYYMMDD') || '-' || lpad(serial::text,6,'0');
            RETURN NEW;
        END $$''')
    op.execute('''CREATE TRIGGER demo_transaction_reference BEFORE INSERT ON demo_transactions
        FOR EACH ROW EXECUTE FUNCTION assign_transaction_reference()''')
    op.alter_column('demo_transactions','reference',nullable=False)
    op.create_unique_constraint('uq_demo_transaction_reference','demo_transactions',['reference'])
    op.create_check_constraint('ck_demo_transaction_reference','demo_transactions',"reference ~ '^BW-[0-9]{8}-[0-9]{6}$'")


def downgrade():
    op.execute('DROP TRIGGER demo_transaction_reference ON demo_transactions')
    op.execute('DROP FUNCTION assign_transaction_reference()')
    op.drop_constraint('ck_demo_transaction_reference','demo_transactions',type_='check')
    op.drop_constraint('uq_demo_transaction_reference','demo_transactions',type_='unique')
    op.drop_column('demo_transactions','reference')
    op.drop_table('transaction_reference_counters')
