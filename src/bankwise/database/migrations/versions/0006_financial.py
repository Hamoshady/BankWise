"""Read-only historical financial data and explicit research sample grants."""
from alembic import op
import sqlalchemy as sa

revision = '0006_financial'
down_revision = '0005_assistant'
branch_labels = depends_on = None

# Frozen source schema. Currency remains CZK; no historical row becomes an EGP balance.
SPECS = {
 'district': [('district_id','int',None)] + [(f'A{i}','text',None) for i in range(2,17)],
 'client': [('client_id','int',None),('gender','text',None),('birth_date','date',None),('district_id','int','district.district_id')],
 'account': [('account_id','int',None),('district_id','int','district.district_id'),('frequency','text',None),('date','date',None)],
 'disp': [('disp_id','int',None),('client_id','int','client.client_id'),('account_id','int','account.account_id'),('type','text',None)],
 'card': [('card_id','int',None),('disp_id','int','disp.disp_id'),('type','text',None),('issued','date',None)],
 'loan': [('loan_id','int',None),('account_id','int','account.account_id'),('date','date',None),('amount','money',None),('duration','int',None),('payments','money',None),('status','text',None)],
 'order': [('order_id','int',None),('account_id','int','account.account_id'),('bank_to','text',None),('account_to','int',None),('amount','money',None),('k_symbol','text',None)],
 'trans': [('trans_id','int',None),('account_id','int','account.account_id'),('date','date',None),('type','text',None),('operation','text',None),('amount','money',None),('balance','money',None),('k_symbol','text',None),('bank','text',None),('account','int',None)]}


def upgrade():
    for name, specs in SPECS.items():
        columns = []
        for field, kind, reference in specs:
            typ = {'int':sa.Integer(), 'text':sa.String(80), 'date':sa.Date(), 'money':sa.Numeric(18,2)}[kind]
            columns.append(sa.Column(field, typ, *([sa.ForeignKey('berka_'+reference)] if reference else []), primary_key=field==name+'_id'))
        op.create_table('berka_'+name, *columns)
    op.create_index('ix_berka_trans_account_date','berka_trans',['account_id','date','trans_id'])
    op.create_index('ix_berka_disp_client','berka_disp',['client_id'])
    op.create_table('banking_dataset_imports',sa.Column('name',sa.String(40),primary_key=True),sa.Column('manifest',sa.JSON,nullable=False))
    op.create_table('berka_research_grants',sa.Column('owner_id',sa.Uuid(),sa.ForeignKey('users.id'),primary_key=True),sa.Column('client_id',sa.Integer(),sa.ForeignKey('berka_client.client_id'),primary_key=True))


def downgrade():
    op.drop_table('berka_research_grants')
    op.drop_table('banking_dataset_imports')
    for name in reversed(SPECS):
        op.drop_table('berka_'+name)
