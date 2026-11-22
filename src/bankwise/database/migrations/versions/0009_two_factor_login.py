"""Two-factor login: verified phone destination and single-use OTP challenges.

Existing users and rows are untouched: the phone columns are nullable and no backfill is performed. An operator
must enrol a phone out of band (see the ``set-phone`` CLI command) before that account can complete 2FA.
"""
from alembic import op
import sqlalchemy as sa

revision = '0009_two_factor_login'
down_revision = '0008_identity_reviews'
branch_labels = depends_on = None


def upgrade():
    op.add_column('users', sa.Column('phone_e164', sa.String(20), nullable=True))
    op.add_column('users', sa.Column('phone_verified_at', sa.DateTime(timezone=True), nullable=True))
    # A verified number belongs to one account only; multiple NULLs are allowed, so existing users are unaffected.
    op.create_index('uq_users_phone_e164', 'users', ['phone_e164'], unique=True)
    op.create_table('otp_challenges',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('flow_id', sa.Uuid(), nullable=False),
        sa.Column('phone_e164', sa.String(20), nullable=False),
        sa.Column('otp_hash', sa.String(512), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('pending', 'consumed', 'superseded', 'expired', 'blocked')",
                           name='ck_otp_challenge_status'),
        sa.CheckConstraint('attempts BETWEEN 0 AND 5', name='ck_otp_challenge_attempts'),
        sa.CheckConstraint('expires_at > created_at AND (consumed_at IS NULL OR consumed_at >= created_at)',
                           name='ck_otp_challenge_dates'))
    op.create_index('ix_otp_challenge_user_status', 'otp_challenges', ['user_id', 'status'])
    op.create_index('ix_otp_challenge_flow', 'otp_challenges', ['flow_id'])
    op.create_index('ix_otp_challenge_expires', 'otp_challenges', ['expires_at'])


def downgrade():
    op.drop_index('ix_otp_challenge_expires', table_name='otp_challenges')
    op.drop_index('ix_otp_challenge_flow', table_name='otp_challenges')
    op.drop_index('ix_otp_challenge_user_status', table_name='otp_challenges')
    op.drop_table('otp_challenges')
    op.drop_index('uq_users_phone_e164', table_name='users')
    op.drop_column('users', 'phone_verified_at')
    op.drop_column('users', 'phone_e164')
