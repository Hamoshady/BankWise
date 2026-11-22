import pytest
from bankwise.rag.arabic import terms
from bankwise.rag.service import RAGService
from bankwise.rag.vector_store import SQLiteVectorStore

@pytest.mark.parametrize('question,source', [
 ('عايز اقفل حسابي', 'close'), ('إغلاق الحِساب', 'close'),
 ('عاوز ابعت فلوس', 'transfer'), ('شروط التمويل', 'loan'),
 ('حلل المصاريف', 'expense'), ('اخر عمليات اودعت', 'deposit'),
])
def test_arabic_intents_retrieve_and_cite(question, source):
    with SQLiteVectorStore(':memory:') as store:
        service = RAGService(store)
        for name, text in {'close':'قفل الحساب يتطلب مراجعة الالتزامات.',
                           'transfer':'تحويل الأموال يتطلب تأكيد المستفيد.',
                           'loan':'شروط القرض لم تعتمد بعد.',
                           'expense':'تحليل المصروفات يعتمد على المعاملات.',
                           'deposit':'عمليات الإيداع تعرض تاريخ العملية.'}.items():
            service.ingest_text(text, name)
        result = service.answer(question, top_k=1, min_score=.15)
        assert result.citations and result.citations[0].source == source
        assert result.citations[0].excerpt in store.all()[[r.chunk.source for r in store.all()].index(source)].chunk.text


def test_normalization_preserves_negation():
    assert 'لا' in terms('لا يمكن إغلاق الحساب')
    assert terms('الحِساب') == terms('حسابي')


def test_unrelated_question_abstains():
    with SQLiteVectorStore(':memory:') as store:
        service = RAGService(store)
        service.ingest_text('قفل الحساب يتطلب مراجعة الالتزامات.', 'close')
        assert not service.answer('علاج الصداع', min_score=.15).citations


def test_banking_synonyms_normalize_to_the_same_terms():
    assert terms('أقل') == terms('الأدنى') == ['ادني']
    assert terms('أكبر') == terms('الأقصى') == ['اقصي']
    assert terms('متاحة') == terms('متاح') == ['متاح']
    assert terms('وأكبر') == terms('أكبر')


def test_purpose_lam_matches_the_bare_verb():
    # "لفتح الحساب" and "فتح الحساب" must retrieve the same policy text.
    assert terms('لفتح الحساب') == terms('فتح الحساب')
    assert terms('للسحب') == terms('سحب')


def test_repeated_subject_term_outranks_a_single_incidental_mention():
    with SQLiteVectorStore(':memory:') as store:
        service = RAGService(store)
        service.ingest_text(
            'القرض قرض شخصي. الحد الأقصى للقرض مبلغ محدد. القرض له شروط. '
            'سداد القرض متاح دون غرامة.', 'loans')
        service.ingest_text(
            'إغلاق الحساب يتطلب استيفاء الشروط. وجود قرض قائم يمنع الإغلاق.', 'closure')
        result = service.answer('شروط التمويل', top_k=1, min_score=.15)
        assert result.citations and result.citations[0].source == 'loans'
