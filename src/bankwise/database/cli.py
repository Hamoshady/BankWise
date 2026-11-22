"""Operator commands. Run account/retention commands with the API stopped (single audit writer)."""
import argparse
from datetime import datetime, timezone
import getpass
import os
import re
from decimal import Decimal, InvalidOperation
import sys
from uuid import UUID

from sqlalchemy import delete, update, select
from sqlalchemy.exc import SQLAlchemyError

from bankwise.core.audit import AuditEvent, AuditOutcome
from bankwise.core.bootstrap import build_security
from bankwise.core.errors import BankWiseError, InvalidInputError
from bankwise.core.logging import correlation_scope
from bankwise.database.accounts import create_user
from bankwise.database.config import load_database_settings
from bankwise.database.models import IdentityRequest
from bankwise.database.session import Database


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user", help="privileged local provisioning; API must be stopped")
    create.add_argument("--username", required=True)
    create.add_argument("--roles", nargs="+", required=True)
    commands.add_parser("purge-expired", help="delete expired terminal request metadata; API must be stopped")
    recover = commands.add_parser("interrupt-request", help="mark one abandoned attempt; API must be stopped")
    recover.add_argument("request_id", type=UUID)
    phone = commands.add_parser("set-phone", help="enrol a previously verified mobile number for 2FA; API must be stopped")
    phone.add_argument("--username", required=True)
    phone.add_argument("--phone", required=True, help="E.164 number, e.g. +201234567890")
    seed = commands.add_parser('seed-demo', help='provision one simulated account per customer; API must be stopped')
    seed.add_argument('--username', required=True)
    seed.add_argument('--balance', default='10000.00', help='simulated EGP, 0 to 1000000')
    seed.add_argument('--verified', action='store_true', help='simulate approved KYC; never use as real KYC evidence')
    args = parser.parse_args(argv)
    database = None
    try:
        runtime = build_security()
        database = Database(load_database_settings())
        database.ready()
        with correlation_scope(), database.session() as session, session.begin():
            if args.command == "create-user":
                password = getpass.getpass("Password: ")
                if password != getpass.getpass("Confirm password: "):
                    raise InvalidInputError("password confirmation mismatch")
                user = create_user(session, args.username, password, args.roles, runtime.authorizer.policy)
                runtime.audit_sink.write(AuditEvent("account.create_authorized", AuditOutcome.SUCCESS,
                                                   actor_id=str(user.id)))
                message = f"User created: {user.id}"
            elif args.command == "set-phone":
                from bankwise.database.accounts import normalize_username
                from bankwise.database.models import User
                if not re.fullmatch(r"\+[1-9][0-9]{7,14}", args.phone):
                    raise InvalidInputError("phone must be an E.164 number")
                user = session.scalar(select(User).where(User.username == normalize_username(args.username)))
                if user is None or not user.active:
                    raise InvalidInputError("unknown or inactive user")
                user.phone_e164 = args.phone
                user.phone_verified_at = datetime.now(timezone.utc)
                runtime.audit_sink.write(AuditEvent("auth.phone_enrolled", AuditOutcome.SUCCESS,
                                                   actor_id=str(user.id)))
                message = f"Verified phone enrolled for {user.username}"
            elif args.command == 'seed-demo':
                from bankwise.core.identifiers import new_opaque_uuid
                from bankwise.database.accounts import normalize_username
                from bankwise.database.models import DemoAccount, DemoCustomer, DemoTransaction, User
                if os.environ.get('BANKWISE_BANKING_MODE') != 'demo':
                    raise InvalidInputError('demo mode must be explicitly enabled')
                try:
                    amount = Decimal(args.balance)
                    if not amount.is_finite() or not 0 <= amount <= 1_000_000 or amount != amount.quantize(Decimal('.01')):
                        raise ValueError()
                except (ValueError, InvalidOperation):
                    raise InvalidInputError() from None
                user = session.scalar(select(User).where(User.username == normalize_username(args.username)))
                if user is None or 'customer' not in {r.role for r in user.roles} or session.get(DemoCustomer, user.id):
                    raise InvalidInputError('requires an existing, not-yet-seeded customer')
                session.add(DemoCustomer(owner_id=user.id, verified=args.verified))
                account = DemoAccount(owner_id=user.id, balance=int(amount * 100), status='open')
                session.add(account)
                session.flush()
                if amount:
                    session.add(DemoTransaction(operation_id=new_opaque_uuid(), destination_id=account.id, amount=int(amount * 100)))
                runtime.audit_sink.write(AuditEvent('assistant.demo_seed_authorized', AuditOutcome.SUCCESS, actor_id=str(user.id)))
                message = f'DEMO account created: {account.id}'
            elif args.command == "purge-expired":
                runtime.audit_sink.write(AuditEvent("identity.purge_authorized", AuditOutcome.SUCCESS))
                count = session.execute(delete(IdentityRequest).where(
                    IdentityRequest.expires_at <= datetime.now(timezone.utc),
                    IdentityRequest.status != "processing")).rowcount
                message = f"Expired requests deleted: {count}"
            else:
                runtime.audit_sink.write(AuditEvent("identity.interrupt_authorized", AuditOutcome.SUCCESS,
                                                   metadata={"request": str(args.request_id)}))
                count = session.execute(update(IdentityRequest).where(
                    IdentityRequest.id == args.request_id, IdentityRequest.status == "processing"
                ).values(status="interrupted", error_code="internal_error",
                         completed_at=datetime.now(timezone.utc))).rowcount
                message = f"Abandoned requests marked interrupted: {count}"
        print(message)  # only after the transaction has committed
        return 0
    except (BankWiseError, SQLAlchemyError, OSError):
        print("Operation failed. Check configuration, database availability, input and audit storage.", file=sys.stderr)
        return 1
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
