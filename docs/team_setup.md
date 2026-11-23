# تشغيل نسخة الفريق من GitHub

هذه التعليمات تخص فرع `main`، ولا تحتاج ملف Windows ZIP القديم أو أي صورة Docker مبنية على جهاز صاحب المشروع.

## المطلوب مرة واحدة

- جهاز Intel/AMD ‏64-bit. Windows مع Docker Desktop وWSL2 وLinux containers، أو Linux مع Docker Engine وCompose.
- Python 3.11 أو أحدث، وGit، وGitHub CLI (`gh`).
- اتصال إنترنت للتنزيل الأول وبناء صور Docker. بعدها الموديلات تعمل محليًا.
- يفضّل 24GB RAM؛ 16GB قد تكفي مع إغلاق البرامج الأخرى. لم تُختبر حدود 8/16GB، و8GB غير موصى بها لهذه النسخة الكاملة. لا يلزم كارت NVIDIA.
- نحو 20GB مساحة فارغة لتنزيل الأوزان وفكّها وبناء صور Docker.

## تنزيل وتشغيل

افتح Terminal أو PowerShell بعد تشغيل Docker:

```sh
gh auth login
gh repo clone Hamoshady/BankWise -- --branch main --single-branch
cd BankWise
python scripts/team.py start
```

في Linux استخدم `python3` إذا لم يتوفر الأمر `python`، وفي Windows يمكن استخدام `py -3.11`.
يجب أن يكون لحساب GitHub صلاحية الوصول إلى الريبو لو كان Private.

الأمر الأخير:

1. يتحقق من أوزان `models/`. إن لم تكن موجودة ينزّل أصول الإصدار `models-20261122-v1` إلى `model_downloads/`.
2. يجمع أجزاء Qwen ويتحقق من SHA-256 لكل وزن مقابل البيان المثبت في `configs/team_models.json`.
3. يبني API وواجهة Streamlit من المصدر. ينزّل اعتماديات Python وllama.cpp أثناء بناء Docker فقط.
4. يستعيد قاعدة البيانات الموجودة في `shared_snapshot/database.dump` داخل volume مستقل لأول تشغيل فقط، ويحافظ على الحسابات والأرصدة والمعاملات المنسوخة. يستعيد ملفات النتائج ومفاتيح التشفير المرتبطة بها.
5. يشغّل EasyOCR وE5 وQwen3.5 محليًا، ثم ينتظر جاهزية الخدمات.

بعد ظهور `BankWise ready` افتح **http://127.0.0.1:8501/**. أول تشغيل قد يستغرق وقتًا طويلًا حسب الإنترنت والمعالج؛ لا تعتبر تنزيل الكود وحده كافيًا لتشغيل الموديلات. لو المنفذ مستخدم:

```sh
python scripts/team.py start --port 8502
```

## تنزيل الأوزان يدويًا بدل GitHub CLI

افتح [إصدار الأوزان](https://github.com/Hamoshady/BankWise/releases/tag/models-20261122-v1) بحساب لديه صلاحية، ونزّل **كل ملفات Assets** إلى مجلد `model_downloads` داخل المشروع: `assets.json` و`models-core.zip` وأجزاء Qwen الثلاثة. ملفات Source code ZIP/TAR ليست الأوزان.

```sh
python scripts/team_models.py install
python scripts/team.py start
```

`Download ZIP` أو `git clone` ينزّلان الكود والأوزان التجريبية الصغيرة؛ الأوزان الكبيرة موجودة في Releases وينزلها سكربت التشغيل تلقائيًا. لا يحتاج الأصدقاء لتنزيل أي موديل يدويًا من Hugging Face.

## الحسابات وOTP

نفس الحسابات وكلمات المرور المعروفة في النسخة الأصلية. قاعدة البيانات تحفظ password hashes، ولا يستطيع السكربت استرجاع كلمات المرور. اطلب بيانات الدخول المعروفة من صاحب المشروع. يمكن إنشاء طلب Signup جديد، لكن يلزم حساب مسؤول موجود للموافقة عليه. وضع هذه النسخة `development` ليعمل عرض OTP التجريبي؛ لا يرسل SMS حقيقيًا.

## الإيقاف والمتابعة

```sh
python scripts/team.py status
python scripts/team.py logs
python scripts/team.py stop
python scripts/team.py start
```

الإيقاف وإعادة التشغيل يحافظان على البيانات في Docker volumes. لا تستخدم `docker compose down -v` على بيانات تريد الاحتفاظ بها. لا تحذف `.team.env` لأنه يحتوي كلمة مرور قاعدة بيانات نسختك المحلية. لا تغير `BANKWISE_REVIEW_KEY` إذا أردت قراءة السجلات المشفرة المنسوخة.

## الأوزان المتاحة

| المكون | المسار بعد التثبيت | الحالة |
|---|---|---|
| Qwen3.5-4B Q4_K_M | `models/llm/Qwen3.5-4B-Q4_K_M.gguf` | الافتراضي للمساعد |
| EasyOCR Arabic + English + CRAFT | `models/ocr/` | OCR الافتراضي |
| multilingual-e5-small | `models/rag/embedding/` | البحث الدلالي |
| PaddleOCR Arabic PP-OCRv5 mobile | `models/experimental/paddle/` | تجريبي، غير محمّل افتراضيًا |
| YOLO11n field detector | `models/experimental/yolo-fields.pt` | تجريبي، غير محمّل افتراضيًا |
| YOLO11n digits | `models/experimental/yolo-digits.pt` | تجريبي، غير محمّل افتراضيًا |
| CRNN-CTC | `models/experimental/crnn-ctc.pt` | تجريبي، غير محمّل افتراضيًا |

تشغيل التجارب يحتاج الاعتماديات الاختيارية المذكورة في `docs/ocr_backends.md` و`evaluation/numeric_ocr/README.md`. وجود الأوزان لا يغيّر backend تلقائيًا، ولا يثبت دقة أفضل. checkpoints التجريبية أُعدت على بيانات محدودة؛ لا يُدّعى تعميمها على جميع البطاقات.

حزمة التشغيل لا تتضمن checkpoints التدريب الوسيطة أو Qwen2.5 القديم الذي استُبدل بـQwen3.5، لأنها ليست مطلوبة للمسار الحالي. صور Docker القديمة وWindows ZIP غير مطلوبين. تراخيص ومصادر الأوزان في `configs/team_models.json` و`distribution/team/model-seed/licenses/` وداخل حزمة الموديلات. كود/أوزان Ultralytics تخضع لترخيصها AGPL-3.0؛ تصدير Thndr المستخدم يصرّح بـMIT ولا يُعاد نشر داتاسيت التدريب هنا.

## مشاكل شائعة

- **Release not found / 404:** تحقق من تسجيل `gh auth login` بحساب عضو في الريبو ومن اكتمال Workflow نشر الأوزان في تبويب Actions.
- **Missing or corrupt release asset:** أعد تنزيل الملف المذكور فقط إلى `model_downloads` ثم أعد التثبيت؛ لا تتجاوز فحص البصمة.
- **Docker unavailable:** افتح Docker Desktop واختر Linux containers.
- **Startup timeout:** راجع `python scripts/team.py logs` وتوفر الذاكرة. Qwen يعمل على CPU، وقد يستغرق الإحماء عدة دقائق.
- **الموقع على جهاز آخر:** الواجهة مربوطة بـ127.0.0.1 عمدًا؛ افتحها من نفس الجهاز الذي يشغّل Docker.

هذه نسخة مشاركة للتجربة المحلية، وتحتوي snapshot حقيقية وفق طلب صاحب المشروع؛ ليست إعداد نشر بنكي عام.
