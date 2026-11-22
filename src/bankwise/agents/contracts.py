"""Allowlisted tools and typed arguments: model/retrieved text never grants authority."""
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Tool = Literal['get_statement', 'get_last_withdrawals', 'get_last_deposits', 'analyze_spending',
    'export_statement_pdf', 'execute_transfer', 'check_eligibility', 'calc_installments',
    'search_knowledge_base', 'create_loan_request', 'check_kyc_status', 'open_account',
    'check_closing_conditions', 'close_account']
GATED = {'execute_transfer', 'create_loan_request', 'open_account', 'close_account'}
AGENTS = {
    'info': {'get_statement', 'get_last_withdrawals', 'get_last_deposits', 'analyze_spending', 'export_statement_pdf'},
    'transfer': {'execute_transfer'},
    'loan': {'check_eligibility', 'calc_installments', 'create_loan_request', 'search_knowledge_base'},
    'account': {'check_kyc_status', 'open_account', 'check_closing_conditions', 'close_account'},
}
REQUIRED = {
    'execute_transfer': {'account_id', 'beneficiary_id', 'amount'},
    'create_loan_request': {'amount', 'months'},
    'calc_installments': {'amount', 'months', 'annual_rate'},
    'close_account': {'account_id'}, 'check_closing_conditions': {'account_id'},
}
ALLOWED = {tool: REQUIRED.get(tool, set()) for names in AGENTS.values() for tool in names}
for tool in AGENTS['info']:
    ALLOWED[tool] = {'account_id', 'historical_account_id'}


AccountReference = UUID | Annotated[str, Field(pattern=r'^[0-9]{12}$')]


class Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid')
    historical_account_id: int | None = Field(default=None, gt=0, strict=True)
    account_id: AccountReference | None = None
    beneficiary_id: AccountReference | None = None
    amount: Decimal | None = Field(default=None, gt=0, le=1_000_000, decimal_places=2, allow_inf_nan=False)
    months: int | None = Field(default=None, ge=1, le=360, strict=True)
    annual_rate: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=4, allow_inf_nan=False)


class ChatInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    dataset: Literal['demo', 'berka'] = 'demo'
    message: str = Field(min_length=1, max_length=1000)
    conversation_id: UUID | None = None
    tool: Tool | None = None
    arguments: Arguments = Field(default_factory=Arguments)
    top_k: int = Field(default=5, ge=1, le=10, strict=True)


class ConfirmationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    approved: bool = Field(strict=True)
    otp: str = Field(default='', pattern=r'^(?:[0-9]{6})?$')
