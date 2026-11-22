"""Loopback-only llama.cpp adapter; model text never becomes executable authority."""
import json
from urllib.parse import urlsplit
import httpx
from bankwise.core.errors import ConfigError, ServiceUnavailable

MODEL = 'bankwise-qwen3.5-4b-q4'

class LlamaClient:
    def __init__(self, url):
        parsed = urlsplit(url)
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {'', '/'}):
            raise ConfigError('llama.cpp must use an explicit loopback HTTP endpoint')
        self.url = url.rstrip('/')

    def check_ready(self):
        try:
            with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
                response = client.get(self.url + '/v1/models')
                response.raise_for_status()
                if MODEL not in [m['id'] for m in response.json()['data']]:
                    raise ValueError('unexpected model')
        except Exception:
            raise ConfigError('configured local llama.cpp model is not ready') from None

    def complete(self, messages, *, key, items, max_items=3, max_tokens=96):
        schema = {'type':'object','properties':{key:{'type':'array','items':items,
                  'minItems':1 if key=='tools' else 0,'maxItems':max_items}},
                  'required':[key],'additionalProperties':False}
        try:
            with httpx.Client(timeout=httpx.Timeout(120, connect=5), trust_env=False, follow_redirects=False) as client:
                response = client.post(self.url + '/v1/chat/completions', json={
                    'model':MODEL,'messages':messages,'temperature':0,'seed':42,
                    'max_tokens':max_tokens,'stream':False,
                    'chat_template_kwargs':{'enable_thinking':False},
                    'response_format':{'type':'json_schema','json_schema':{'name':'selection','strict':True,'schema':schema}}})
                response.raise_for_status()
                value = response.json()['choices'][0]
                if value.get('finish_reason') != 'stop': raise ValueError('incomplete model output')
                raw = value['message']['content']
                if not isinstance(raw,str) or len(raw)>10000: raise ValueError('invalid content')
                data=json.loads(raw)
                if not isinstance(data,dict) or set(data)!={key}: raise ValueError('invalid fields')
                return raw
        except Exception:
            raise ServiceUnavailable('local language model request failed') from None
