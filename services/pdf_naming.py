# ============================================================
# services/pdf_naming.py
# Inference of PDF metadata from a Telegram upload filename.
#
# Pure logic, no DB access. Everything returned is a *suggestion*
# for the admin to accept or overwrite in the fulfil form.
#
# Detection order matters:
#   1. extract years (before everything else so they don't get
#      mixed into the title or eaten by the numbers-stripper)
#   2. "all chapters" patterns
#   3. specific "chapter N"
#   4. tags
#   5. subject (detected but NOT stripped — kept in the title)
#   6. grade tokens are protected
#   7. dates / standalone numbers stripped
#   8. remaining text is filtered to Unicode letters + digits + '&'
#   9. title-cased for Latin words; non-Latin scripts left as-is
# ============================================================

import re
import logging
import unicodedata

logger = logging.getLogger(__name__)


# ============================================================
# ACRONYM WHITELIST
# ============================================================
ACRONYM_WHITELIST = frozenset({
    'ICT', 'SST', 'GPE', 'GP', 'PDF', 'MCQ', 'CGSE',
    'QA', 'OK', 'ID', 'UI', 'AI',
})


# ============================================================
# SUBJECT ALIASES
# ============================================================
SUBJECT_ALIASES = {
    'mathematics': ['mathematics', 'math', 'maths', 'mathmatics', 'xisaab', 'mat'],
    'english':     ['english', 'eng', 'ingiriisi'],
    'af_somali':   ['af somali', 'afsoomaali', 'somali', 'af_somali'],
    'arabic':      ['arabic', 'arab', 'carabi'],
    'islamic':     ['islamic', 'islam', 'islaam', 'diin'],
    'geography':   ['geography', 'geog', 'geo', 'juqraafi'],
    'history':     ['history', 'hist', 'taariikh'],
    'physics':     ['physics', 'phys', 'fisikis'],
    'chemistry':   ['chemistry', 'chem', 'kimistari'],
    'biology':     ['biology', 'bio', 'bayoloji'],
    'ict':         ['ict', 'computer', 'it'],
    'business':    ['business', 'bus', 'ganacsi'],
    'gp':          ['gp', 'gov', 'government', 'policy'],
    'agriculture': ['agriculture', 'agri', 'agric', 'beeraha'],
}


# ============================================================
# TAG VOCABULARY
# ============================================================
TAG_PATTERNS = [
    (r'\bsualo\s+iyo\s+jawabo\b',              'Q&A'),
    (r'\bquestions?\s+and\s+answers?\b',       'Q&A'),
    (r'\bquestions?\s+and\s+a\b',              'Q&A'),
    (r'\bq\s*&\s*a\b',                         'Q&A'),
    (r'\bq\s+a\b',                             'Q&A'),
    (r'\bqa\b',                                'Q&A'),
    (r'\bnotes?\b',                            'Notes'),
    (r'\bexams?\b',                            'Exam'),
    (r'\bassign(?:ment)?s?\b',                 'Assignment'),
    (r'\bassig\b',                             'Assignment'),
    (r'\banswers?\b',                          'Answers'),
    (r'\bexercises?\b',                        'Exercise'),
    (r'\brevisions?\b',                        'Revision'),
    (r'\bsummar(?:y|ies)\b',                   'Summary'),
    (r'\bworksheets?\b',                       'Worksheet'),
    (r'\btests?\b',                            'Test'),
    (r'\bquiz(?:zes)?\b',                      'Quiz'),
    (r'\bpast\s+papers?\b',                    'Past paper'),
]


# ============================================================
# CHAPTER PATTERNS
# ============================================================
_CHAPTER_WORDS = r'(?:ch|chp|chap|chapter|cutub|unit)'
_NUMBER_WORDS  = (
    r'(?:one|two|three|four|five|six|seven|eight|nine|ten|'
    r'eleven|twelve|1[0-9]|[1-9])'
)

ALL_CHAPTERS_RE = re.compile(
    r'\ball\s+(?:ch(?:apter|p)?s?|cutubs?|units?)\b',
    re.IGNORECASE,
)

CHAPTER_RE = re.compile(
    r'\b' + _CHAPTER_WORDS + r'\s*' + _NUMBER_WORDS + r'\b',
    re.IGNORECASE,
)

NUMBER_WORD_MAP = {
    'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5',
    'six': '6', 'seven': '7', 'eight': '8', 'nine': '9', 'ten': '10',
    'eleven': '11', 'twelve': '12',
}


# ============================================================
# GRADE / FORM TOKENS
# ============================================================
GRADE_RE = re.compile(
    r'\b(?:form|f|grade|g)\s*([1-9]|1[0-2])\b',
    re.IGNORECASE,
)


# ============================================================
# YEAR PATTERNS
# ============================================================
YEAR_SPAN_RE = re.compile(
    r'\b((?:19|20)\d{2})\s*[\-–—/]\s*((?:19|20)\d{2})\b'
)
YEAR_RE = re.compile(r'\b(?:19|20)\d{2}\b')
DATE_RE = re.compile(r'\b\d{1,4}[\s\-\./]\d{1,2}[\s\-\./]\d{1,4}\b')


# ============================================================
# TITLE FILTERING — UNICODE-AWARE
# ============================================================
# Keep every character that is:
#   • a Unicode letter (Arabic, Latin, Cyrillic, Somali, ...)
#   • a Unicode decimal digit
#   • a Unicode combining mark (Arabic diacritics)
#   • the ampersand `&`
#   • any whitespace
#
# Everything else becomes a space.
#
# The old regex `[^A-Za-z0-9&\s]` stripped Arabic completely.
# These helpers replace it.

_WHITESPACE_RE = re.compile(r'\s+')

# Characters kept as-is. `c.isalnum()` alone is not enough because
# it excludes combining marks, which are important for proper
# Arabic rendering.
_KEPT_CATEGORIES = ('L', 'N', 'M')  # Letter, Number, Mark


def _is_kept_char(c):
    if c == '&':
        return True
    if c.isspace():
        return True
    cat = unicodedata.category(c)
    return cat and cat[0] in _KEPT_CATEGORIES


def _strip_disallowed(s):
    """
    Replace every character that isn't a Unicode letter/digit/mark,
    `&`, or whitespace, with a single space.
    """
    if not s:
        return ''
    return ''.join(c if _is_kept_char(c) else ' ' for c in s)


# ============================================================
# HELPERS
# ============================================================

def _normalize_separators(text):
    """Replace _, -, . with a single space and collapse runs."""
    if not text:
        return ''
    text = re.sub(r'[\s_\-\.]+', ' ', text)
    return text.strip()


def _pretty_subject_name(code):
    """Human-readable subject name for a canonical code."""
    try:
        from subjects_config import get_subject
        subj = get_subject(code)
        return subj['name'] if subj else code
    except Exception:
        return code


def _word_is_latin(word):
    """True if the word contains at least one Latin letter."""
    for c in word:
        if 'LATIN' in unicodedata.name(c, ''):
            return True
    return False


def _title_case_with_acronyms(text):
    """
    Title-case every word that contains Latin letters.
    Non-Latin words (Arabic, etc.) are left untouched.

    Arabic, for example, has no case, so `word.upper()` is a no-op —
    but explicitly skipping the transform is clearer and avoids any
    edge cases with mixed content.
    """
    if not text:
        return ''
    parts = []
    for word in text.split(' '):
        if not word:
            continue
        if word.upper() in ACRONYM_WHITELIST:
            parts.append(word.upper())
            continue
        if _word_is_latin(word):
            parts.append(word[:1].upper() + word[1:].lower())
        else:
            parts.append(word)
    return ' '.join(parts)


# Build the reverse alias -> code map and subject regex once.
_ALIAS_TO_CODE = {}
for _code, _aliases in SUBJECT_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_CODE[_normalize_separators(_alias).lower()] = _code

_SORTED_ALIASES = sorted(_ALIAS_TO_CODE.keys(), key=len, reverse=True)

if _SORTED_ALIASES:
    _SUBJECT_RE = re.compile(
        r'\b(?:' + '|'.join(re.escape(a) for a in _SORTED_ALIASES) + r')\b',
        re.IGNORECASE,
    )
else:
    _SUBJECT_RE = None


# ============================================================
# PUBLIC: TITLE CLEANER
# ============================================================

def clean_title(raw):
    """
    Return a title containing only Unicode letters, digits, `&`,
    and spaces.

    Examples:
        'chemistry_ch4_(2023)-final.pdf'  ->  'Chemistry Ch4 2023 Final'
        'كتاب_الرياضيات_2024.pdf'          ->  'كتاب الرياضيات 2024'
        'ICT & MATH F4.pdf'               ->  'ICT & Math F4'
        '###'                             ->  ''
    """
    if not raw:
        return ''

    s = raw
    lower = s.lower()
    if lower.endswith('.pdf'):
        s = s[:-4]

    # Separators to spaces
    s = re.sub(r'[\s_\-\.\,;:\(\)\[\]\{\}/\\|!?@#$%^*+=~`\'"<>]+', ' ', s)

    # Unicode-safe filter
    s = _strip_disallowed(s)

    # Collapse
    s = _WHITESPACE_RE.sub(' ', s).strip()

    if not s:
        return ''

    return _title_case_with_acronyms(s)


# ============================================================
# PUBLIC: YEAR EXTRACTOR
# ============================================================

def extract_year(raw):
    """
    Find year patterns and return (cleaned_text, years_list).

    Recognises:
        2023-2024  /  2023/2024  /  2023–2024     ->  both years
        2023                                       ->  single year
        (2023)  /  [2023]  /  _2023_  /  -2023-   ->  extracted the same way
    """
    if not raw:
        return '', []

    years = []
    working = raw

    def _span_repl(m):
        years.append(m.group(1))
        years.append(m.group(2))
        return ' '

    working = YEAR_SPAN_RE.sub(_span_repl, working)

    def _single_repl(m):
        years.append(m.group(0))
        return ' '

    working = YEAR_RE.sub(_single_repl, working)

    seen = set()
    unique = []
    for y in years:
        if y not in seen:
            seen.add(y)
            unique.append(y)

    return working.strip(), unique


# ============================================================
# PUBLIC: FULL SUGGESTION
# ============================================================

def suggest_full(filename):
    """
    Extended suggestion: title, subject, chapter, tags, plus
    confidence flags and warnings.

    Returns:
        {
            'title':       str,
            'subject':     str,          # canonical subject code, '' if unknown
            'chapter':     str,          # "Chapter 5" or "All chapters" or ''
            'tags':        str,          # comma-separated
            'confidence':  'high' | 'medium' | 'low',
            'needs_review': bool,
            'warnings':    [str, ...],
        }
    """
    result = {
        'title':        '',
        'subject':      '',
        'chapter':      '',
        'tags':         '',
        'confidence':   'low',
        'needs_review': True,
        'warnings':     [],
    }

    if not filename:
        result['warnings'].append('no filename')
        return result

    # 1. Strip extension
    base = filename
    lower = base.lower()
    for ext in ('.pdf',):
        if lower.endswith(ext):
            base = base[:-len(ext)]
            break

    # 2. Normalise separators
    working = _normalize_separators(base)

    # 3. Extract years
    working, year_tags = extract_year(working)

    # 4. Chapter detection
    chapter = ''

    m_all = ALL_CHAPTERS_RE.search(working)
    if m_all:
        chapter = 'All chapters'
        working = working[:m_all.start()] + ' ' + working[m_all.end():]
    else:
        m_ch = CHAPTER_RE.search(working)
        if m_ch:
            tail = m_ch.group(0).lower()
            num_match = re.search(
                r'(?:' + _NUMBER_WORDS + r')', tail, re.IGNORECASE,
            )
            if num_match:
                raw_num = num_match.group(0).lower()
                num = NUMBER_WORD_MAP.get(raw_num, raw_num)
                chapter = f'Chapter {num}'
            working = working[:m_ch.start()] + ' ' + working[m_ch.end():]

    # 5. Tag detection
    detected_tags = []
    for pattern, canonical in TAG_PATTERNS:
        matches = re.findall(pattern, working, re.IGNORECASE)
        if matches:
            detected_tags.append(canonical)
        working = re.sub(pattern, ' ', working, flags=re.IGNORECASE)

    seen = set()
    unique_tags = []
    for t in detected_tags:
        if t not in seen:
            seen.add(t)
            unique_tags.append(t)

    # 6. Subject detection — the alias is INTENTIONALLY LEFT IN
    #    `working` so it survives into the final title.
    subject_code = ''
    if _SUBJECT_RE:
        m_sub = _SUBJECT_RE.search(working)
        if m_sub:
            alias = _normalize_separators(m_sub.group(0)).lower()
            subject_code = _ALIAS_TO_CODE.get(alias, '')

    # 7. Protect grade tokens
    protected_grades = []

    def _stash(match):
        protected_grades.append(match.group(0))
        return f'\x00G{len(protected_grades) - 1}\x00'

    working = GRADE_RE.sub(_stash, working)

    # 8. Strip dates + stray numbers
    working = DATE_RE.sub(' ', working)
    working = re.sub(r'\b\d{1,4}\b', ' ', working)

    # 9. Restore grade tokens
    for i, g in enumerate(protected_grades):
        working = working.replace(f'\x00G{i}\x00', g)

    # 10. Unicode-safe filter — keeps Arabic, Latin, any script
    working = _strip_disallowed(working)
    working = _WHITESPACE_RE.sub(' ', working).strip()

    # 11. Title case (Latin only)
    title = _title_case_with_acronyms(working)

    # 12. Fallback if the title ended up empty
    if not title:
        parts = []
        if subject_code:
            parts.append(_pretty_subject_name(subject_code))
        if chapter:
            parts.append(chapter.lower())
        if unique_tags:
            parts.append(unique_tags[0].lower())
        if parts:
            title = _title_case_with_acronyms(' '.join(parts))
        else:
            title = clean_title(base)
        if not title:
            title = 'Untitled'
            result['warnings'].append('could not derive a title from filename')

    # 13. Combine tags
    all_tags = list(unique_tags)
    for y in year_tags:
        if y not in all_tags:
            all_tags.append(y)
    tags_str = ', '.join(all_tags)

    # 14. Confidence
    signal_score = 0
    if subject_code:
        signal_score += 2
    if chapter:
        signal_score += 1
    if unique_tags:
        signal_score += 1
    if len(title) >= 8:
        signal_score += 1

    if signal_score >= 4:
        confidence = 'high'
    elif signal_score >= 2:
        confidence = 'medium'
    else:
        confidence = 'low'

    if not subject_code:
        result['warnings'].append('subject could not be inferred')
    if len(title) < 5:
        result['warnings'].append('title is very short — check before publishing')

    needs_review = (confidence != 'high') or (not subject_code)

    return {
        'title':        title,
        'subject':      subject_code,
        'chapter':      chapter,
        'tags':         tags_str,
        'confidence':   confidence,
        'needs_review': needs_review,
        'warnings':     result['warnings'],
    }


# ============================================================
# BACKWARD-COMPATIBLE WRAPPER
# ============================================================

def suggest_from_filename(filename):
    full = suggest_full(filename)
    return {
        'title':   full['title'],
        'subject': full['subject'],
        'chapter': full['chapter'],
        'tags':    full['tags'],
    }