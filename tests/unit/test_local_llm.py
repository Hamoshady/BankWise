import json
from types import SimpleNamespace
from threading import Lock
import httpx
import pytest
from bankwise.rag.local_llm import LlamaClient, MODEL
from bankwise.agents.planner import plan_with_llm
from bankwise.core.errors import ConfigError, ServiceUnavailable

@pytest.mark.parametrize('url',['https://remote.invalid','http://localhost:8081','http://127.0.0.1.evil:8081','http://user:secret@127.0.0.1:8081','http://127.0.0.1:8081/private','http://127.0.0.1:8081?url=external'])
def test_only_loopback_configuration_allowed(url):
    with pytest.raises(ConfigError):LlamaClient(url)


def transport(monkeypatch,handler):
    real=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))


def test_constrained_request_and_ready_model(monkeypatch):
    calls=[]
    def handler(request):
        calls.append(request)
        if request.method=='GET':return httpx.Response(200,json={'data':[{'id':MODEL}]})
        body=json.loads(request.content)
        assert body['chat_template_kwargs']['enable_thinking'] is False
        assert body['response_format']['json_schema']['schema']['additionalProperties'] is False
        assert body['model']==MODEL
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"tools":["get_statement"]}'}}]})
    transport(monkeypatch,handler)
    client=LlamaClient('http://127.0.0.1:8081');client.check_ready()
    runtime=SimpleNamespace(lock=Lock(),external_llm=client)
    assert plan_with_llm(runtime,'وريني حسابي')==['get_statement']
    assert not runtime.lock.locked()
    assert len(calls)==2


@pytest.mark.parametrize('response',[
    httpx.Response(302,headers={'Location':'https://remote.invalid'}),
    httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{"tools":["get_statement"]}'}}]}),
    httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"tools":["get_statement"],"execute":"sql"}'}}]}),
    httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'not json'}}]}),
])
def test_failed_or_untrusted_response_fails_closed(monkeypatch,response):
    requests=[]
    def handler(request):requests.append(request);return response
    transport(monkeypatch,handler)
    runtime=SimpleNamespace(lock=Lock(),external_llm=LlamaClient('http://127.0.0.1:8081'))
    with pytest.raises(ServiceUnavailable):plan_with_llm(runtime,'question')
    assert len(requests)==1 and not runtime.lock.locked()


def test_external_model_cannot_select_multiple_sensitive_actions():
    class BadModel:
        def complete(self,*args,**kwargs):return '{"tools":["open_account","execute_transfer"]}'
    runtime=SimpleNamespace(lock=Lock(),external_llm=BadModel())
    with pytest.raises(ServiceUnavailable):plan_with_llm(runtime,'question')
    assert not runtime.lock.locked()


def test_unexpected_ready_model_rejected(monkeypatch):
    transport(monkeypatch,lambda _:httpx.Response(200,json={'data':[{'id':'unknown'}]}))
    with pytest.raises(ConfigError):LlamaClient('http://127.0.0.1:8081').check_ready()
