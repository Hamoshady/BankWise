"""Initialize a fresh deployment; never copy the publisher's accounts/database."""
import os,subprocess,sys
from sqlalchemy import select
from bankwise.database.config import load_database_settings
from bankwise.database.session import Database
from bankwise.database.accounts import create_user
from bankwise.database.models import User
from bankwise.core.bootstrap import build_security
from bankwise.rag.import_policies import import_policies
subprocess.run([sys.executable,'-m','alembic','upgrade','head'],check=True)
runtime=build_security();db=Database(load_database_settings());db.ready()
with db.session() as session,session.begin():
    existing=session.scalar(select(User).where(User.username=='identity_officer'))
    if existing is None:
        user=create_user(session,'identity_officer',os.environ['BANKWISE_INITIAL_ADMIN_PASSWORD'],['identity_reviewer'],runtime.authorizer.policy)
        runtime.service.record_access('account.create_authorized',str(user.id))
    elif {r.role for r in existing.roles}!={'identity_reviewer'}:
        raise RuntimeError('Unexpected existing administrator roles')
import_policies(db,runtime,__import__('pathlib').Path('/policies'))
print('Fresh deployment ready. Administrator: identity_officer. Password is in your local .env file.')
