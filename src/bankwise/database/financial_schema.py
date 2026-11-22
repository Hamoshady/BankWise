"""Historical CTU schema, separate from the mutable EGP demo ledger."""
import sqlalchemy as sa
from bankwise.database.models import Base


def table(name, specs):
    columns = []
    for field, kind, reference in specs:
        typ = {'int': sa.Integer, 'text': sa.String(80), 'date': sa.Date, 'money': sa.Numeric(18, 2)}[kind]
        options = [sa.ForeignKey('berka_' + reference)] if reference else []
        columns.append(sa.Column(field, typ, *options, primary_key=field == name + '_id'))
    return sa.Table('berka_' + name, Base.metadata, *columns)


district = table('district', [('district_id', 'int', None)] + [(f'A{i}', 'text', None) for i in range(2, 17)])
client = table('client', [('client_id','int',None), ('gender','text',None), ('birth_date','date',None), ('district_id','int','district.district_id')])
account = table('account', [('account_id','int',None), ('district_id','int','district.district_id'), ('frequency','text',None), ('date','date',None)])
disp = table('disp', [('disp_id','int',None), ('client_id','int','client.client_id'), ('account_id','int','account.account_id'), ('type','text',None)])
card = table('card', [('card_id','int',None), ('disp_id','int','disp.disp_id'), ('type','text',None), ('issued','date',None)])
loan = table('loan', [('loan_id','int',None), ('account_id','int','account.account_id'), ('date','date',None), ('amount','money',None), ('duration','int',None), ('payments','money',None), ('status','text',None)])
order = table('order', [('order_id','int',None), ('account_id','int','account.account_id'), ('bank_to','text',None), ('account_to','int',None), ('amount','money',None), ('k_symbol','text',None)])
trans = table('trans', [('trans_id','int',None), ('account_id','int','account.account_id'), ('date','date',None), ('type','text',None), ('operation','text',None), ('amount','money',None), ('balance','money',None), ('k_symbol','text',None), ('bank','text',None), ('account','int',None)])
sa.Index('ix_berka_trans_account_date', trans.c.account_id, trans.c.date, trans.c.trans_id)
sa.Index('ix_berka_disp_client', disp.c.client_id)
imports = sa.Table('banking_dataset_imports', Base.metadata,
    sa.Column('name', sa.String(40), primary_key=True), sa.Column('manifest', sa.JSON, nullable=False))
# These grants are research sample assignments, not proof of ownership or KYC.
grants = sa.Table('berka_research_grants', Base.metadata,
    sa.Column('owner_id', sa.Uuid, sa.ForeignKey('users.id'), primary_key=True),
    sa.Column('client_id', sa.Integer, sa.ForeignKey('berka_client.client_id'), primary_key=True))
TABLES = {t.name.removeprefix('berka_'): t for t in (district, client, account, disp, card, loan, order, trans)}
