"""BankWise local Streamlit workspace. All authority stays in the existing API."""
import base64
from decimal import Decimal
from html import escape
import json
from pathlib import Path
import sys
import time
from uuid import UUID, uuid4

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
from client import APIError, Client

st.set_page_config(page_title='BankWise | مساحة العميل', page_icon='◈', layout='wide', initial_sidebar_state='expanded')
st.markdown('<style>' + Path(__file__).with_name('style.css').read_text() + '</style>', unsafe_allow_html=True)
S = st.session_state
PAGES = ['نظرة عامة', 'المساعد الذكي', 'التحويلات', 'القروض', 'إدارة الحسابات', 'التحقق من الهوية', 'المعرفة والسياسات', 'البيانات البنكية الحقيقية', 'سجل العمليات']
LABELS = {'account_id': 'الحساب المصدر', 'beneficiary_id': 'حساب المستفيد', 'amount': 'المبلغ بالجنيه', 'months': 'مدة السداد بالشهور', 'annual_rate': 'الفائدة السنوية %'}
TOOLS = {'execute_transfer': 'تحويل مالي', 'open_account': 'فتح حساب', 'close_account': 'إغلاق حساب', 'create_loan_request': 'طلب قرض'}


def brand():
    st.markdown('<div class="brand"><div class="brand-mark">B</div><div><div class="brand-name">BankWise</div><div class="brand-sub">حسابك. بكل وضوح.</div></div></div>', unsafe_allow_html=True)


def heading(title, subtitle, tag='BANKWISE'):
    st.markdown(f'<div class="eyebrow">{escape(tag)}</div>', unsafe_allow_html=True)
    st.title(title)
    st.caption(subtitle)


def clear_session(message=None):
    for key in list(S):
        del S[key]
    if message:
        S.flash = message


def attempt(fn):
    try:
        return fn()
    except APIError as error:
        if error.status == 401 and S.get('token'):
            clear_session('الجلسة انتهت. سجّل الدخول للمتابعة.')
            st.rerun()
        st.error(str(error))
        return None


def api():
    return Client(S.get('token'))


def login():
    left, right = st.columns([1.15, 1], gap='large')
    with left:
        st.markdown('''<div class="login-intro"><span class="pill demo-pill">حسابك يبدأ من هنا</span>
        <div class="eyebrow">WELCOME TO BANKWISE</div><h1>كل تفاصيل حسابك.<br>في مكان واحد.</h1>
        <p>مساحة واضحة لإدارة حساباتك، مراجعة معاملاتك، وسؤال مساعدك البنكي بالعربي.</p>
        <div class="feature">◈ &nbsp; إجابات من السياسات، ومعاها المصادر</div>
        <div class="feature">↗ &nbsp; تحويلات بخطوة مراجعة وتأكيد</div>
        <div class="feature">▤ &nbsp; كشف حساب جاهز للتحميل</div></div>''', unsafe_allow_html=True)
    with right:
        with st.container(border=True):
            st.markdown('### أهلاً بيك في BankWise')
            st.caption('ادخل إلى مساحتك الشخصية بأمان.')
            if st.button('عندي حساب · تسجيل الدخول' if S.get('signup') else 'جديد هنا؟ إنشاء حساب'):
                S.signup = not S.get('signup', False)
                st.rerun()
            if S.get('signup'):
                signup_form()
                return
            with st.form('login', clear_on_submit=True):
                username = st.text_input('اسم المستخدم', placeholder='اسم المستخدم الخاص بك', max_chars=64)
                password = st.text_input('كلمة المرور', type='password', max_chars=256)
                submit = st.form_submit_button('دخول إلى حسابي  ←', type='primary', use_container_width=True)
            if submit:
                if not username.strip() or not password:
                    st.warning('اكتب اسم المستخدم وكلمة المرور.')
                else:
                    with st.spinner('جاري تسجيل الدخول…'):
                        result = attempt(lambda: Client().request('POST', '/api/v1/auth/login', payload={'username': username.strip(), 'password': password}))
                    if result:
                        # Step 1 succeeded: no token yet, only an opaque OTP challenge.
                        S.username = username.strip()
                        S.otp_development = result.get('development_otp', False)
                        S.otp_challenge = result['challenge_id']
                        S.otp_deadline = time.time() + result['expires_in']
                        S.otp_resend_at = time.time() + result['resend_after_seconds']
                        S.pop('otp_error', None)
                        st.rerun()
            st.caption('استخدم اسم المستخدم وكلمة المرور المخصصين لحسابك.')
        st.info('الأرصدة والتحويلات هنا تجريبية. رمز OTP المحلي لتجربة التأكيد فقط.')


def otp_screen():
    """Step 2: OTP challenge UI. The full token is stored only after the API verifies the code."""
    development = S.get('otp_development', False)
    delivery = 'وضع تجريبي — لم تُرسل رسالة SMS. اضغط زر OTP لعرض كود الاختبار.' if development else 'أرسلنا رمز التحقق إلى رقم الهاتف المسجّل والموثّق لحسابك.'
    left, right = st.columns([1.15, 1], gap='large')
    with left:
        st.markdown(f'''<div class="login-intro"><span class="pill demo-pill">خطوة حماية إضافية</span>
        <div class="eyebrow">TWO-FACTOR AUTHENTICATION</div><h1>رمز واحد فقط.<br>وأنت داخل حسابك.</h1>
        <p>{delivery} الرمز مكوّن من 6 أرقام وصالح لمدة 5 دقائق
        ولا يمكن استخدامه أكثر من مرة.</p>
        <div class="feature">◈ &nbsp; لا يمكن الوصول إلى حسابك قبل التحقق.</div>
        <div class="feature">↗ &nbsp; طلب رمز جديد يُبطل الرمز السابق.</div></div>''', unsafe_allow_html=True)
    with right:
        with st.container(border=True):
            st.markdown('### أدخل رمز التحقق')
            st.caption(delivery)
            remaining = max(0, int(S.get('otp_deadline', 0) - time.time()))
            if remaining <= 0:
                st.warning('انتهت صلاحية الرمز. اطلب رمزًا جديدًا أو ارجع إلى تسجيل الدخول.')
            else:
                st.markdown(f'<div class="pill">الوقت المتبقي: {remaining // 60:02d}:{remaining % 60:02d}</div>',
                            unsafe_allow_html=True)
            if S.get('otp_error'):
                st.error(S.pop('otp_error'))
            if development and st.button('OTP', disabled=remaining <= 0, use_container_width=True):
                result = attempt(lambda: Client().request('GET', '/api/v1/auth/dev/otp/' + S.otp_challenge))
                if result:
                    st.code(result['otp'], language=None)
            with st.form('otp_verify', clear_on_submit=True):
                code = st.text_input('رمز التحقق · 6 أرقام', max_chars=6)
                verify = st.form_submit_button('تأكيد وتسجيل الدخول  ←', type='primary', use_container_width=True)
            cooldown = max(0, int(S.get('otp_resend_at', 0) - time.time()))
            if st.button('إرسال رمز جديد', disabled=cooldown > 0, use_container_width=True):
                with st.spinner('جاري إرسال رمز جديد…'):
                    try:
                        result = Client().request('POST', '/api/v1/auth/login/otp/resend',
                                                  payload={'challenge_id': S.otp_challenge})
                    except APIError as error:
                        S.otp_error = str(error)
                    else:
                        S.otp_development = result.get('development_otp', False)
                        S.otp_challenge = result['challenge_id']
                        S.otp_deadline = time.time() + result['expires_in']
                        S.otp_resend_at = time.time() + result['resend_after_seconds']
                    st.rerun()
            if cooldown > 0:
                st.caption(f'يمكنك طلب رمز جديد بعد {cooldown} ثانية.')
            if st.button('العودة إلى تسجيل الدخول', use_container_width=True):
                for key in ('otp_challenge', 'otp_deadline', 'otp_resend_at', 'otp_error', 'otp_development'):
                    S.pop(key, None)
                st.rerun()
            if verify:
                if len(code) != 6 or not code.isascii() or not code.isdigit():
                    st.warning('اكتب رمز التحقق المكوّن من 6 أرقام.')
                elif remaining <= 0:
                    st.error('انتهت صلاحية الرمز. اطلب رمزًا جديدًا.')
                else:
                    with st.spinner('جاري التحقق…'):
                        try:
                            result = Client().request('POST', '/api/v1/auth/login/otp/verify',
                                                      payload={'challenge_id': S.otp_challenge, 'otp': code})
                        except APIError as error:
                            S.otp_error = str(error)
                            st.rerun()
                    # Claims only customize navigation; API verifies every request independently.
                    claims = json.loads(base64.urlsafe_b64decode(result['access_token'].split('.')[1] + '==='))
                    import onboarding
                    onboarding.reset()
                    S.token = result['access_token']
                    S.roles = claims.get('roles', [])
                    S.expires = time.time() + result['expires_in']
                    S.kyc_pending = result.get('kyc_pending', False)
                    S.page = 'مراجعة طلبات الهوية' if 'identity_reviewer' in S.roles else PAGES[5] if S.kyc_pending else PAGES[0] if 'customer' in S.roles else PAGES[5]
                    for key in ('otp_challenge', 'otp_deadline', 'otp_resend_at', 'otp_error', 'otp_development'):
                        S.pop(key, None)
                    st.rerun()


def signup_form():
    import onboarding
    onboarding.wizard()
    if st.button('العودة إلى تسجيل الدخول', key='signup_back_login'):
        onboarding.reset()
        S.signup = False
        st.rerun()


def go(page):
    S.page = page


def money(value):
    return f'{Decimal(str(value)):,.2f}'


def snapshot():
    if 'statement' not in S:
        result = attempt(lambda: api().chat('عرض كشف الحساب', tool='get_statement'))
        if result:
            S.statement = result['results'][0]['data']
    return S.get('statement', {'accounts': [], 'transactions': []})


def account_picker(label, key, *, only_open=True):
    accounts = snapshot()['accounts']
    rows = {a.get('account_number', a['id']): a for a in accounts if not only_open or a['status'] == 'open'}
    if not rows:
        st.info('لا توجد حسابات متاحة. راجع حالة هويتك أو افتح حسابًا من إدارة الحسابات.')
        return None
    return st.selectbox(label, list(rows), key=key,
        format_func=lambda value: f"حساب {value} · {money(rows[value]['balance'])} جنيه")


def transactions(data):
    rows = data.get('transactions', [])
    if not rows:
        st.info('لسه مفيش معاملات في الحساب ده. أي حركة جديدة هتظهر هنا.')
        return
    directions = {'debit': 'صادر', 'credit': 'وارد', 'internal': 'بين حساباتك'}
    st.dataframe([{'التاريخ · UTC': row['created_at'][:19].replace('T', ' '),
                   'نوع الحركة': 'إيداع تجريبي' if row.get('kind') == 'demo_deposit' else directions.get(row['direction'], row['direction']),
                   'المبلغ · EGP': money(row['amount']), 'رقم المعاملة': row.get('reference', 'حدّث البيانات')} for row in rows],
                 use_container_width=True, hide_index=True)
    st.caption('آخر 100 معاملة في حسابك.')


def results_view(response):
    if response.get('trace'):
        labels = {'security':'التحقق', 'supervisor':'المشرف', 'info':'المعلومات', 'loan':'القروض والمعرفة', 'transfer':'التحويل', 'account':'الحسابات', 'confirmation_gate':'بوابة التأكيد', 'final_response':'الإجابة', 'audit_log':'السجل', 'checkpointed_state':'حفظ الحالة'}
        with st.expander('كيف عالج المساعد طلبك؟'):
            st.caption('فهم الطلب بالموديل المحلي' if response.get('planner') == 'local_llm' else 'طلب مباشر من نموذج الخدمة')
            if response.get('model'): st.caption(response['model'])
            st.write(' ← '.join(labels.get(node, node) for node in response['trace']))
            chosen = {'get_statement':'كشف الحساب','get_last_withdrawals':'آخر السحوبات','get_last_deposits':'آخر الإيداعات','analyze_spending':'تحليل الإنفاق','export_statement_pdf':'كشف PDF','search_knowledge_base':'البحث في السياسات','check_eligibility':'أهلية القرض','calc_installments':'حساب القسط','check_kyc_status':'حالة الهوية','check_closing_conditions':'شروط إغلاق الحساب', **TOOLS}
            selected = [chosen.get(item['tool'], item['tool']) for item in response.get('results', [])]
            if selected: st.write('الخدمات المختارة: ' + '، '.join(selected))
            st.caption('مراحل التنفيذ محفوظة ويمكن مراجعتها من سجل العمليات.')
    if response.get('answer'):
        st.markdown(response['answer'])
    for item in response.get('results', []):
        data = item['data']
        if data.get('citations'):
            for index, source in enumerate(data['citations'], 1):
                with st.expander(f"المصدر {index} · {source['source']}"):
                    st.write(source['excerpt'])
                    if source.get('url'):
                        st.link_button('المصدر الرسمي لدى CIB ↗', source['url'])
                        st.caption(str(source.get('locator', '')) + ' · مراجعة: ' + str(source.get('reviewed_at', '')))
        elif data.get('historical') and 'transactions' in data:
            historical_result(data)
        elif 'accounts' in data:
            for account in data['accounts']:
                st.write(f"الحساب {account.get('account_number', account['id'])} — {money(account['balance'])} EGP")
            transactions(data)
        elif 'monthly_payment' in data:
            st.metric('القسط الشهري التقديري', money(data['monthly_payment']) + ' EGP')
            st.caption('تقدير بالسعر المدخل، بدون رسوم أو تأمين. ليس عرضًا ائتمانيًا.')
        elif 'verified' in data:
            st.info('حالة الهوية في التطبيق: معتمدة' if data['verified'] else 'حالة الهوية: بانتظار المراجعة')
        elif 'can_apply' in data:
            st.info('يمكن تقديم طلب؛ الموافقة النهائية تحتاج مراجعة.')
        elif 'can_close' in data:
            st.info('الحساب يستوفي شروط الإغلاق.' if data['can_close'] else 'الحساب لا يستوفي شروط الإغلاق: راجع الرصيد والقروض المعلقة.')
        elif 'download_url' in data:
            st.info('حمّل كشف الحساب من صفحة نظرة عامة.')


def submit_action(tool, arguments, message):
    if S.get('pending'):
        st.warning('راجع العملية المعلقة وأكّدها أو ألغِها الأول.')
        return
    with st.spinner('جاري فحص الطلب…'):
        response = attempt(lambda: api().chat(message, tool=tool, arguments=arguments))
    if response:
        S.last_result = response
        if response.get('operation'):
            S.pending = response['operation']
            S.pending_conversation = response['conversation_id']
        st.rerun()


def confirmation():
    operation = S.get('pending')
    if not operation:
        return
    with st.container(border=True):
        st.markdown('### خطوة أخيرة · راجع وأكّد')
        st.warning('بانتظار موافقتك — لم يتم التنفيذ بعد.')
        st.write('**' + TOOLS.get(operation['tool'], operation['tool']) + '**')
        for key, value in operation['arguments'].items():
            if key in {'account_id', 'beneficiary_id'}:
                labels = {ref: 'حسابك ' + a.get('account_number', a['id'])
                          for a in S.get('statement',{}).get('accounts', [])
                          for ref in (a['id'], a.get('account_number', a['id']))}
                labels.update({ref:a['label'] for a in S.get('beneficiaries',[])
                               for ref in (a['id'], a.get('account_number', a['id']))})
                value = labels.get(str(value), 'حساب ' + str(value) if len(str(value)) == 12 else 'حساب محفوظ')
            st.write(f"**{LABELS.get(key, key)}:** {value}")
        st.caption('رمز التأكيد صالح لمدة 5 دقائق. الإلغاء لا يغيّر الرصيد.')
        if st.button('عرض رمز التأكيد', key='show_otp'):
            result = attempt(lambda: api().request('GET', '/api/v1/assistant/demo/otp/' + operation['id']))
            if result:
                S.demo_code = result['otp']
        if S.get('demo_code'):
            st.code(S.demo_code, language=None)
            st.caption('رمز داخل التطبيق للاختبار، لا يُرسل برسالة نصية.')
        with st.form('confirmation_' + operation['id'], clear_on_submit=True):
            code = st.text_input('رمز التحقق · 6 أرقام', max_chars=6)
            approval = st.checkbox('راجعت التفاصيل وأوافق على التنفيذ')
            a, b = st.columns(2)
            confirm = a.form_submit_button('تأكيد التنفيذ', type='primary', use_container_width=True)
            cancel = b.form_submit_button('إلغاء العملية', use_container_width=True)
        if confirm or cancel:
            if confirm and (not approval or len(code) != 6 or not code.isascii() or not code.isdigit()):
                st.error('فعّل الموافقة واكتب رمز التحقق المكوّن من 6 أرقام.')
            else:
                result = attempt(lambda: api().request('POST', '/api/v1/assistant/operations/' + operation['id'] + '/confirm',
                    payload={'approved': bool(confirm), 'otp': code if confirm else ''}))
                if result:
                    status = result['status']
                    if status == 'invalid_otp':
                        st.error(f"الكود غير صحيح. باقي {result['attempts_remaining']} محاولة.")
                    else:
                        S.receipt = result
                        for key in ('pending', 'demo_code', 'statement', 'pdf', 'last_result'):
                            S.pop(key, None)
                        if S.get('conversation') == S.get('pending_conversation'):
                            S.pop('conversation', None)
                        st.rerun()
    st.divider()


def identity_status():
    result = attempt(lambda: api().request('GET', '/api/v1/identity/status'))
    if result:
        S.kyc_pending = not result['verified']
        S.identity_status = result
    return result


def review_status_view(status):
    if status.get('verified'):
        st.success('حسابك مفعّل. الخدمات المالية متاحة لك.')
        return
    review = status.get('review')
    if not review:
        st.info('حسابك جاهز، لكنه يحتاج مراجعة الهوية. ارفع صور البطاقة وأرسل طلب المراجعة.')
    elif review['status'] == 'pending':
        st.info('طلبك قيد المراجعة. سيظهر قرار المسؤول هنا؛ لا تحتاج إلى إنشاء حساب جديد.')
        st.caption('رقم الطلب: ' + review['id'])
    elif review['status'] == 'rejected':
        reasons = {'unclear_images':'الصور غير واضحة', 'inconsistent_data':'البيانات تحتاج مراجعة',
                   'expired_card':'البطاقة منتهية الصلاحية', 'expired':'انتهت مهلة الطلب'}
        st.warning('يحتاج طلبك إلى إعادة إرسال: ' + reasons.get(review['reason'], 'راجع الصور والبيانات'))


def dashboard():
    state = identity_status()
    if not state:
        return
    if not state['verified']:
        heading('أهلاً، ' + S.username.replace('_', ' '), 'حسابك الشخصي جاهز. ابدأ بقراءة بيانات بطاقتك.')
        review_status_view(state)
        if st.button('تحديث حالة الحساب'):
            S.pop('statement', None)
            st.rerun()
        st.button('رفع البطاقة', on_click=go, args=(PAGES[5],), type='primary')
        return
    heading('أهلاً، ' + S.username.replace('_', ' '), 'حساباتك، معاملاتك، وخدماتك في مكان واحد.')
    st.markdown('<div class="hero"><span class="pill">مساحتك البنكية الذكية</span><h2>ابدأ بسؤال أو اختار خدمة.</h2><p>حوّل إلى مستفيد، راجع آخر معاملاتك، أو ارفع بطاقتك لقراءة بياناتها. كل خدمة بخطوات واضحة.</p></div>', unsafe_allow_html=True)
    data = snapshot()
    if 'statement' not in S:
        st.info('تعذر تحميل الأرصدة. لن نعرض أرقامًا قبل استلام البيانات من الخدمة.')
        if st.button('إعادة محاولة تحميل البيانات'):
            st.rerun()
        return
    total = sum((Decimal(a['balance']) for a in data['accounts']), Decimal(0))
    a, b, c = st.columns([1.5, 1, 1])
    with a:
        st.markdown(f'<div class="balance"><small>إجمالي الرصيد المتاح</small><div class="amount">{money(total)} <span style="font-size:15px">EGP</span></div><small>من {len(data["accounts"])} حساب</small></div>', unsafe_allow_html=True)
    b.metric('الحسابات المفتوحة', sum(a['status'] == 'open' for a in data['accounts']))
    c.metric('المعاملات المعروضة', len(data['transactions']))
    st.markdown('### تحب تبدأ بإيه؟')
    a, b, c = st.columns(3)
    a.button('↗  تحويل جديد', use_container_width=True, on_click=go, args=(PAGES[2],))
    b.button('◈  اسأل المساعد', use_container_width=True, on_click=go, args=(PAGES[1],))
    c.button('▤  استكشف القروض', use_container_width=True, on_click=go, args=(PAGES[3],))
    st.button('استكشف البيانات البنكية الحقيقية ↗', use_container_width=True, on_click=go, args=(PAGES[7],))
    st.divider()
    a, b = st.columns([3, 1])
    a.markdown('### أحدث المعاملات')
    if b.button('تحديث البيانات', use_container_width=True):
        S.pop('statement', None)
        S.pop('pdf', None)
        st.rerun()
    transactions(data)
    if st.button('تجهيز كشف حساب PDF'):
        content = attempt(lambda: api().request('GET', '/api/v1/assistant/statement.pdf', binary=True))
        if content:
            S.pdf = content
    if S.get('pdf'):
        st.download_button('تحميل كشف الحساب', S.pdf, 'bankwise-demo-statement.pdf', 'application/pdf')


def chat_page():
    heading('مساعدك البنكي', 'اسأل بالعربي. الإجابات من السياسات بتظهر معاها المصادر.', 'YOUR BANKING COPILOT')
    dataset = st.selectbox('مصدر معاملات المحادثة', ['حسابات المحاكاة · EGP', 'عينة Berka التاريخية · CZK'], key='chat_source', disabled=bool(S.get('pending')))
    selected_dataset = 'berka' if 'Berka' in dataset else 'demo'
    if S.get('chat_dataset', 'demo') != selected_dataset:
        S.history = []
        S.pop('conversation', None)
        S.pop('missing', None)
    S.chat_dataset = selected_dataset
    S.setdefault('history', [])
    a, b = st.columns([4, 1])
    a.caption('اختار مصدر البيانات أولًا. المساعد يفهم طلبك بالعربي، ويطلب أي تفاصيل ناقصة.')
    if b.button('محادثة جديدة', disabled=bool(S.get('pending'))):
        S.history = []
        S.pop('conversation', None)
        S.pop('missing', None)
        st.rerun()
    if not S.history:
        starters = st.columns(3)
        for column, prompt in zip(starters, ['اعرض كشف حسابي', 'ما الحد اليومي لحساب بداية؟', 'عايز كشف حساب وشروط القرض']):
            if column.button(prompt, use_container_width=True, disabled=bool(S.get('pending'))):
                send_chat(prompt)
    for item in S.history:
        with st.chat_message(item['role'], avatar='🤖' if item['role'] == 'assistant' else None):
            if item['role'] == 'assistant':
                results_view(item['response'])
            else:
                st.write(item['text'])
    missing = S.get('missing', [])
    if 'beneficiary_id' in missing:
        load_beneficiaries()
    if missing and not S.get('pending'):
        with st.form('missing_details'):
            st.markdown('#### كمّل البيانات المطلوبة')
            values = {}
            for key in missing:
                if key == 'account_id':
                    values[key] = account_picker('اختار حسابك', 'chat_account')
                elif key == 'beneficiary_id':
                    values[key] = beneficiary_picker('المستفيد', 'chat_beneficiary')
                elif key == 'months':
                    values[key] = st.number_input(LABELS[key], min_value=1, max_value=360, value=12)
                else:
                    values[key] = st.text_input(LABELS.get(key, key))
            complete = st.form_submit_button('متابعة الطلب', type='primary')
        if complete:
            send_chat('استكمال البيانات المطلوبة', values)
    message = st.chat_input('اكتب سؤالك هنا…', disabled=bool(S.get('pending')), max_chars=1000)
    if message:
        send_chat(message)
    if S.get('conversation'):
        with st.expander('استكمال المحادثة لاحقًا'):
            st.caption('احتفظ بالمعرّف لو هتعيد فتح المتصفح. تاريخ الرسائل المعروض محفوظ في الجلسة الحالية فقط.')
            st.code(S.conversation, language=None)


def send_chat(message, arguments=None):
    with st.spinner('المساعد بيراجع طلبك…'):
        if S.get('chat_dataset') == 'berka':
            body = {'message': message, 'dataset': 'berka', 'arguments': arguments or {}}
            if S.get('conversation'): body['conversation_id'] = S.conversation
            response = attempt(lambda: api().request('POST', '/api/v1/assistant/chat', payload=body))
        else:
            response = attempt(lambda: api().chat(message, arguments=arguments, conversation_id=S.get('conversation')))
    if response:
        S.setdefault('history', []).extend([{'role': 'user', 'text': message}, {'role': 'assistant', 'response': response}])
        S.conversation = response['conversation_id']
        S.missing = response.get('missing', [])
        if response.get('operation'):
            S.pending = response['operation']
            S.pending_conversation = response['conversation_id']
        st.rerun()


def transfer_page():
    heading('حوّل بثقة', 'حدّد الحساب والمستفيد، وبعدها راجع التفاصيل قبل تأكيد OTP.', 'MOVE WITH CONFIDENCE')
    load_beneficiaries()
    st.caption('راجع الحساب والمستفيد والمبلغ. لن يتم الخصم قبل تأكيدك.')
    with st.form('transfer'):
        source = account_picker('من الحساب', 'transfer_source')
        destination = beneficiary_picker('إلى مين؟', 'transfer_beneficiary', exclude=source)
        amount = st.number_input('المبلغ · EGP', min_value=0.01, max_value=50000.0, value=100.0, step=10.0, format='%.2f')
        st.caption('الحد اليومي داخل التطبيق: 50,000 جنيه. التحويل بين حسابات التطبيق فقط.')
        submit = st.form_submit_button('مراجعة التحويل  ←', type='primary', disabled=bool(S.get('pending')) or not source, use_container_width=True)
    if submit:
        if not destination:
            st.error('اختار حساب مستفيد متاح.')
        else:
            submit_action('execute_transfer', {'account_id': source, 'beneficiary_id': destination, 'amount': f'{amount:.2f}'}, 'تجهيز تحويل تجريبي')


def loans_page():
    heading('خطّط لخطوتك الجاية', 'احسب قسطًا تقديريًا، أو قدّم طلب قرض للمراجعة.', 'PLAN AHEAD')
    first, second = st.tabs(['حاسبة الأقساط', 'طلب قرض'])
    with first:
        with st.form('calculator'):
            principal = st.number_input('مبلغ التمويل · EGP', min_value=1.0, max_value=1000000.0, value=50000.0)
            months = st.slider('مدة السداد بالشهور', 1, 360, 24)
            rate = st.number_input('الفائدة السنوية المفترضة %', min_value=0.0, max_value=100.0, value=15.0)
            calculate = st.form_submit_button('احسب القسط', type='primary', disabled=bool(S.get('pending')))
        if calculate:
            submit_action('calc_installments', {'amount': f'{principal:.2f}', 'months': months, 'annual_rate': str(rate)}, 'حساب القسط')
    with second:
        st.info('الطلب ينتقل للمراجعة فقط؛ لا توجد موافقة ائتمانية أو صرف أموال تلقائي.')
        with st.form('loan'):
            amount = st.number_input('المبلغ المطلوب · EGP', min_value=1.0, max_value=1000000.0, value=10000.0)
            months = st.number_input('عدد أشهر السداد', min_value=1, max_value=360, value=12)
            apply = st.form_submit_button('مراجعة طلب القرض', type='primary', disabled=bool(S.get('pending')))
        if apply:
            submit_action('create_loan_request', {'amount': f'{amount:.2f}', 'months': months}, 'تقديم طلب قرض تجريبي')


def accounts_page():
    heading('حساباتك تحت إيدك', 'تفاصيل واضحة لكل حساب، وفتح أو إغلاق بخطوة تأكيد.')
    for account in snapshot()['accounts']:
        with st.container(border=True):
            a, b = st.columns([2, 1])
            a.write('**حساب بالجنيه المصري** · ' + ('مفتوح' if account['status'] == 'open' else 'مغلق'))
            a.caption('رقم الحساب: ' + account.get('account_number', account['id']))
            b.metric('الرصيد', money(account['balance']) + ' EGP')
    a, b = st.columns(2)
    with a:
        st.markdown('### حساب جديد')
        st.caption('يبدأ برصيد صفر. حد أقصى 5 حسابات مفتوحة.')
        if st.button('طلب فتح حساب', disabled=bool(S.get('pending')), type='primary'):
            submit_action('open_account', {}, 'فتح حساب تجريبي')
    with b:
        st.markdown('### إغلاق حساب')
        with st.form('close_account'):
            account = account_picker('اختار الحساب', 'closing_account')
            st.caption('يتطلب رصيد صفر وعدم وجود طلب قرض معلّق.')
            close = st.form_submit_button('مراجعة طلب الإغلاق', disabled=bool(S.get('pending')) or not account)
        if close:
            submit_action('close_account', {'account_id': account}, 'إغلاق حساب تجريبي')


def identity_page():
    state = identity_status() if 'customer' in S.roles else None
    if state:
        review_status_view(state)
    if st.button('تحديث حالة الطلب'):
        S.pop('statement', None)
        st.rerun()
    heading('التحقق من الهوية', 'ارفع صورتي البطاقة لقراءة البيانات وفحص اكتمالها محليًا. هذه الخطوة لا تعتمد هويتك رسميًا أو تغيّر حالة حسابك تلقائيًا.')
    if 'identity_processor' not in S.roles:
        st.info('خدمة فحص البطاقة غير مفعّلة لهذا الحساب. اطلب تفعيل الخدمة من مسؤول التطبيق، أو ادخل بحسابك الشخصي المفعّل.')
        return
    with st.form('identity_upload'):
        a, b = st.columns(2)
        front = a.file_uploader('صورة الوجه الأمامي', type=['jpg', 'jpeg', 'png'])
        back = b.file_uploader('صورة الوجه الخلفي', type=['jpg', 'jpeg', 'png'])
        st.caption('صوّر البطاقة كاملة بإضاءة جيدة ومن غير انعكاسات. JPG أو PNG، بحد أقصى 5 ميجابايت لكل صورة.')
        submit = st.form_submit_button('فحص الهوية', type='primary')
        consent = st.checkbox('أوافق على إرسال الصور والقراءة للمسؤول لمراجعتها، وحفظها مشفّرة للمراجعة. تنتهي صلاحية الطلب بعد 7 أيام وتُحذف الأدلة عند اتخاذ القرار أو تنظيف الطلب المنتهي')
        send_review = st.form_submit_button('إرسال طلب مراجعة الهوية', disabled=bool(state and (state['verified'] or (state.get('review') or {}).get('status')=='pending')))
    if send_review:
        if not consent or not front or not back:
            st.warning('ارفع صورتي البطاقة ووافق على إرسالها للمراجعة.')
        else:
            with st.spinner('جاري قراءة البطاقة وإرسالها للمراجعة…'):
                sent = attempt(lambda: api().request('POST', '/api/v1/identity/reviews',
                    files={'front': ('front.'+front.name.split('.')[-1],front.getvalue(),front.type),
                           'back': ('back.'+back.name.split('.')[-1],back.getvalue(),back.type)}))
            if sent:
                S.pop('identity_result', None)
                st.rerun()
    if submit:
        if not front or not back:
            st.warning('ارفع الصورتين الأول.')
        elif max(front.size, back.size) > 5 * 1024 * 1024:
            st.error('كل صورة لازم تكون 5 MB أو أقل.')
        else:
            S.pop('identity_result', None)
            with st.spinner('جاري فحص الصور محليًا…'):
                result = attempt(lambda: api().request('POST', '/api/v1/identity/extract',
                    files={'front': ('front.' + front.name.split('.')[-1], front.getvalue(), front.type),
                           'back': ('back.' + back.name.split('.')[-1], back.getvalue(), back.type)}))
            if result:
                S.identity_result = result
    result = S.get('identity_result')
    if result:
        st.markdown('### البيانات المقروءة من بطاقتك')
        st.caption('راجع كل حقل مع البطاقة الأصلية. الحقل غير المقروء لن يتم تخمينه. الفحص وحده لا يحفظ الصور أو النص في قاعدة البيانات. الإرسال للمراجعة يتم فقط بموافقتك.')
        labels = {'first_name':'الاسم الأول', 'other_names':'باقي الاسم', 'address_line1':'العنوان',
                  'address_line2':'تكملة العنوان', 'national_id':'الرقم القومي', 'birth_date':'تاريخ الميلاد',
                  'gender':'النوع', 'job':'المهنة', 'religion':'الديانة', 'marital_status':'الحالة الاجتماعية',
                  'issue_date':'تاريخ الإصدار', 'expiry_date':'تاريخ الانتهاء'}
        fields = result.get('fields', {})
        st.dataframe([{'البيان': label, 'القراءة': fields.get(key) or 'لم تتم قراءته بوضوح'}
                      for key, label in labels.items()], hide_index=True, use_container_width=True)
        derived = result.get('derived', {}).get('birth_date_from_national_id')
        if derived:
            st.write('تاريخ الميلاد المستنتج من الرقم القومي: ' + derived)
            st.caption('هذه قيمة مشتقة من الرقم المقروء، وليست قراءة لتاريخ مطبوع مستقل.')
        decision = result.get('summary', {}).get('decision', {}).get('status')
        st.info({'VERIFIED':'اجتازت القراءة فحوص الاتساق؛ هذا ليس إثباتًا لأصالة البطاقة.',
                 'RETRY':'تحتاج الصور إلى إعادة تصوير أوضح.', 'REJECTED':'لم تجتز القراءة فحوص الاتساق.',
                 'MANUAL_REVIEW':'تحتاج النتيجة إلى مراجعة بشرية.'}.get(decision, 'راجع البيانات المقروءة قبل استخدامها.'))
        if st.button('مسح نتيجة البطاقة'):
            S.pop('identity_result', None)
            st.rerun()


def reviewer_page():
    heading('مراجعة طلبات الهوية', 'راجع الصور والبيانات قبل تفعيل خدمات الحساب داخل التطبيق.')
    if st.button('تحديث الطلبات'):
        S.pop('review_evidence', None)
        st.rerun()
    result = attempt(lambda: api().request('GET', '/api/v1/identity/reviews'))
    rows = (result or {}).get('reviews', [])
    if not rows:
        st.info('لا توجد طلبات بانتظار المراجعة.')
        return
    items = {r['id']: r for r in rows}
    selected = st.selectbox('اختار طلبًا', list(items), format_func=lambda key: items[key]['username']+' · '+items[key]['created_at'][:16])
    if st.button('عرض الصور والبيانات'):
        S.review_evidence = attempt(lambda: api().request('GET','/api/v1/identity/reviews/'+selected))
    evidence = S.get('review_evidence')
    if evidence and evidence['id'] == selected:
        a,b = st.columns(2)
        a.image(base64.b64decode(evidence['front']),caption='الوجه الأمامي')
        b.image(base64.b64decode(evidence['back']),caption='الوجه الخلفي')
        labels={'first_name':'الاسم الأول','other_names':'باقي الاسم','national_id':'الرقم القومي','birth_date':'الميلاد',
                'address_line1':'العنوان','address_line2':'تكملة العنوان','job':'المهنة','gender':'النوع','religion':'الديانة',
                'marital_status':'الحالة الاجتماعية','issue_date':'الإصدار','expiry_date':'الانتهاء'}
        st.dataframe([{'البيان':labels.get(k,k),'القراءة':v or 'غير مقروء'} for k,v in evidence['result']['fields'].items()],hide_index=True)
        derived = evidence['result'].get('derived', {}).get('birth_date_from_national_id')
        if derived:
            st.write('تاريخ الميلاد المستنتج من الرقم القومي: ' + derived)
        st.caption('القراءة الآلية قد تخطئ. الموافقة هنا تفعّل حساب المحاكاة فقط، ولا توثّق البطاقة لدى جهة رسمية.')
        with st.form('review_decision'):
            decision = st.selectbox('القرار',['موافقة وتفعيل الحساب','طلب إعادة الإرسال'])
            reasons={'راجعت الصور والبيانات':'checked','الصور غير واضحة':'unclear_images','البيانات غير متسقة':'inconsistent_data','البطاقة منتهية':'expired_card'}
            reason=st.selectbox('سبب القرار',list(reasons))
            checked=st.checkbox('راجعت صور البطاقة والبيانات وأؤكد هذا القرار')
            apply=st.form_submit_button('حفظ القرار',type='primary')
        if apply:
            approved=decision=='موافقة وتفعيل الحساب'
            if not checked or (approved != (reasons[reason]=='checked')):
                st.warning('أكد المراجعة واختر سببًا مناسبًا للقرار.')
            else:
                done=attempt(lambda: api().request('POST','/api/v1/identity/reviews/'+selected+'/decision',
                    payload={'decision':'approved' if approved else 'rejected','reason':reasons[reason]}))
                if done:
                    S.pop('review_evidence', None)
                    S.flash='تم حفظ القرار وتحديث حالة الحساب.'
                    st.rerun()


def knowledge_page():
    heading('المعرفة والسياسات', 'إجابات مرتبطة بمصادر مكتبة المشروع، بدون أرقام أو شروط مخترعة.')
    st.caption('مكتبة CIB: ملخصات عربية مراجعة لمصادر رسمية. تاريخ المراجعة 15 نوفمبر 2026. لا تمثل عقودًا كاملة أو اتصالًا بالبنك.')
    with st.expander('المنتجات والمصادر الرسمية'):
        if st.button('تحميل دليل المصادر'):
            S.cib_catalog = attempt(lambda: api().request('GET', '/api/v1/rag/catalog'))
        for source in (S.get('cib_catalog') or {}).get('documents', {}).values():
            st.link_button(source['title'], source['url'])
            st.caption(source['locator'] + ' · آخر مراجعة ' + source['reviewed_at'])
    with st.form('knowledge_query'):
        question = st.text_input('سؤالك عن سياسات الخدمات', placeholder='ما شروط قفل الحساب؟', max_chars=1000)
        ask = st.form_submit_button('ابحث في السياسات', type='primary')
    if ask and question.strip():
        with st.spinner('جاري البحث في المصادر…'):
            answer = attempt(lambda: api().request('POST', '/api/v1/rag/query', payload={'query': question.strip()}))
        if answer:
            results_view({'answer': answer['answer'], 'results': [{'data': answer}]})
    if 'knowledge_user' in S.roles:
        with st.expander('إضافة مستند خاص إلى مكتبتك'):
            with st.form('ingest'):
                source = st.text_input('اسم المصدر بالإنجليزية', placeholder='savings-policy', max_chars=80)
                text = st.text_area('نص المستند', max_chars=8000, height=200)
                save = st.form_submit_button('حفظ المستند')
            if save:
                import re
                if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}', source) or not text.strip():
                    st.error('اكتب اسم مصدر صحيح ونص المستند.')
                else:
                    result = attempt(lambda: api().request('PUT', '/api/v1/rag/documents/' + source, payload={'text': text}))
                    if result:
                        st.success('تم حفظ المستند في مكتبتك الخاصة.')



def historical_result(data):
    st.caption(data['notice'])
    a, b, c = st.columns(3)
    a.metric('آخر رصيد تاريخي · CZK', money(data['latest_balance'] or 0))
    b.metric('إجمالي الوارد في الفترة · CZK', money(data['incoming_total']))
    c.metric('إجمالي الصادر في الفترة · CZK', money(data['outgoing_total']))
    st.caption('الرصيد بتاريخ ' + str(data['as_of']) + ' · إجماليات الفترة تشمل كل المعاملات المطابقة، وليس الصفحة الحالية فقط. الصادر يشمل التحويلات وليس كله إنفاقًا استهلاكيًا.')
    if data['monthly']:
        import pandas as pd
        frame = pd.DataFrame([{'الشهر':m['month'], 'وارد':float(m['credit']), 'صادر':float(m['debit'])} for m in data['monthly']]).set_index('الشهر')
        st.bar_chart(frame, color=['#238777', '#d1a35a'])
    st.dataframe([{'التاريخ':r['created_at'], 'الحركة':r['operation'], 'الاتجاه':r['direction'], 'المبلغ · CZK':money(r['amount']), 'الرصيد بعدها · CZK':money(r['balance']), 'التصنيف الأصلي':r['category'], 'المعرف':r['id']} for r in data['transactions']], hide_index=True, use_container_width=True)
    st.caption(f"{data['total_transactions']:,} معاملة مطابقة · عرض حتى 100 معاملة ابتداءً من {data['offset'] + 1}")


def historical_page():
    from urllib.parse import urlencode
    heading('سجل مالي حقيقي. رؤية أوضح.', 'بيانات PKDD99 / CTU الأصلية للتحليل والاستكشاف، بعملتها وتواريخها التاريخية.', 'REAL DATA / RESEARCH WORKSPACE')
    st.markdown('<div class="hero"><span class="pill">BERKA · HISTORICAL DATA</span><h2>كل حركة وراها قصة.</h2><p>استكشف التدفقات الشهرية، راجع المعاملات، وافهم تاريخ الحساب من بيانات بنكية منشورة للبحث.</p></div>', unsafe_allow_html=True)
    if 'research_base' not in S:
        S.research_base = attempt(lambda: api().request('GET', '/api/v1/assistant/history'))
    base = S.get('research_base')
    if not base:
        st.info('لم تُخصّص عينة بحثية لهذا المستخدم بعد. تخصيص العينات يتم بواسطة مشغّل المشروع.')
        if st.button('إعادة تحميل العينة'): S.pop('research_base', None); st.rerun()
        return
    counts = base.get('dataset_counts', {})
    a, b, c, d = st.columns(4)
    a.metric('معاملات في المصدر', f"{counts.get('trans', 0):,}")
    b.metric('عملاء مجهّلون', f"{counts.get('client', 0):,}")
    c.metric('حسابات في المصدر', f"{counts.get('account', 0):,}")
    d.metric('قروض تاريخية', f"{counts.get('loan', 0):,}")
    st.caption('الأعداد تخص كامل المصدر؛ الحسابات المتاحة أدناه هي العينات المخصصة لدخولك فقط. بيانات CIB موجودة في مكتبة السياسات بشكل مستقل.')
    accounts = {int(a['id']):a for a in base['accounts']}
    with st.form('history_filters'):
        selected = st.selectbox('الحساب البحثي', list(accounts), format_func=lambda n:f"حساب {n} · عميل مجهّل {accounts[n]['client_id']}")
        left, right = st.columns(2)
        start = left.text_input('من تاريخ (اختياري)', placeholder='1998-01-01')
        end = right.text_input('إلى تاريخ (اختياري)', placeholder='1998-12-31')
        page = st.number_input('رقم صفحة المعاملات', min_value=1, max_value=1001, value=1, step=1)
        search = st.form_submit_button('عرض الحركة', type='primary')
    if search:
        from datetime import date
        try:
            filters = {'account_id':selected, 'offset':(page-1)*100}
            for key,value in (('start',start),('end',end)):
                if value.strip(): filters[key]=date.fromisoformat(value.strip()).isoformat()
            if start and end and filters['start'] > filters['end']: raise ValueError()
        except ValueError:
            st.error('اكتب تاريخًا صحيحًا بصيغة YYYY-MM-DD، وبداية قبل النهاية.')
        else:
            result = attempt(lambda: api().request('GET', '/api/v1/assistant/history?' + urlencode(filters)))
            if result:
                S.research_result = result
                S.pop('research_pdf', None)
    data = S.get('research_result', base)
    historical_result(data)
    if data['loans']:
        with st.expander('القروض التاريخية لهذا الحساب'):
            st.dataframe(data['loans'], hide_index=True, use_container_width=True)
            st.caption('A: منتهٍ دون مشاكل؛ B: منتهٍ غير مسدد؛ C: جارٍ منتظم؛ D: جارٍ مع مديونية. الحالة تعكس زمن البيانات وليست قرارًا حاليًا.')
    if st.button('تجهيز كشف العينة PDF'):
        S.research_pdf = attempt(lambda: api().request('GET', '/api/v1/assistant/history/statement.pdf?account_id=' + str(data['selected_account_id']), binary=True))
    if S.get('research_pdf'):
        st.download_button('تحميل كشف العينة', S.research_pdf, 'berka-research-statement.pdf', 'application/pdf')
        st.caption('التصدير يعرض أحدث 100 معاملة للحساب دون فلاتر الفترة.')
    st.link_button('مصدر البيانات وتوثيقها ↗', base['source_url'])



def load_beneficiaries():
    if 'beneficiaries' not in S:
        result = attempt(lambda: api().request('GET', '/api/v1/assistant/demo/beneficiaries'))
        if result: S.beneficiaries = result['beneficiaries']


def beneficiary_picker(label, key, exclude=None):
    choices = {r.get('account_number', r['id']):r['label'] for r in S.get('beneficiaries',[])
               if r.get('account_number', r['id']) != exclude}
    if not choices:
        st.caption('لا يوجد مستفيد متاح الآن.')
        return None
    return st.selectbox(label, list(choices), format_func=choices.get, key=key)


def audit_page():
    heading('سجل العمليات', 'راجع ما طلبته، وما تم تأكيده، والمراحل التي حفظها المساعد.')
    if st.button('تحديث السجل', type='primary'):
        S.pop('audit_events', None)
        S.pop('node_checkpoints', None)
    if 'audit_events' not in S:
        S.audit_events = attempt(lambda: api().request('GET', '/api/v1/assistant/audit'))
    if 'node_checkpoints' not in S:
        S.node_checkpoints = attempt(lambda: api().request('GET', '/api/v1/assistant/checkpoints'))
    events = (S.get('audit_events') or {}).get('events', [])
    snapshots = (S.get('node_checkpoints') or {}).get('checkpoints', [])
    first, second = st.tabs(['العمليات والتأكيدات', 'مراحل المساعد المحفوظة'])
    names = {'confirmation_requested':'بانتظار تأكيدك', 'execution_authorized':'تم قبول التأكيد',
             'completed':'تم تنفيذ العملية', 'cancelled':'ألغيت العملية', 'expired':'انتهت مهلة التأكيد',
             'otp_rejected':'رمز التحقق غير صحيح', 'confirmation_replayed':'عرض نتيجة عملية سابقة دون تكرارها',
             'checkpoint':'تم حفظ الطلب', 'checkpoint_read':'استعادة الطلب', 'graph_completed':'انتهت معالجة الطلب',
             'graph_checkpoint':'حفظ الحالة النهائية', 'node.info':'مراجعة معلومات الحساب', 'node.loan':'مراجعة القروض والمعرفة',
             'node.transfer':'تجهيز التحويل', 'node.account':'مراجعة الحسابات'}
    with first:
        if not events: st.info('لسه مفيش عمليات مسجلة. ابدأ بسؤال للمساعد أو تجربة خدمة.')
        st.dataframe([{'الوقت · UTC':e['created_at'][:19].replace('T',' '), 'الحدث': names.get(e['event'], 'قراءة بيانات: ' + e['event'].removeprefix('tool.')), 'الطلب':e['conversation_id'][-8:]} for e in events], hide_index=True, use_container_width=True)
        st.caption('آخر 50 حدث خاص بحساب دخولك. تجهيز طلب أو حفظ مرحلة لا يعني تنفيذ تحويل.')
    with second:
        st.info('هنا تتابع مراحل معالجة طلبك. حفظ مرحلة لا يعني تنفيذ عملية مالية.')
        nodes={'input':'بداية الطلب','security':'التحقق من الطلب','supervisor':'فهم الطلب واختيار الأدوات','info':'معلومات الحساب',
               'loan':'القروض والمعرفة','transfer':'تجهيز التحويل','account':'إدارة الحساب','confirmation_gate':'بوابة التأكيد',
               'final_response':'تجهيز الرد','audit_log':'تسجيل الحدث','checkpointed_state':'اكتمال حفظ الحالة'}
        st.dataframe([{'الوقت · UTC':r['created_at'][:19].replace('T',' '),'المرحلة':nodes.get(r['node'],r['node']),
                       'الخطوة':r['step'],'طريقة الفهم':'الموديل المحلي' if r.get('planner')=='local_llm' else 'نموذج الخدمة' if r.get('planner') else '—',
                       'الطلب':r['conversation_id'][-8:]} for r in snapshots],hide_index=True,use_container_width=True)
        st.caption('آخر 100 checkpoint. لا يتم عرض نص المحادثة أو رموز التحقق هنا.')


if S.get('token') and time.time() >= S.get('expires', 0):
    clear_session('انتهت الجلسة. سجّل الدخول مرة تانية.')
if S.get('flash'):
    st.info(S.pop('flash'))
if not S.get('token'):
    if S.get('otp_challenge'):
        otp_screen()
    elif S.get('signup'):
        signup_form()
    else:
        login()
else:
    import onboarding
    if 'applicant' in S.roles:
        onboarding.wizard(authenticated=True)
        if st.button('تسجيل الخروج', key='applicant_logout'):
            attempt(lambda: api().request('POST', '/api/v1/auth/logout'))
            clear_session('تم تسجيل الخروج.'); st.rerun()
        st.stop()
    with st.sidebar:
        brand()
        st.caption('خدماتك')
        pages = PAGES if 'customer' in S.roles else [PAGES[5], PAGES[6]]
        if 'identity_reviewer' in S.roles or 'admin' in S.roles:
            pages = ['طلبات فتح الحساب', 'مراجعة طلبات الهوية'] + pages
        if 'demo_treasurer' in S.roles or 'admin' in S.roles:
            pages = ['إيداع تجريبي'] + pages
        if S.get('navigate_to'):
            S.page = S.pop('navigate_to')
        st.radio('التنقل', pages, key='page', label_visibility='collapsed')
        st.divider()
        st.markdown('<span class="pill demo-pill">محاكاة مصرفية · لا أموال حقيقية</span>', unsafe_allow_html=True)
        st.write('مرحبًا، **' + S.username + '**')
        st.caption('جلسة شخصية آمنة')
        with st.expander('استكمال طلب سابق'):
            if st.button('اعرض آخر طلباتي'):
                result = attempt(lambda: api().request('GET', '/api/v1/assistant/recent-conversations'))
                if result: S.recent_conversations = result['conversations']
            recent = S.get('recent_conversations', [])
            if recent:
                labels = {'completed':'مكتمل', 'needs_input':'بيانات ناقصة', 'awaiting_confirmation':'ينتظر تأكيدك', 'cancelled':'ملغي', 'expired':'انتهت صلاحيته'}
                selected = st.selectbox('اختار الطلب', range(len(recent)), format_func=lambda i: recent[i]['updated_at'][:16].replace('T',' ') + ' · ' + labels.get(recent[i]['status'], 'طلب سابق'))
                if st.button('استكمل الطلب'):
                    conversation = recent[selected]['id']
                    result = attempt(lambda: api().request('GET', '/api/v1/assistant/conversations/' + conversation))
                    if result:
                        S.conversation = conversation
                        S.missing = result.get('missing', [])
                        S.pending = result.get('operation')
                        S.pending_conversation = conversation
                        S.chat_dataset = result.get('dataset', 'demo')
                        S['chat_source'] = 'عينة Berka التاريخية · CZK' if S.chat_dataset == 'berka' else 'حسابات المحاكاة · EGP'
                        S.navigate_to = PAGES[1]
                        S.pop('demo_code', None)
                        st.rerun()
        if st.button('تسجيل الخروج', use_container_width=True):
            try:
                api().request('POST', '/api/v1/auth/logout', payload={})
                message = 'تم تسجيل الخروج.'
            except APIError:
                message = 'تم مسح الجلسة من الواجهة، لكن تعذر تأكيد إلغاء التوكن على الخدمة.'
            clear_session(message)
            st.rerun()
    confirmation()
    if S.get('receipt'):
        receipt = S.receipt
        status = receipt['status']
        text = {'completed': 'تم تنفيذ العملية بنجاح.', 'cancelled': 'تم إلغاء العملية بدون تنفيذ.',
                'expired': 'انتهت صلاحية الطلب. ابدأ طلبًا جديدًا.', 'locked': 'تم إيقاف الطلب بعد محاولات تحقق غير صحيحة.'}.get(status, status)
        if status == 'completed':
            st.success(text)
        else:
            st.info(text)
        if receipt.get('result'):
            st.caption('الرقم المرجعي: ' + receipt['result'].get('reference', receipt['result'].get('account_number', 'راجع سجل العمليات')) + (' · طلب قرض قيد المراجعة' if receipt['result'].get('status') == 'pending_review' else ''))
        if st.button('إخفاء الإشعار'):
            S.pop('receipt', None)
            st.rerun()
    handlers = dict(zip(PAGES, [dashboard, chat_page, transfer_page, loans_page, accounts_page, identity_page, knowledge_page, historical_page, audit_page]))
    handlers['مراجعة طلبات الهوية'] = reviewer_page
    handlers['طلبات فتح الحساب'] = lambda: onboarding.staff_reviews(api())
    handlers['إيداع تجريبي'] = lambda: onboarding.deposits(api())
    if 'customer' in S.roles and S.page in [PAGES[2], PAGES[3], PAGES[4]]:
        state = identity_status()
        if state and state['verified']:
            handlers[S.page]()
        elif state:
            review_status_view(state)
            st.button('متابعة طلب الهوية', on_click=go, args=(PAGES[5],), type='primary')
    else:
        handlers[S.page]()
    if S.get('last_result') and not S.get('pending'):
        with st.container(border=True):
            results_view(S.last_result)
            if st.button('إخفاء النتيجة'):
                S.pop('last_result', None)
                st.rerun()
