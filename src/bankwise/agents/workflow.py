"""LangGraph specialists with a database-backed, owner-scoped confirmation gate.

Native superstep checkpoints are independently durable; financial confirmations remain atomic.
No LangGraph time-travel endpoint is exposed, and no raw prompts or OTPs are saved.
"""
from datetime import datetime, timedelta, timezone
import secrets
import re
from typing import TypedDict
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError
from sqlalchemy import select

from bankwise.agents.banking import DemoBanking, installments
from bankwise.agents.contracts import AGENTS, ALLOWED, GATED, REQUIRED, Arguments
from bankwise.core.errors import Conflict, InvalidInputError, NotFound, PermissionDenied, ServiceUnavailable
from bankwise.database.models import AssistantAudit, AssistantConversation, AssistantOperation
from bankwise.rag.arabic import normalize, terms

_hasher = PasswordHasher()


class State(TypedDict, total=False):
    tool: str | None
    arguments: dict
    queue: list[str]
    results: list[dict]
    status: str
    missing: list[str]
    operation: dict | None
    trace: list[str]
    planner: str


def route(message):
    """Conservative bilingual router; text routes read tools and proposes actions only."""
    tokens = set(terms(message))
    normalized = normalize(message)
    tools = []
    if tokens & {'كشف', 'سحب', 'ايداع', 'مصروفات', 'رصيد', 'balance', 'statement', 'withdrawals', 'deposits', 'spending', 'معاملات'}:
        if 'pdf' in tokens:
            tools.append('export_statement_pdf')
        elif tokens & {'سحب', 'withdrawals'}:
            tools.append('get_last_withdrawals')
        elif tokens & {'ايداع', 'deposits'}:
            tools.append('get_last_deposits')
        elif tokens & {'مصروفات', 'spending'}:
            tools.append('analyze_spending')
        else:
            tools.append('get_statement')
    # Deliberately avoid treating policy questions or negated imperatives as orders.
    question = bool(tokens & {'شروط', 'رسوم', 'requirements', 'policy', 'policies'}) or '?' in message or '؟' in message
    negative = any(word in normalized.split() for word in ('لا', 'مش', 'متعملش', "don't", 'not', 'never'))
    if not question and not negative:
        if any(word in normalized.split() for word in ('حول', 'احول', 'ابعت', 'transfer', 'send')):
            tools.append('execute_transfer')
        if tokens & {'فتح'}:
            tools.append('open_account')
        if tokens & {'اغلاق'}:
            tools.append('close_account')
        if tokens & {'قرض'} and any(word in normalized.split() for word in ('قدم', 'تقديم', 'apply', 'اطلب')):
            tools.append('create_loan_request')
    if tokens & {'قسط', 'اقساط', 'installment', 'installments'} and not question:
        tools.append('calc_installments')
    if tokens & {'اهليه', 'eligibility'}:
        tools.append('check_eligibility')
    if tokens & {'kyc', 'هويه'} and not question:
        tools.append('check_kyc_status')
    if tokens & {'قرض'} and not any(t in tools for t in ('create_loan_request', 'calc_installments', 'check_eligibility')):
        tools.append('search_knowledge_base')
    if not tools or question:
        tools.append('search_knowledge_base')
    unique = list(dict.fromkeys(tools))
    return [tool for tool in unique if tool not in GATED] + [tool for tool in unique if tool in GATED]


def followup_arguments(message, missing):
    """Only unambiguous numeric replies; never infer account ownership or approval."""
    value = normalize(message).strip()
    number = r'([0-9]+(?:\.[0-9]+)?)'
    if len(missing) == 1 and re.fullmatch(number, value):
        key = missing[0]
        if key in {'account_id', 'beneficiary_id'} and re.fullmatch(r'[0-9]{12}', value):
            return {key: value}
        if key in {'amount', 'months', 'annual_rate'}:
            return {key: int(value) if key == 'months' and value.isdigit() else value}
    values = {}
    for key, pattern in {'amount': number + r'\s*(?:جنيه|egp)',
                         'months': r'([0-9]+)\s*(?:شهر|شهور|months?)',
                         'annual_rate': number + r'\s*%'}.items():
        matches = re.findall(pattern, value)
        if key in missing and len(matches) == 1:
            values[key] = int(matches[0]) if key == 'months' else matches[0]
    return values


class AssistantWorkflow:
    def __init__(self, session, owner_id, *, demo, knowledge, audit, historical=None, planner=None, checkpointer_factory=None):
        self.session, self.owner = session, UUID(str(owner_id))
        self.demo, self.knowledge, self.audit = demo, knowledge, audit
        self.bank = DemoBanking(session, self.owner)
        self.historical = historical
        self.planner = planner
        self.checkpointer_factory = checkpointer_factory
        self.delivery = None  # Ephemeral demo code, delivered only after the commit.

    def conversation(self, conversation_id=None):
        if conversation_id is None:
            row = AssistantConversation(owner_id=self.owner, state={})
            self.session.add(row)
            self.session.flush()
            return row
        row = self.session.scalar(select(AssistantConversation).where(
            AssistantConversation.id == conversation_id,
            AssistantConversation.owner_id == self.owner).with_for_update())
        if row is None:
            raise NotFound()
        return row

    def record(self, conversation, event):
        self.audit('assistant.' + event, str(self.owner))
        self.session.add(AssistantAudit(conversation_id=conversation.id, event=event))

    def chat(self, body):
        conversation = self.conversation(body.conversation_id)
        old = conversation.state
        if old and old.get('dataset', 'demo') != body.dataset:
            raise InvalidInputError('start a new conversation when changing datasets')
        if old.get('status') == 'awaiting_confirmation':
            operation = self.session.get(AssistantOperation, UUID(old['operation']['id']))
            expiry = operation.expires_at.replace(tzinfo=timezone.utc) if operation.expires_at.tzinfo is None else operation.expires_at
            if operation.status == 'pending' and expiry > datetime.now(timezone.utc):
                raise Conflict()  # Confirm or cancel the frozen proposal before editing it.
            if operation.status == 'pending':
                operation.status = 'expired'
        tool = body.tool
        args = body.arguments.model_dump(mode='json', exclude_none=True)
        if old.get('status') == 'needs_input' and (tool is None or tool == old.get('tool')):
            tool = old['tool']
            inferred = followup_arguments(body.message, old.get('missing', []))
            try:
                args = Arguments.model_validate({**old.get('arguments', {}), **inferred, **args}).model_dump(mode='json', exclude_none=True)
            except ValidationError:
                raise InvalidInputError() from None

        def security(state):
            if '\x00' in body.message:
                raise InvalidInputError()
            return {'trace': ['security']}

        def supervisor(state):
            from bankwise.agents.planner import validate_plan
            if state.get('tool'):
                queue = [state['tool']]
                planner_mode = 'explicit_tool'
            else:
                if self.planner is None:
                    raise ServiceUnavailable('language supervisor unavailable')
                queue = validate_plan(self.planner(body.message))
                planner_mode = 'local_llm'
            if len(queue) == 1:
                inferred = followup_arguments(body.message, REQUIRED.get(queue[0], set()))
                values = Arguments.model_validate({**inferred, **state['arguments']}).model_dump(mode='json', exclude_none=True)
            else:
                values = state['arguments']
            if sum(t in GATED for t in queue) > 1:
                raise InvalidInputError('one sensitive operation per turn')
            if state['arguments'] and len(queue) != 1:
                raise InvalidInputError('select a single tool when providing arguments')
            return {'queue': queue, 'results': [], 'status': 'completed', 'missing': [], 'operation': None, 'trace': [*state['trace'], 'supervisor'], 'arguments': values, 'planner': planner_mode}

        def next_node(state):
            if state['status'] != 'completed' or not state['queue']:
                return 'confirmation_gate'
            return next(agent for agent, names in AGENTS.items() if state['queue'][0] in names)

        def specialist(state):
            selected = state['queue'][0]
            if body.dataset == 'berka' and selected not in AGENTS['info'] | {'search_knowledge_base'}:
                raise InvalidInputError('historical research data is read-only; use the demo workspace for actions')
            values = state['arguments']
            if body.dataset == 'demo' and 'historical_account_id' in values:
                raise InvalidInputError('historical identifiers cannot be used in demo operations')
            if body.dataset == 'berka' and 'account_id' in values:
                raise InvalidInputError('use historical_account_id for research data')
            if set(values) - ALLOWED[selected]:
                raise InvalidInputError()
            Arguments.model_validate(values)
            missing = sorted(REQUIRED.get(selected, set()) - values.keys())
            if missing:
                return {'tool': selected, 'status': 'needs_input', 'missing': missing}
            if selected == 'search_knowledge_base':
                result = self.knowledge(body.message, body.top_k)
            elif body.dataset == 'berka':
                if self.historical is None:
                    raise ServiceUnavailable()
                result = self.historical.read(selected, values)
                if selected == 'export_statement_pdf':
                    result = {'download_url': '/api/v1/assistant/history/statement.pdf?account_id=' + str(result['selected_account_id']), 'historical': True}
            elif selected == 'calc_installments':
                result = installments(values)
            else:
                if not self.demo:
                    raise ServiceUnavailable('banking demo is disabled')
                if selected in GATED:
                    self.bank.prepare(selected, values)
                    return {'tool': selected, 'status': 'prepared'}
                result = self.bank.read(selected, values)
                if selected == 'export_statement_pdf':
                    suffix = '?account_id=' + values['account_id'] if values.get('account_id') else ''
                    result = {'download_url': '/api/v1/assistant/statement.pdf' + suffix, 'demo': True}
            self.record(conversation, 'tool.' + selected)
            return {'queue': state['queue'][1:], 'results': [*state['results'], {'tool': selected, 'data': result}]}

        def gate(state):
            if state['status'] != 'prepared':
                return {'trace': [*state['trace'], 'confirmation_gate']}
            code = f'{secrets.randbelow(1_000_000):06d}'
            operation = AssistantOperation(conversation_id=conversation.id, owner_id=self.owner,
                tool=state['tool'], arguments=state['arguments'], otp_hash=_hasher.hash(code),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5), attempts=0, status='pending')
            self.session.add(operation)
            self.session.flush()
            self.delivery = (str(operation.id), str(self.owner), code)
            self.record(conversation, 'confirmation_requested')
            return {'trace': [*state['trace'], 'confirmation_gate'], 'status': 'awaiting_confirmation', 'operation': {
                'id': str(operation.id), 'tool': operation.tool, 'arguments': operation.arguments,
                'expires_at': operation.expires_at.isoformat(), 'demo': True}}

        def audited_specialist(agent):
            def run(state):
                update = specialist(state)
                self.record(conversation, 'node.' + agent)
                update['trace'] = [*state['trace'], agent]
                return update
            return run

        def final_response(state):
            return {'trace': [*state['trace'], 'final_response']}

        def audit_log(state):
            self.record(conversation, 'graph_completed')
            return {'trace': [*state['trace'], 'audit_log']}

        def checkpoint_state(state):
            self.record(conversation, 'graph_checkpoint')
            return {'trace': [*state['trace'], 'checkpointed_state']}

        graph = StateGraph(State)
        graph.add_node('security', security)
        graph.add_node('supervisor', supervisor)
        for agent in AGENTS:
            graph.add_node(agent, audited_specialist(agent))
            graph.add_conditional_edges(agent, next_node)
        graph.add_node('confirmation_gate', gate)
        graph.add_edge(START, 'security')
        graph.add_edge('security', 'supervisor')
        graph.add_conditional_edges('supervisor', next_node)
        graph.add_node('final_response', final_response)
        graph.add_node('audit_log', audit_log)
        graph.add_node('checkpointed_state', checkpoint_state)
        graph.add_edge('confirmation_gate', 'final_response')
        graph.add_edge('final_response', 'audit_log')
        graph.add_edge('audit_log', 'checkpointed_state')
        graph.add_edge('checkpointed_state', END)
        run_id = str(uuid4())
        saver = self.checkpointer_factory(conversation.id, run_id) if self.checkpointer_factory else None
        compiled = graph.compile(checkpointer=saver)
        result = compiled.invoke({'tool': tool, 'arguments': args},
            {'recursion_limit': 24, 'configurable': {'thread_id': run_id}}, **({'durability': 'sync'} if saver else {}))
        # Persist only continuation fields. Neither user text nor RAG excerpts become instructions.
        conversation.state = {key: result[key] for key in
                              ('tool', 'arguments', 'status', 'missing', 'operation', 'trace', 'planner')}
        conversation.state = {**conversation.state, 'dataset': body.dataset, 'run_id': run_id}
        self.record(conversation, 'checkpoint')
        return self.response(conversation, result['results'])

    @staticmethod
    def response(conversation, results=None):
        state = conversation.state
        status = state['status']
        answer = {'completed': 'تم تنفيذ الطلب.', 'needs_input': 'محتاج البيانات دي عشان أكمل: ' + ', '.join(state.get('missing', [])),
                  'awaiting_confirmation': 'راجع تفاصيل العملية التجريبية، وبعدها أكّد باستخدام OTP.',
                  'cancelled': 'تم إلغاء العملية.', 'expired': 'انتهت صلاحية التأكيد. ابدأ طلب جديد.',
                  'locked': 'تم إيقاف التأكيد بعد ٥ محاولات غير صحيحة.'}.get(status, 'راجع حالة الطلب.')
        results = results or []
        answers = [r['data']['answer'] for r in results if 'answer' in r['data']]
        if answers:
            answer = '\n\n'.join(answers) + ('\n' + answer if status != 'completed' else '')
        return {'conversation_id': str(conversation.id), 'status': status, 'answer': answer,
                'planner': state.get('planner'), 'run_id': state.get('run_id'), 'trace': state.get('trace', []), 'dataset': state.get('dataset', 'demo'), 'results': results, 'missing': state.get('missing', []), 'operation': state.get('operation')}

    def confirm(self, operation_id, body):
        if not self.demo:
            raise ServiceUnavailable()
        # Conversation then operation is the lock order used by every confirmation.
        stub = self.session.scalar(select(AssistantOperation).where(
            AssistantOperation.id == operation_id, AssistantOperation.owner_id == self.owner))
        if stub is None:
            raise NotFound()
        conversation = self.conversation(stub.conversation_id)
        operation = self.session.scalar(select(AssistantOperation).where(
            AssistantOperation.id == operation_id).with_for_update().execution_options(populate_existing=True))
        if operation.status == 'completed':
            self.record(conversation, 'confirmation_replayed')
            result = dict(operation.result or {})
            if operation.tool == 'execute_transfer':
                from bankwise.database.models import DemoTransaction
                transaction = self.session.scalar(select(DemoTransaction).where(DemoTransaction.operation_id == operation.id))
                if transaction is not None:
                    result['reference'] = transaction.reference
            return {'conversation_id': str(conversation.id), 'status': 'completed', 'result': result, 'demo': True}
        if operation.status != 'pending':
            raise Conflict()
        expiry = operation.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= datetime.now(timezone.utc):
            operation.status = 'expired'
        elif not body.approved:
            operation.status = 'cancelled'
        else:
            try:
                valid = bool(_hasher.verify(operation.otp_hash, body.otp))
            except VerificationError:
                valid = False
            if not valid:
                operation.attempts += 1
                if operation.attempts == 5:
                    operation.status = 'locked'
                self.record(conversation, 'otp_rejected')
                # Return a committed result instead of raising/rolling back the attempt counter.
                if operation.status == 'locked':
                    conversation.state = {**conversation.state, 'status': 'locked', 'operation': None}
                return {'conversation_id': str(conversation.id), 'status': 'locked' if operation.status == 'locked' else 'invalid_otp',
                        'attempts_remaining': 5 - operation.attempts, 'demo': True}
            self.record(conversation, 'execution_authorized')
            operation.result = self.bank.execute(operation.tool, operation.arguments, operation.id)
            operation.status = 'completed'
        self.record(conversation, operation.status)
        conversation.state = {**conversation.state, 'status': operation.status, 'operation': None, 'missing': []}
        return {'conversation_id': str(conversation.id), 'status': operation.status, 'result': operation.result, 'demo': True}
