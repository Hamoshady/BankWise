"""Owner-scoped submissions and separate human-review authority for local accounts."""
import base64
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID
from typing import Literal
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.orm import defer
from starlette.concurrency import run_in_threadpool
from bankwise.api.deps import authorization, client_key, get_security, get_session, get_identity
from bankwise.api.routers.rag import principal_for
from bankwise.api.transport import parse_images
from bankwise.database.models import DemoCustomer, IdentityReview, User
from bankwise.core.errors import Conflict, NotFound, ServiceUnavailable, PermissionDenied

router=APIRouter(prefix='/api/v1/identity', tags=['identity review'])
customer=principal_for('assistant:use','rag_read')
reviewer=principal_for('identity:approve_account','identity_submit')

def cipher(request):
    key=getattr(request.app.state,'review_key',None)
    if not key: raise ServiceUnavailable('review storage unavailable')
    return Fernet(key.encode())

def clean_expired(session):
    session.execute(update(IdentityReview).where(IdentityReview.status=='pending',
        IdentityReview.expires_at<=datetime.now(timezone.utc)).values(status='rejected',reason='expired',encrypted_payload=None))

def metadata(row):
    return {'id':str(row.id),'status':row.status,'reason':row.reason,
            'created_at':row.created_at.isoformat(),'expires_at':row.expires_at.isoformat()}

@router.get('/status')
def status(principal=Depends(customer),session=Depends(get_session)):
    with session.begin():
        clean_expired(session)
        person=session.get(DemoCustomer,UUID(principal.subject_id))
        if not person: raise NotFound()
        row=session.scalar(select(IdentityReview).options(defer(IdentityReview.encrypted_payload)).where(IdentityReview.owner_id==person.owner_id).order_by(IdentityReview.created_at.desc()))
        return {'verified':person.verified,'review':metadata(row) if row else None}

@router.post('/reviews',status_code=201)
async def submit(request:Request, principal=Depends(customer),session=Depends(get_session),service=Depends(get_identity),security=Depends(get_security)):
    if not request.app.state.banking_demo: raise NotFound()
    codec=cipher(request)
    with session.begin():
        clean_expired(session)
        person=session.get(DemoCustomer,UUID(principal.subject_id))
        if not person: raise NotFound()
        if person.verified or session.scalar(select(IdentityReview.id).where(IdentityReview.owner_id==person.owner_id,IdentityReview.status=='pending')):
            raise Conflict()
    body=await request.body()
    settings=request.app.state.runtime.config.identity_processing
    images=[]
    def load():
        pair=parse_images(request.headers.get('content-type',''),body,settings.max_image_bytes)
        images.extend(pair)
        return pair
    result=await run_in_threadpool(service.extract_own_upload,authorization(request),client_key(request),load)
    payload=codec.encrypt(json.dumps({'result':result,'front':base64.b64encode(images[0]).decode(),
        'back':base64.b64encode(images[1]).decode()},ensure_ascii=False).encode())
    with session.begin():
        clean_expired(session)
        person=session.scalar(select(DemoCustomer).where(DemoCustomer.owner_id==UUID(principal.subject_id)).with_for_update())
        if not person: raise NotFound()
        if person.verified or session.scalar(select(IdentityReview.id).where(IdentityReview.owner_id==person.owner_id,IdentityReview.status=='pending')):
            raise Conflict()
        row=IdentityReview(owner_id=person.owner_id,encrypted_payload=payload,expires_at=datetime.now(timezone.utc)+timedelta(days=7))
        session.add(row);session.flush()
        security.record_access('identity.review_submitted',principal.subject_id)
        return metadata(row)

@router.get('/reviews')
def queue(principal=Depends(reviewer),session=Depends(get_session),security=Depends(get_security)):
    with session.begin():
        clean_expired(session)
        rows=session.execute(select(IdentityReview,User.username).options(defer(IdentityReview.encrypted_payload)).join(User,User.id==IdentityReview.owner_id)
            .where(IdentityReview.status=='pending').order_by(IdentityReview.created_at).limit(50)).all()
        security.record_access('identity.review_queue',principal.subject_id)
        return {'reviews':[{**metadata(r),'username':name} for r,name in rows]}

@router.get('/reviews/{review_id}')
def detail(review_id:UUID,request:Request,principal=Depends(reviewer),session=Depends(get_session),security=Depends(get_security)):
    with session.begin():
        row=session.scalar(select(IdentityReview).where(IdentityReview.id==review_id,IdentityReview.status=='pending',IdentityReview.expires_at>datetime.now(timezone.utc)))
        if not row: raise NotFound()
        if row.owner_id==UUID(principal.subject_id): raise PermissionDenied()
        security.record_access('identity.review_evidence_read',principal.subject_id)
        try: payload=json.loads(cipher(request).decrypt(row.encrypted_payload))
        except InvalidToken: raise ServiceUnavailable('review key mismatch') from None
        return {**metadata(row),**payload}

class Decision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    decision:Literal['approved','rejected']
    reason:Literal['checked','unclear_images','inconsistent_data','expired_card']

@router.post('/reviews/{review_id}/decision')
def decide(review_id:UUID,body:Decision,request:Request,principal=Depends(reviewer),session=Depends(get_session),security=Depends(get_security)):
    if not request.app.state.banking_demo: raise NotFound()
    with session.begin():
        row=session.scalar(select(IdentityReview).where(IdentityReview.id==review_id).with_for_update())
        if not row: raise NotFound()
        if row.owner_id==UUID(principal.subject_id): raise PermissionDenied()
        if row.status!='pending' or row.expires_at<=datetime.now(timezone.utc): raise Conflict()
        if (body.decision=='approved') != (body.reason=='checked'): raise Conflict()
        person=session.scalar(select(DemoCustomer).where(DemoCustomer.owner_id==row.owner_id).with_for_update())
        if not person: raise NotFound()
        security.record_access('identity.review_'+body.decision,principal.subject_id)
        row.status=body.decision;row.reason=body.reason;row.reviewer_id=UUID(principal.subject_id)
        row.decided_at=datetime.now(timezone.utc);row.encrypted_payload=None
        person.verified=body.decision=='approved'
        return {'status':row.status,'verified':person.verified}
