"""Read-only research samples. Never trusts a client/account ID without a grant."""
from datetime import date
from decimal import Decimal
from uuid import UUID
from sqlalchemy import select, func, case, extract
from bankwise.database.financial_schema import account, client, disp, trans, loan, grants, imports
from bankwise.core.errors import NotFound, InvalidInputError

CREDIT = ('PRIJEM',)
DEBIT = ('VYDAJ', 'VYBER')
LABELS = {'VKLAD':'إيداع نقدي', 'VYBER':'سحب نقدي', 'VYBER KARTOU':'سحب بالبطاقة',
          'PREVOD Z UCTU':'تحويل وارد', 'PREVOD NA UCET':'تحويل صادر'}


def money(value):
    return str(Decimal(value or 0).quantize(Decimal('.01')))


class HistoricalBanking:
    def __init__(self, session, owner):
        self.session, self.owner = session, UUID(str(owner))

    def accounts(self):
        query = select(account, disp.c.client_id).join(disp, disp.c.account_id==account.c.account_id).join(
            grants, grants.c.client_id==disp.c.client_id).where(grants.c.owner_id==self.owner).distinct().order_by(account.c.account_id)
        return [dict(r) for r in self.session.execute(query).mappings()]

    def read(self, tool='get_statement', args=None, *, start=None, end=None, offset=0):
        args = args or {}
        accounts = self.accounts()
        if not accounts: raise NotFound()
        selected = args.get('historical_account_id') or accounts[0]['account_id']
        if selected not in {a['account_id'] for a in accounts}: raise NotFound()
        if start and end and start > end: raise InvalidInputError()
        conditions = [trans.c.account_id==selected]
        if start: conditions.append(trans.c.date>=start)
        if end: conditions.append(trans.c.date<=end)
        if tool == 'get_last_withdrawals': conditions.append(trans.c.type.in_(DEBIT))
        if tool == 'get_last_deposits': conditions.append(trans.c.type.in_(CREDIT))
        query = select(trans).where(*conditions).order_by(trans.c.date.desc(),trans.c.trans_id.desc())
        rows = self.session.execute(query.offset(offset).limit(100)).mappings().all()
        credit = case((trans.c.type.in_(CREDIT), trans.c.amount), else_=0)
        debit = case((trans.c.type.in_(DEBIT), trans.c.amount), else_=0)
        totals = self.session.execute(select(func.count(),func.sum(credit),func.sum(debit),func.min(trans.c.date),func.max(trans.c.date)).where(*conditions)).one()
        monthly = self.session.execute(select(extract('year',trans.c.date).label('year'),extract('month',trans.c.date).label('month'),func.sum(credit).label('credit'),func.sum(debit).label('debit')).where(*conditions).group_by('year','month').order_by('year','month')).mappings()
        latest = self.session.execute(select(trans.c.balance,trans.c.date).where(trans.c.account_id==selected).order_by(trans.c.date.desc(),trans.c.trans_id.desc()).limit(1)).first()
        loans = self.session.execute(select(loan).where(loan.c.account_id==selected)).mappings()
        manifest = self.session.scalar(select(imports.c.manifest).where(imports.c.name=='berka')) or {}
        return {'dataset':'berka', 'currency':'CZK', 'historical':True, 'demo':False,
            'source_url':'https://relational.fel.cvut.cz/dataset/Financial',
            'notice':'عينة بحثية تاريخية من PKDD99 / CTU؛ ليست حسابات CIB أو حساباتك الشخصية. لا تنفذ عمليات مالية.',
            'accounts':[{'id':str(a['account_id']), 'client_id':a['client_id'], 'opened':a['date'].isoformat(), 'status':'historical'} for a in accounts],
            'selected_account_id':selected, 'latest_balance':money(latest[0]) if latest else None,
            'as_of':latest[1].isoformat() if latest else None,
            'transactions':[{'id':str(r['trans_id']),'created_at':r['date'].isoformat(),'direction':'credit' if r['type'] in CREDIT else 'debit' if r['type'] in DEBIT else 'unknown',
                'amount':money(r['amount']),'balance':money(r['balance']),'operation':LABELS.get(r['operation'],r['operation'] or 'غير محدد'),'category':r['k_symbol'] or 'غير مصنف'} for r in rows],
            'total_transactions':totals[0], 'incoming_total':money(totals[1]), 'outgoing_total':money(totals[2]),
            'period_start':str(totals[3]) if totals[3] else None, 'period_end':str(totals[4]) if totals[4] else None,
            'monthly':[{'month':f"{int(r['year'])}-{int(r['month']):02}",'credit':money(r['credit']),'debit':money(r['debit'])} for r in monthly],
            'loans':[{'id':r['loan_id'],'amount':money(r['amount']),'duration':r['duration'],'payment':money(r['payments']),'status':r['status'],'date':r['date'].isoformat()} for r in loans],
            'offset':offset,'limit':100, 'dataset_counts':{k:v['rows'] for k,v in manifest.get('tables',{}).items()}}
