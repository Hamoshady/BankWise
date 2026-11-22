"""Authenticated assistant API. The local OTP inbox exists only in explicit demo mode."""
from threading import Lock
from datetime import date
from fastapi import Query
from bankwise.agents.historical import HistoricalBanking
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from bankwise.agents.contracts import ChatInput, ConfirmationInput
from bankwise.agents.workflow import AssistantWorkflow
from bankwise.agents.planner import plan_with_llm
from bankwise.agents.checkpoints import DatabaseSaver
from sqlalchemy import select
from bankwise.database.graph_schema import checkpoints
from bankwise.database.models import AssistantAudit, AssistantConversation, DemoAccount, DemoCustomer, User
from bankwise.api.deps import bearer_scheme, get_security, get_session
from bankwise.api.routers.rag import QueryInput, principal_for, query
from bankwise.core.errors import NotFound, ServiceUnavailable

router = APIRouter(prefix='/api/v1/assistant', tags=['assistant'], dependencies=[Depends(bearer_scheme)])
principal_dependency = principal_for('assistant:use', 'rag_read')


class DemoInbox:
    """Bounded process-local test delivery. Restart loses codes, never ledger/checkpoints."""
    def __init__(self):
        self._codes = {}
        self._lock = Lock()

    def put(self, operation_id, owner, code):
        with self._lock:
            if len(self._codes) >= 1000:
                self._codes.pop(next(iter(self._codes)))
            self._codes[operation_id] = (owner, code)

    def get(self, operation_id, owner):
        with self._lock:
            value = self._codes.get(str(operation_id))
            if value is None or value[0] != owner:
                raise NotFound()
            return value[1]

    def remove(self, operation_id):
        with self._lock:
            self._codes.pop(str(operation_id), None)


def service(request, session, principal, security):
    def knowledge(message, top_k):
        return query(QueryInput(query=message, top_k=top_k), request, principal, session, security).model_dump()
    return AssistantWorkflow(session, principal.subject_id, demo=request.app.state.banking_demo,
                             knowledge=knowledge, audit=security.record_access, historical=HistoricalBanking(session, principal.subject_id),
        planner=getattr(request.app.state, 'supervisor_planner', None) or (lambda message: plan_with_llm(request.app.state.semantic, message)),
        checkpointer_factory=lambda conversation, thread: DatabaseSaver(session.get_bind(), principal.subject_id, conversation, thread))


@router.post('/chat')
def chat(body: ChatInput, request: Request, principal=Depends(principal_dependency),
         session=Depends(get_session), security=Depends(get_security)):
    workflow = service(request, session, principal, security)
    result = workflow.chat(body)
    if result.get('planner') == 'local_llm':
        result['model'] = ('Qwen3.5-4B · Q4_K_M · llama.cpp' if getattr(request.app.state.semantic, 'external_llm', None) is not None else 'Qwen2.5-1.5B-Instruct')
    session.commit()
    if workflow.delivery:
        request.app.state.demo_inbox.put(*workflow.delivery)
    return result


@router.get('/conversations/{conversation_id}')
def checkpoint(conversation_id: UUID, request: Request, principal=Depends(principal_dependency),
               session=Depends(get_session), security=Depends(get_security)):
    workflow = service(request, session, principal, security)
    conversation = workflow.conversation(conversation_id)
    workflow.record(conversation, 'checkpoint_read')
    result = workflow.response(conversation)
    session.commit()
    return result


@router.post('/operations/{operation_id}/confirm')
def confirm(operation_id: UUID, body: ConfirmationInput, request: Request,
            principal=Depends(principal_dependency), session=Depends(get_session), security=Depends(get_security)):
    result = service(request, session, principal, security).confirm(operation_id, body)
    session.commit()
    if result['status'] != 'invalid_otp':
        request.app.state.demo_inbox.remove(operation_id)
    return result


@router.get('/demo/otp/{operation_id}')
def demo_otp(operation_id: UUID, request: Request, principal=Depends(principal_dependency),
             session=Depends(get_session), security=Depends(get_security)):
    if not request.app.state.banking_demo:
        raise NotFound()
    from bankwise.database.models import AssistantOperation
    operation = session.get(AssistantOperation, operation_id)
    if operation is None or str(operation.owner_id) != principal.subject_id or operation.status != 'pending':
        raise NotFound()
    security.record_access('assistant.demo_otp_read', principal.subject_id)
    return {'demo': True, 'otp': request.app.state.demo_inbox.get(operation_id, principal.subject_id)}


@router.get('/statement.pdf')
def pdf(request: Request, account_id: str | None = Query(None, pattern=r'^(?:[0-9]{12}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$'), principal=Depends(principal_dependency),
        session=Depends(get_session), security=Depends(get_security)):
    if not request.app.state.banking_demo:
        raise ServiceUnavailable()
    from bankwise.agents.banking import DemoBanking
    from bankwise.agents.pdf import statement_pdf
    statement = DemoBanking(session, principal.subject_id).read('get_statement',
                {'account_id': str(account_id)} if account_id else {})
    security.record_access('assistant.statement_export', principal.subject_id)
    return Response(statement_pdf(statement), media_type='application/pdf',
                    headers={'Content-Disposition': 'attachment; filename="demo-statement.pdf"'})


@router.get('/history')
def history(account_id: int | None = Query(None, gt=0), start: date | None = None, end: date | None = None,
            offset: int = Query(0, ge=0, le=100000), principal=Depends(principal_dependency),
            session=Depends(get_session), security=Depends(get_security)):
    result = HistoricalBanking(session, principal.subject_id).read(args={'historical_account_id': account_id}, start=start, end=end, offset=offset)
    security.record_access('assistant.history_read', principal.subject_id)
    return result


@router.get('/history/statement.pdf')
def historical_pdf(account_id: int = Query(..., gt=0), principal=Depends(principal_dependency),
                   session=Depends(get_session), security=Depends(get_security)):
    from bankwise.agents.pdf import statement_pdf
    result = HistoricalBanking(session, principal.subject_id).read(args={'historical_account_id':account_id})
    security.record_access('assistant.history_export', principal.subject_id)
    return Response(statement_pdf(result), media_type='application/pdf', headers={'Content-Disposition':'attachment; filename="berka-research-statement.pdf"'})


@router.get('/audit')
def audit_events(offset: int = Query(0, ge=0, le=100000), limit: int = Query(50, ge=1, le=100),
                 conversation_id: UUID | None = None, principal=Depends(principal_dependency),
                 session=Depends(get_session)):
    query = select(AssistantAudit).join(AssistantConversation).where(AssistantConversation.owner_id==UUID(principal.subject_id))
    if conversation_id: query=query.where(AssistantAudit.conversation_id==conversation_id)
    rows=session.scalars(query.order_by(AssistantAudit.created_at.desc(),AssistantAudit.id.desc()).offset(offset).limit(limit)).all()
    return {'events':[{'id':str(r.id),'conversation_id':str(r.conversation_id),'event':r.event,'created_at':r.created_at.isoformat()} for r in rows], 'offset':offset, 'limit':limit}


@router.get('/checkpoints')
def node_checkpoints(conversation_id: UUID | None = None, offset: int = Query(0, ge=0, le=100000),
                     principal=Depends(principal_dependency),session=Depends(get_session)):
    query=select(checkpoints).where(checkpoints.c.owner_id==UUID(principal.subject_id))
    if conversation_id: query=query.where(checkpoints.c.conversation_id==conversation_id)
    rows=session.execute(query.order_by(checkpoints.c.created_at.desc(),checkpoints.c.checkpoint_id.desc()).offset(offset).limit(100)).mappings().all()
    result=[]
    for row in rows:
        state=row['checkpoint'].get('channel_values',{})
        trace=state.get('trace',[])
        result.append({'id':row['checkpoint_id'],'run_id':row['thread_id'],'conversation_id':str(row['conversation_id']),
                       'step':row['meta'].get('step'), 'node':trace[-1] if trace else 'input', 'status':state.get('status','running'),
                       'planner':state.get('planner'), 'created_at':row['created_at'].isoformat()})
    return {'checkpoints':result, 'notice':'سجل مراحل المعالجة، وليس إثبات تنفيذ مالي. التنفيذ المؤكد يظهر في سجل العمليات.'}


@router.get('/recent-conversations')
def recent_conversations(principal=Depends(principal_dependency),session=Depends(get_session)):
    rows=session.scalars(select(AssistantConversation).where(AssistantConversation.owner_id==UUID(principal.subject_id)).order_by(AssistantConversation.updated_at.desc()).limit(30)).all()
    return {'conversations':[{'id':str(r.id),'status':r.state.get('status'), 'dataset':r.state.get('dataset','demo'),
        'updated_at':r.updated_at.isoformat()} for r in rows]}


@router.get('/demo/beneficiaries')
def beneficiaries(request: Request, principal=Depends(principal_dependency),session=Depends(get_session)):
    if not request.app.state.banking_demo: raise NotFound()
    rows=session.execute(select(DemoAccount.id,DemoAccount.account_number,User.username).join(User,User.id==DemoAccount.owner_id).join(
        DemoCustomer,DemoCustomer.owner_id==User.id).where(DemoAccount.status=='open',DemoCustomer.verified.is_(True),User.active.is_(True)).order_by(User.username,DemoAccount.id).limit(100)).all()
    return {'beneficiaries':[{'id':str(id),'account_number':number,'label':username + ' · حساب ' + number} for id,number,username in rows]}
