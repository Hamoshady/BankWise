"""Local EGP demo ledger. Never an adapter to real funds or real underwriting."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy import or_, select

from bankwise.core.errors import Conflict, NotFound, PermissionDenied
from bankwise.database.models import DemoAccount, DemoCustomer, DemoLoan, DemoTransaction, DemoDeposit


def money(minor):
    return str((Decimal(minor) / 100).quantize(Decimal('.01')))


class DemoBanking:
    DAILY_LIMIT = 5_000_000  # EGP 50,000; an explicit demo rule, not a bank policy.

    def __init__(self, session, owner_id):
        self.session, self.owner = session, UUID(str(owner_id))

    def verified(self):
        customer = self.session.get(DemoCustomer, self.owner)
        return bool(customer and customer.verified)

    def require_kyc(self):
        # Serialize all mutations for an owner (including daily-limit checks).
        customer = self.session.scalar(select(DemoCustomer).where(
            DemoCustomer.owner_id == self.owner).with_for_update())
        if customer is None or not customer.verified:
            raise PermissionDenied()

    def account(self, account_id, *, owned=True, lock=False):
        reference = str(account_id)
        condition = (DemoAccount.account_number == reference if len(reference) == 12 and reference.isascii() and reference.isdigit()
                     else DemoAccount.id == UUID(reference))
        query = select(DemoAccount).where(condition)
        if owned:
            query = query.where(DemoAccount.owner_id == self.owner)
        if lock:
            query = query.with_for_update()
        row = self.session.scalar(query)
        if row is None:
            raise NotFound()
        return row

    def prepare(self, tool, args):
        self.require_kyc()
        if tool == 'execute_transfer':
            source_id = self.account(args['account_id']).id
            dest_id = self.account(args['beneficiary_id'], owned=False).id
            if source_id == dest_id:
                raise Conflict()
            # Stable ordering avoids opposite-transfer account-lock deadlocks.
            rows = self.session.scalars(select(DemoAccount).where(
                DemoAccount.id.in_([source_id, dest_id])).order_by(DemoAccount.id).with_for_update()).all()
            accounts = {row.id: row for row in rows}
            source, destination = accounts.get(source_id), accounts.get(dest_id)
            if source is None or source.owner_id != self.owner or destination is None:
                raise NotFound()
            amount = int(Decimal(args['amount']) * 100)
            start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            sent = self.session.scalars(select(DemoTransaction).join(
                DemoAccount, DemoTransaction.source_id == DemoAccount.id).where(
                DemoAccount.owner_id == self.owner, DemoTransaction.created_at >= start)).all()
            if (source.status != 'open' or destination.status != 'open' or source.balance < amount
                    or sum(row.amount for row in sent) + amount > self.DAILY_LIMIT):
                raise Conflict()
        elif tool == 'close_account':
            account = self.account(args['account_id'], lock=True)
            outstanding = self.session.scalar(select(DemoLoan.id).where(
                DemoLoan.owner_id == self.owner, DemoLoan.status == 'pending_review').limit(1))
            if account.status != 'open' or account.balance != 0 or outstanding is not None:
                raise Conflict()
        elif tool == 'open_account':
            rows = self.session.scalars(select(DemoAccount).where(
                DemoAccount.owner_id == self.owner, DemoAccount.status == 'open')).all()
            if len(rows) >= 5:
                raise Conflict()
        elif tool != 'create_loan_request':
            raise PermissionDenied()

    def execute(self, tool, args, operation_id):
        # Always recheck at execution; balances/KYC/limits can change after the quote.
        self.prepare(tool, args)
        if tool == 'execute_transfer':
            source = self.account(args['account_id'])
            destination = self.account(args['beneficiary_id'], owned=False)
            amount = int(Decimal(args['amount']) * 100)
            source.balance -= amount
            destination.balance += amount
            record = DemoTransaction(operation_id=operation_id, source_id=source.id,
                                     destination_id=destination.id, amount=amount)
        elif tool == 'open_account':
            record = DemoAccount(owner_id=self.owner, balance=0, status='open')
        elif tool == 'close_account':
            record = self.account(args['account_id'])
            record.status = 'closed'
        else:
            record = DemoLoan(operation_id=operation_id, owner_id=self.owner, details=args, status='pending_review')
        self.session.add(record)
        self.session.flush()
        return {**({'account_number': record.account_number} if isinstance(record, DemoAccount) else {}),
                **({'reference': record.reference} if isinstance(record, DemoTransaction) else {}),
                'id': str(record.id), 'status': getattr(record, 'status', 'completed'), 'demo': True}

    def read(self, tool, args):
        if tool == 'check_kyc_status':
            return {'verified': self.verified(), 'demo': True}
        self.require_kyc()
        if tool == 'check_eligibility':
            return {'can_apply': True, 'approval': 'requires_manual_review', 'demo': True}
        if tool == 'check_closing_conditions':
            account = self.account(args['account_id'])
            loan = self.session.scalar(select(DemoLoan.id).where(
                DemoLoan.owner_id == self.owner, DemoLoan.status == 'pending_review').limit(1))
            return {'can_close': account.status == 'open' and account.balance == 0 and loan is None, 'demo': True}
        query = select(DemoAccount).where(DemoAccount.owner_id == self.owner).order_by(DemoAccount.id)
        accounts = [self.account(args['account_id'])] if args.get('account_id') else list(self.session.scalars(query))
        ids = {a.id for a in accounts}
        tx_query = select(DemoTransaction).where(or_(DemoTransaction.source_id.in_(ids),
                                                   DemoTransaction.destination_id.in_(ids)))
        if tool == 'get_last_withdrawals':
            tx_query = tx_query.where(DemoTransaction.source_id.in_(ids))
        elif tool == 'get_last_deposits':
            tx_query = tx_query.where(DemoTransaction.destination_id.in_(ids))
        transactions = self.session.scalars(tx_query.order_by(DemoTransaction.created_at.desc(),
                                                              DemoTransaction.id.desc()).limit(100)).all()
        deposit_ids = set(self.session.scalars(select(DemoDeposit.transaction_id).where(DemoDeposit.transaction_id.in_([t.id for t in transactions])))) if transactions else set()
        rows = [{'id': str(t.id), 'reference': t.reference, 'amount': money(t.amount),
                 'kind': 'demo_deposit' if t.id in deposit_ids else 'transfer',
                 'direction': 'internal' if t.source_id in ids and t.destination_id in ids else
                              'debit' if t.source_id in ids else 'credit',
                 'created_at': t.created_at.isoformat()} for t in transactions]
        return {'accounts': [{'id': str(a.id), 'account_number': a.account_number, 'balance': money(a.balance), 'status': a.status} for a in accounts],
                'transactions': rows, 'currency': 'EGP', 'demo': True, 'limit': 100,
                'outgoing_total': money(sum(t.amount for t in transactions if t.source_id in ids and t.destination_id not in ids))}


def installments(args):
    principal = Decimal(args['amount'])
    months = args['months']
    monthly_rate = Decimal(args['annual_rate']) / 1200
    payment = principal / months if not monthly_rate else principal * monthly_rate / (1 - (1 + monthly_rate) ** -months)
    return {'monthly_payment': str(payment.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)),
            'currency': 'EGP', 'illustrative_only': True, 'annual_rate': args['annual_rate'],
            'note': 'Reducing balance estimate using your supplied rate; excludes fees and insurance.'}
