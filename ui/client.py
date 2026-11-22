"""Session-local API client: no stored passwords, shared token cache or redirects."""
import os
import httpx


class APIError(Exception):
    def __init__(self, status, message, *, code=None, reason=None):
        self.status = status
        self.code = code
        self.reason = reason
        super().__init__(message)


OTP_MESSAGES = {
    'invalid_otp': 'رمز التحقق غير صحيح. حاول مرة تانية.',
    'expired': 'انتهت صلاحية الرمز. اطلب رمزًا جديدًا.',
    'exhausted': 'استهلكت محاولات التحقق. ابدأ تسجيل الدخول من جديد.',
    'challenge_consumed': 'تم استخدام هذا الرمز بالفعل. سجّل الدخول من جديد.',
    'invalid_challenge': 'انتهت صلاحية طلب التحقق. ابدأ تسجيل الدخول من جديد.',
    'superseded': 'تم استبدال هذا الرمز برمز أحدث. استخدم أحدث رمز.',
    'blocked': 'تم إيقاف طلب التحقق. ابدأ تسجيل الدخول من جديد.',
}


MESSAGES = {
    401: 'بيانات الدخول غير صحيحة أو الجلسة انتهت. سجّل الدخول مرة تانية.',
    403: 'هذه الخدمة تحتاج تفعيلًا لحسابك أو مراجعة حالة الهوية.',
    404: 'البيانات غير متاحة للحساب ده، أو كود التجربة لم يعد موجودًا.',
    409: 'حالة العملية لا تسمح بالتنفيذ. راجع الرصيد والحدود، وأكّد أو ألغِ الطلب المعلق.',
    413: 'حجم الملف أكبر من الحد المسموح.',
    422: 'راجع البيانات المدخلة: المبلغ ومعرّفات الحسابات والحقول المطلوبة.',
    429: 'وصلت لحد الطلبات المؤقت. انتظر دقيقة وجرب تاني.',
    500: 'حصل خطأ في الخدمة. لم نتمكن من تأكيد النتيجة؛ راجع حالة الطلب قبل إعادة تنفيذه.',
    503: 'الخدمة غير متاحة حاليًا. حاول مرة أخرى بعد قليل.',
}


class Client:
    def __init__(self, token=None):
        self.token = token
        self.base = os.environ.get('BANKWISE_API_URL', 'http://127.0.0.1:8000').rstrip('/')

    def request(self, method, path, *, payload=None, files=None, headers=None, binary=False):
        auth = {'Authorization': 'Bearer ' + self.token} if self.token else {}
        try:
            with httpx.Client(timeout=180, follow_redirects=False, trust_env=False) as client:
                response = client.request(method, self.base + path, json=payload, files=files,
                                          headers={**auth, **(headers or {})})
        except httpx.RequestError:
            raise APIError(0, 'الاتصال انقطع أو انتهت المهلة. راجع حالة العملية قبل إنشاء طلب جديد.') from None
        if response.status_code >= 300:
            body = response.json() if response.content and response.headers.get('content-type', '').startswith('application/json') else {}
            error = body.get('error', {}) if isinstance(body, dict) else {}
            reason = error.get('reason') if isinstance(error, dict) else None
            code = error.get('code') if isinstance(error, dict) else None
            message = OTP_MESSAGES.get(reason) or MESSAGES.get(response.status_code, 'تعذر إتمام الطلب.')
            raise APIError(response.status_code, message, code=code, reason=reason)
        if binary:
            return response.content
        return response.json() if response.content else None

    def chat(self, message, *, tool=None, arguments=None, conversation_id=None):
        body = {'message': message}
        if tool:
            body['tool'] = tool
        if arguments:
            body['arguments'] = arguments
        if conversation_id:
            body['conversation_id'] = conversation_id
        return self.request('POST', '/api/v1/assistant/chat', payload=body)
