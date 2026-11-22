"""Signup wizard and staff tools. Every mutation is authorized again by the API."""
import base64
import time
from datetime import date
from uuid import uuid4
import streamlit as st
from client import Client, APIError

ROOT = '/api/v1/onboarding'
STATUS = {'draft':'مسودة', 'pending_review':'قيد المراجعة', 'needs_information':'مطلوب استكمال بيانات',
          'rejected':'مرفوض', 'active':'الحساب مفعّل', 'cancelled':'ملغي', 'expired':'انتهت صلاحية الطلب'}
REASONS = {'checked':'تمت مراجعة البيانات', 'unclear_images':'صور البطاقة غير واضحة', 'inconsistent_data':'بيانات غير متطابقة',
           'expired_card':'البطاقة منتهية', 'missing_information':'معلومات ناقصة'}
EMPLOYMENT = {'employed':'موظف', 'self_employed':'عمل حر', 'student':'طالب', 'retired':'متقاعد', 'unemployed':'بدون عمل'}
ACTIVITY = {'under_10000':'أقل من 10,000 جنيه', '10000_50000':'10,000–50,000 جنيه', 'over_50000':'أكثر من 50,000 جنيه'}

def headers():
    return {'X-Application-ID':st.session_state.ob_app['id'], 'X-Application-Token':st.session_state.ob_token}

def call(method, path, **kwargs):
    return Client().request(method, ROOT+path, headers={**headers(), **kwargs.pop('headers', {})}, **kwargs)

def save(result):
    st.session_state.ob_app = result
    if result.get('application_token'): st.session_state.ob_token = result.pop('application_token')

def reset():
    for key in list(st.session_state):
        if str(key).startswith('ob_'): del st.session_state[key]

def wizard(*, authenticated=False):
    st.title('طلب فتح حساب' if not authenticated else 'متابعة طلب فتح الحساب')
    st.caption('حساب محاكاة بالجنيه المصري · يبدأ بصفر بعد الموافقة · لا أموال حقيقية')
    try:
        _wizard(authenticated)
    except APIError as error:
        if error.status == 401 and error.code != 'otp_failed':
            reset()
            if authenticated:
                st.warning('تغيّرت حالة الطلب أو انتهت الجلسة. سجّل الخروج ثم ادخل مجددًا؛ ربما تم تفعيل حسابك.')
            else:
                st.warning('انتهت جلسة التسجيل. أكّد الموبايل مجددًا لاستكمال المسودة.')
                if st.button('العودة للتحقق'): st.rerun()
        else:
            st.error('البيانات أو اسم المستخدم مستخدمون بالفعل، أو حالة الطلب تغيرت. راجع بياناتك وحدّث الطلب.' if error.status==409 else str(error))

def _wizard(authenticated):
    S = st.session_state
    if authenticated and not S.get('ob_token'):
        save(Client(S.token).request('GET', ROOT+'/mine'))
    if not S.get('ob_token'):
        st.markdown('### 1 · رقم الموبايل')
        st.caption('لو عندك مسودة، استخدم نفس الرقم لاستكمالها بعد التحقق. لو أنشأت بيانات الدخول، يمكنك الدخول لمتابعة الطلب.')
        with st.form('ob_phone_form'):
            phone = st.text_input('رقم الموبايل المصري', placeholder='010xxxxxxxx', key='ob_phone')
            start = st.form_submit_button('إرسال كود التسجيل')
        if start:
            result = Client().request('POST',ROOT+'/phone',payload={'phone':phone})
            S.ob_challenge = result; S.ob_sent_at = time.time()
        if S.get('ob_challenge'):
            ch = S.ob_challenge
            if ch.get('development_otp'):
                st.info('وضع تجريبي — لم تُرسل رسالة SMS. هذا الاختبار لا يثبت ملكية الموبايل.')
                if st.button('OTP',key='ob_show_otp'):
                    result=Client().request('GET',ROOT+'/dev/otp/'+ch['challenge_id'])
                    st.code(result['otp'],language=None)
            else: st.info('أرسلنا كود التسجيل إلى الموبايل. صالح لمدة 5 دقائق و5 محاولات.')
            with st.form('ob_verify_form',clear_on_submit=True):
                code=st.text_input('كود التسجيل · 6 أرقام',max_chars=6)
                verify=st.form_submit_button('تأكيد الموبايل والمتابعة')
            st.caption('لإعادة الإرسال، اضغط «إرسال كود التسجيل» بعد 60 ثانية. الكود الجديد يلغي القديم.')
            if verify:
                save(Client().request('POST',ROOT+'/phone/verify',payload={'challenge_id':ch['challenge_id'],'otp':code}))
                S.pop('ob_challenge',None); st.rerun()
        return
    app = call('GET','/application')
    save(app)
    st.info('رقم الطلب: '+app['reference']+' · الحالة: '+STATUS[app['status']])
    if app.get('reason'): st.write(REASONS.get(app['reason'],app['reason']))
    if app['status'] in {'pending_review','rejected','active','cancelled','expired'}:
        if app['status']=='pending_review': st.write('طلبك بانتظار مراجعة المسؤول. الخدمات البنكية غير متاحة حتى التفعيل.')
        elif app['status']=='active': st.success('حسابك اتفعّل. سجّل الدخول من جديد لفتح الخدمات البنكية.')
        elif app['status']=='expired': st.write('انتهت مدة الاحتفاظ بالمسودة. ارجع لإنشاء حساب وأكّد نفس الموبايل لبدء الطلب مجددًا.')
        if st.button('تحديث حالة الطلب'): st.rerun()
        if app['status']=='pending_review': cancellation()
        return
    if not app['has_images']:
        step='رفع البطاقة'
    elif not app.get('fields'):
        step='مراجعة البيانات'
    elif not app['credentials_created']:
        step='بيانات الدخول'
    else:
        step='تقديم الطلب'
    options=['رفع البطاقة']
    if app['has_images']: options += ['مراجعة البيانات']
    if app.get('fields'): options += ['بيانات الدخول'] if not app['credentials_created'] else ['تقديم الطلب']
    selected=st.radio('خطوات التسجيل',options,index=options.index(step),horizontal=True)
    if selected=='رفع البطاقة':
        st.markdown('### 2 · صور البطاقة')
        st.caption('ارفع صورًا واضحة تشمل البطاقة كاملة. تحفظ مشفّرة للمراجعة لمدة أقصاها 7 أيام، وتُحذف صورها عند القرار النهائي.')
        with st.form('ob_images_form'):
            front=st.file_uploader('وش البطاقة',type=['png','jpg','jpeg'],key='ob_front')
            back=st.file_uploader('ظهر البطاقة',type=['png','jpg','jpeg'],key='ob_back')
            consent=st.checkbox('أوافق على معالجة وحفظ صور البطاقة مشفّرة لمراجعة هذا الطلب')
            upload=st.form_submit_button('استخراج بيانات البطاقة')
        if upload:
            if not consent or front is None or back is None: st.warning('ارفع الصورتين ووافق على استخدامهما.'); return
            with st.spinner('جاري قراءة البطاقة بالموديل المحلي…'):
                save(call('POST','/images',files={'front':(front.name,front.getvalue(),front.type),'back':(back.name,back.getvalue(),back.type)},headers={'X-Evidence-Consent':'true'}))
            for k in list(S):
                if str(k).startswith('ob_field_'): del S[k]
            st.rerun()
    elif selected=='مراجعة البيانات': details_form(app)
    elif selected=='بيانات الدخول':
        st.markdown('### 4 · بيانات الدخول')
        st.caption('ستستخدم هذه البيانات لمتابعة طلبك. الحساب المالي لن يُنشأ الآن.')
        with st.form('ob_credentials_form',clear_on_submit=True):
            name=st.text_input('اسم المستخدم',max_chars=64)
            password=st.text_input('كلمة المرور',type='password',max_chars=256)
            confirm=st.text_input('تأكيد كلمة المرور',type='password',max_chars=256)
            st.caption('اسم المستخدم 3–64 حرفًا إنجليزيًا أو رقمًا أو _ أو . أو -. كلمة المرور 12 حرفًا على الأقل.')
            create=st.form_submit_button('حفظ بيانات الدخول')
        if create:
            if password!=confirm or len(password)<12: st.warning('تأكد من تطابق كلمتي المرور وطولهما 12 حرفًا على الأقل.'); return
            save(call('POST','/credentials',payload={'username':name,'password':password,'confirmation':confirm})); st.rerun()
    else:
        st.markdown('### 5 · مراجعة وتقديم الطلب')
        st.write('اسم المستخدم: '+str(app.get('username') or 'محفوظ'))
        display_details(app)
        st.info('المراجعة يدوية. OCR لا يثبت الهوية. الحساب يبدأ بصفر بعد الموافقة.')
        approved=st.checkbox('راجعت بياناتي وأفهم أن هذا حساب محاكاة',key='ob_submit_consent')
        if st.button('تقديم طلب فتح الحساب',type='primary',disabled=not approved):
            call('POST','/submit'); st.rerun()
    cancellation()

def cancellation():
    with st.expander('إلغاء الطلب'):
        yes=st.checkbox('أؤكد إلغاء الطلب وحذف مستنداته',key='ob_cancel_yes')
        if st.button('إلغاء طلب فتح الحساب',disabled=not yes): call('POST','/cancel'); st.rerun()

def details_form(app):
    st.markdown('### 3 · مراجعة البيانات ومعلومات العميل')
    st.caption('صحّح أخطاء القراءة. التصحيحات تظهر للمراجع ولا تُعتمد تلقائيًا. أدخل التواريخ بصيغة YYYY-MM-DD.')
    st.caption('المنتج المتاح: حساب شخصي تجريبي بالجنيه · الحد الأدنى للعمر 21 سنة كقاعدة للمحاكاة.')
    fields=app.get('fields') or {}
    raw=app.get('ocr',{}).get('fields',{})
    labels = {'first_name': 'الاسم الأول', 'other_names': 'باقي الاسم',
              'address_line1': 'العنوان الأول', 'address_line2': 'العنوان الثاني',
              'national_id': 'الرقم القومي', 'birth_date': 'تاريخ الميلاد',
              'expiry_date': 'تاريخ الانتهاء', 'job': 'المهنة'}
    states = dict(item.split(':', 1) for item in app.get('ocr', {}).get('summary', {}).get('field_status', [])
                  if isinstance(item, str) and ':' in item)
    review = [label for key, label in labels.items() if not raw.get(key) or states.get(key, 'ok') != 'ok']
    if review:
        st.warning('قراءة غير مكتملة أو غير مؤكدة — راجع مقابل البطاقة: ' + '، '.join(review)
                   + '. يمكنك الرجوع لرفع صور أوضح. لا تعتمد على القيم المملوءة تلقائيًا دون مراجعة.')
    kyc=app.get('kyc') or {}
    def text(label,name,default='',limit=150):
        return st.text_input(label,value=str(fields.get(name,kyc.get(name,default)) or ''),max_chars=limit,key='ob_field_'+name)
    with st.form('ob_details_form'):
        full_name=text('الاسم الكامل','full_name',' '.join(str(raw.get(k) or '') for k in ['first_name','other_names']).strip())
        national_id=text('الرقم القومي · 14 رقمًا','national_id',raw.get('national_id'),14)
        birth=text('تاريخ الميلاد','birth_date',raw.get('birth_date') or app.get('ocr',{}).get('derived',{}).get('birth_date_from_national_id'),10)
        gender=st.selectbox('النوع',['male','female'],index=0 if fields.get('gender')!='female' else 1,format_func=lambda x:'ذكر' if x=='male' else 'أنثى',key='ob_field_gender')
        address=text('العنوان','address',' '.join(str(raw.get(k) or '') for k in ['address_line1','address_line2']).strip(),300)
        occupation=text('المهنة','occupation',raw.get('job'),120)
        expiry=text('تاريخ انتهاء البطاقة','expiry_date',raw.get('expiry_date'),10)
        email=text('البريد الإلكتروني','email',limit=254)
        employment=st.selectbox('الحالة الوظيفية',list(EMPLOYMENT),index=list(EMPLOYMENT).index(kyc.get('employment','employed')),format_func=EMPLOYMENT.get,key='ob_field_employment')
        income=text('مصدر الدخل','income_source')
        purpose=text('الغرض من فتح الحساب','purpose')
        activity=st.selectbox('النشاط الشهري المتوقع',list(ACTIVITY),index=list(ACTIVITY).index(kyc.get('expected_activity','under_10000')),format_func=ACTIVITY.get,key='ob_field_activity')
        tax=text('دولة / دول الإقامة الضريبية','tax_residency',limit=100)
        consent=st.checkbox('أقر بأن البيانات المدخلة صحيحة لأغراض طلب المحاكاة',key='ob_field_consent')
        submit=st.form_submit_button('حفظ البيانات والمتابعة',type='primary')
    if submit:
        if not consent: st.warning('راجع البيانات ثم وافق على الإقرار.'); return
        try:
            b=date.fromisoformat(birth); e=date.fromisoformat(expiry)
            today=date.today(); age=today.year-b.year-((today.month,today.day)<(b.month,b.day))
            if age<21 or e<today: raise ValueError()
        except ValueError: st.warning('راجع التواريخ: العمر 21 سنة على الأقل والبطاقة غير منتهية. الصيغة YYYY-MM-DD.'); return
        payload=dict(full_name=full_name,national_id=national_id,birth_date=birth,gender=gender,address=address,occupation=occupation,expiry_date=expiry,email=email,employment=employment,income_source=income,purpose=purpose,expected_activity=activity,tax_residency=tax,account_type='personal_egp',consent=True)
        save(call('PUT','/details',payload=payload)); st.rerun()

def display_details(app):
    labels={'full_name':'الاسم','national_id':'الرقم القومي','birth_date':'الميلاد','gender':'النوع','address':'العنوان','occupation':'المهنة','expiry_date':'انتهاء البطاقة','email':'البريد','employment':'العمل','income_source':'مصدر الدخل','purpose':'الغرض','expected_activity':'النشاط المتوقع','tax_residency':'الإقامة الضريبية','account_type':'نوع الحساب'}
    for key,value in {**app.get('fields',{}),**app.get('kyc',{})}.items():
        if key!='consent': st.write(labels.get(key,key)+': '+str(value))

def staff_reviews(client):
    st.title('طلبات فتح الحساب')
    st.caption('الموافقة تنشئ حسابًا بصفر. لا تعني هذه المراجعة تحققًا حكوميًا أو اعتمادًا بنكيًا حقيقيًا.')
    try:
        rows=client.request('GET',ROOT+'/reviews')['applications']
        if not rows: st.info('لا توجد طلبات تحتاج مراجعة.'); return
        selected=st.selectbox('اختر الطلب',rows,format_func=lambda r:r['username']+' · '+STATUS[r['status']]+' · '+r['reference'])
        if st.button('عرض طلب فتح الحساب'):
            st.session_state.ob_review=client.request('GET',ROOT+'/reviews/'+selected['id'])
        detail=st.session_state.get('ob_review')
        if not detail or detail['id']!=selected['id']: return
        display_details(detail)
        st.write('التحقق الفعلي من الموبايل: '+('نعم' if detail['phone_verified'] else 'لا — تجربة محلية'))
        for flag in detail.get('flags',[]):
            st.warning({'manual_identity_review_required':'تجب مراجعة الهوية يدويًا','review_national_id':'راجع الرقم القومي المصحّح مقابل البطاقة','review_birth_date':'راجع تاريخ الميلاد المصحّح','review_expiry_date':'راجع تاريخ الانتهاء المصحّح'}.get(flag,flag))
        with st.expander('قراءة OCR الأصلية'): st.json(detail.get('ocr',{}).get('fields',{}))
        left,right=st.columns(2)
        if detail.get('front'): left.image(base64.b64decode(detail['front']),caption='وش البطاقة')
        if detail.get('back'): right.image(base64.b64decode(detail['back']),caption='ظهر البطاقة')
        if selected['status']!='pending_review': st.info('بانتظار استكمال العميل وإعادة تقديم الطلب.'); return
        with st.form('ob_decision_form'):
            decision=st.selectbox('القرار',['needs_information','approved','rejected'],format_func=lambda x:{'approved':'موافقة وتفعيل','rejected':'رفض','needs_information':'طلب استكمال'}[x])
            reason=st.selectbox('سبب القرار',list(REASONS),format_func=REASONS.get)
            confirmed=st.checkbox('راجعت الصور والبيانات وأؤكد القرار')
            send=st.form_submit_button('تسجيل قرار الطلب')
        if send:
            if not confirmed: st.warning('أكد مراجعة الطلب أولًا.'); return
            if (decision=='approved')!=(reason=='checked'): st.warning('اختر «تمت مراجعة البيانات» للموافقة، وسببًا آخر للاستكمال أو الرفض.'); return
            client.request('POST',ROOT+'/reviews/'+selected['id']+'/decision',payload={'decision':decision,'reason':reason,'confirmed':True})
            st.session_state.pop('ob_review',None); st.rerun()
    except APIError as error: st.error(str(error))

def deposits(client):
    st.title('إيداع تجريبي')
    st.info('أموال محاكاة فقط. كل إيداع يُسجّل في حركة الحساب مع هوية المسؤول وسبب الإيداع.')
    try:
        accounts=client.request('GET',ROOT+'/deposit/accounts')['accounts']
        if not accounts: st.info('لا توجد حسابات مفعّلة للإيداع.'); return
        if not st.session_state.get('ob_deposit_pending'):
            with st.form('ob_deposit_prepare'):
                account=st.selectbox('حساب العميل',accounts,format_func=lambda a:a['username']+' · '+a.get('account_number', a['id'])+' · '+a['balance']+' جنيه')
                amount=st.number_input('المبلغ بالجنيه',min_value=0.01,max_value=1000000.0,value=100.0,step=100.0)
                note=st.text_input('سبب الإيداع التجريبي',max_chars=200)
                prepare=st.form_submit_button('مراجعة الإيداع')
            if prepare:
                if len(note.strip())<3: st.warning('اكتب سبب الإيداع.'); return
                st.session_state.ob_deposit_pending={'account_id':account['id'],'amount':f'{amount:.2f}','note':note.strip(),'operation_id':str(uuid4()),'confirmed':True}
                st.session_state.ob_deposit_name=account['username']
                st.session_state.ob_deposit_number=account.get('account_number', account['id']); st.rerun()
        else:
            pending=st.session_state.ob_deposit_pending
            st.write('العميل: '+st.session_state.ob_deposit_name)
            st.write('الحساب: '+st.session_state.get('ob_deposit_number', 'حساب محفوظ'))
            st.write('المبلغ: '+pending['amount']+' جنيه · '+pending['note'])
            yes=st.checkbox('أؤكد إضافة هذا المبلغ التجريبي إلى الحساب')
            if st.button('تنفيذ الإيداع',disabled=not yes):
                result=client.request('POST',ROOT+'/deposit',payload=pending)
                st.session_state.ob_deposit_receipt=result
                st.session_state.pop('ob_deposit_pending',None); st.rerun()
            if st.button('إلغاء الإيداع'):
                st.session_state.pop('ob_deposit_pending',None); st.rerun()
        if st.session_state.get('ob_deposit_receipt'):
            receipt=st.session_state.ob_deposit_receipt
            st.success('تم تسجيل الإيداع: '+receipt['amount']+' جنيه · معاملة '+receipt.get('reference', 'حدّث البيانات لعرض الرقم المرجعي'))
    except APIError as error: st.error(str(error))
