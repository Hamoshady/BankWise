"""Conservative Arabic lexical normalization; never modify quoted source text."""
import re
import unicodedata

_STOP = set('ما ماذا متى كيف هل هو هي في من على عن الى انا عايز عاوز اريد لو ممكن اعمل اي ايه اللي و او ده دي يتم التي الذي كل حاله'.split())
_GROUPS = [
    'قرض قروض تمويل اقتراض loan loans',
    'اغلاق اغلق اقفل اقفله قفل اقفال closure close',
    'فتح افتح افتتاح opening open',
    'تحويل احول ابعت ارسل ارسال حواله حوالات transfers transfer',
    'فلوس اموال مال مبلغ',
    'حساب حسابي حسابات account accounts',
    'سحب اسحب سحبت سحوبات السحوبات withdrawals withdrawal',
    'ايداع اودع اودعت ايداعات deposits deposit',
    'مصروفات مصاريف انفاق صرفت expense expenses spending',
    'تحليل حلل analysis analyze',
    'كشف بيان statement',
    'رسوم تكلفه تكاليف هدفع ادفع بكام fees fee cost',
    'شروط متطلبات مطلوب requirements conditions',
    'عمر سن age',
    'ادني اقل',
    'اقصي اكبر',
    'متاح متاحه متاحين متوفر متوفره',
    'بنك لبنك بنوك bank banks',
    'دولي دوليه خارجي خارجيه بره international',
    'صيغه صيغ بصيغتي format formats',
    'افتراضي افتراضيا افتراضيه default',
    'فتره مده period duration',
]

def normalize(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    text = re.sub('[\u064b-\u065f\u0670\u0640]', '', text)
    return text.translate(str.maketrans('أإآٱىة', 'اااايه'))

_ALIASES = {normalize(word): normalize(group.split()[0]) for group in _GROUPS for word in group.split()}

def terms(text):
    result = []
    for word in re.findall(r'[^\W_]+', normalize(text), re.UNICODE):
        if word in _STOP:
            continue
        for prefix in ('وال', 'بال', 'كال', 'فال', 'لل'):
            if word.startswith(prefix) and len(word) >= len(prefix) + 3:
                word = word[len(prefix):]
                break
        if word.startswith('ال') and len(word) > 4:
            word = word[2:]
        if word.startswith('و') and word[1:] in _ALIASES:
            word = word[1:]
        # Purpose lam: "لفتح" / "للسحب" carry the same meaning as "فتح" / "سحب".
        if word.startswith('ل') and len(word) > 3 and word[1:] in _ALIASES:
            word = word[1:]
        result.append(_ALIASES.get(word, word))
    return result
