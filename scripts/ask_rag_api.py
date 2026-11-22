"""Interactive local RAG API client: python3 scripts/ask_rag_api.py."""
import getpass
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

BASE = 'http://127.0.0.1:8000'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward credentials to a redirect target.


def call(path, payload, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = Request(BASE + path, data=json.dumps(payload).encode(), headers=headers, method='POST')
    with build_opener(NoRedirect).open(request, timeout=180) as response:
        data = response.read()
    return json.loads(data) if data else None


def main():
    token = None
    try:
        username = input('اسم المستخدم: ').strip()
        password = getpass.getpass('كلمة المرور: ')
        result = call('/api/v1/auth/login', {'username': username, 'password': password})
        password = None
        token = result['access_token']
        print('اكتب سؤالك، أو exit للخروج. الإجابة قد تستغرق بعض الوقت.')
        while True:
            question = input('\nالسؤال: ').strip()
            if question.lower() in {'exit', 'quit'}:
                break
            if not question:
                continue
            try:
                result = call('/api/v1/rag/query', {'query': question, 'top_k': 5}, token)
            except HTTPError as error:
                print(f'تعذر تنفيذ السؤال (HTTP {error.code}). راجع الصلاحيات أو حدود الطلبات.')
                if error.code == 401:
                    break
                continue
            print('\n' + result['answer'])
            print('الوضع:', result['mode'])
            for index, citation in enumerate(result['citations'], 1):
                print(f"\n[{index}] {citation['source']}\n{citation['excerpt']}")
    except HTTPError as error:
        print(f'تعذر تسجيل الدخول (HTTP {error.code}). تحقق من الحساب والصلاحيات.')
        return 1
    except (URLError, TimeoutError):
        print('تعذر الاتصال أو انتهت مهلة الرد. راجع حالة خدمة الـAPI.')
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
