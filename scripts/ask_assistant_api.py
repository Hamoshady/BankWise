"""Local authenticated supervisor client: python3 scripts/ask_assistant_api.py."""
import getpass
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

BASE = 'http://127.0.0.1:8000'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def call(path, payload=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(BASE + path, data=data, headers=headers, method='POST' if data is not None else 'GET')
    with build_opener(NoRedirect).open(request, timeout=180) as response:
        body = response.read()
    return json.loads(body) if body else None


def main():
    token = conversation = operation = None
    try:
        username = input('اسم المستخدم: ').strip()
        password = getpass.getpass('كلمة المرور: ')
        token = call('/api/v1/auth/login', {'username': username, 'password': password})['access_token']
        password = None
        print('المساعد البنكي: الحسابات والتحويلات Demo فقط.')
        print('exit للخروج، /new لمحادثة جديدة، /otp للكود التجريبي، /confirm CODE للتأكيد، /cancel للإلغاء.')
        print('لتحديد أداة: /tool TOOL_NAME {"argument": "value"}')
        while True:
            message = input('\nأنت: ').strip()
            if message.lower() in {'exit', 'quit'}:
                break
            if not message:
                continue
            try:
                if message == '/new':
                    conversation = operation = None
                    continue
                if message == '/otp':
                    if not operation:
                        print('مفيش عملية معلقة في المحادثة دي.')
                        continue
                    result = call('/api/v1/assistant/demo/otp/' + operation, token=token)
                elif message == '/cancel' or message.startswith('/confirm '):
                    if not operation:
                        print('مفيش عملية معلقة في المحادثة دي.')
                        continue
                    result = call('/api/v1/assistant/operations/' + operation + '/confirm',
                        {'approved': message != '/cancel', 'otp': message.split(' ', 1)[1] if message != '/cancel' else ''}, token)
                    if result['status'] != 'invalid_otp':
                        operation = None
                else:
                    payload = {'message': message}
                    if conversation:
                        payload['conversation_id'] = conversation
                    if message.startswith('/tool '):
                        parts = message.split(' ', 2)
                        payload['tool'] = parts[1]
                        payload['arguments'] = json.loads(parts[2]) if len(parts) == 3 else {}
                    result = call('/api/v1/assistant/chat', payload, token)
                    conversation = result['conversation_id']
                    operation = result['operation']['id'] if result.get('operation') else None
                print(json.dumps(result, ensure_ascii=False, indent=2))
            except HTTPError as error:
                print(f'تعذر تنفيذ الطلب (HTTP {error.code}). راجع الصلاحيات والبيانات وحالة العملية.')
                if error.code == 401:
                    break
            except (ValueError, IndexError):
                print('صيغة الأمر غير صحيحة؛ استخدم JSON صالح للبيانات.')
    except HTTPError as error:
        print(f'تعذر تسجيل الدخول (HTTP {error.code}).')
        return 1
    except (URLError, TimeoutError):
        print('تعذر الاتصال. راجع حالة API؛ لا تعِد تأكيد عملية بمعرّف جديد عند فقد الرد.')
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nإنهاء الجلسة.')
    finally:
        if token:
            try:
                call('/api/v1/auth/logout', {}, token)
            except (HTTPError, URLError, TimeoutError):
                print('تعذر تأكيد إلغاء التوكن؛ سيظل خاضعًا لمدة صلاحيته.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
