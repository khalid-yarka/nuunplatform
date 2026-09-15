# ============================================================
# services/pdf_naming.py
# Inference of PDF metadata from a Telegram upload filename.
#
# Pure logic, no DB access. Everything returned is a *suggestion*
# for the admin to accept or overwrite in the fulfil form.
#
# Detection order matters:
#   1. "all chapters" patterns
#   2. specific "chapter N"
#   3. tags
#   4. subject
#   5. grade tokens are protected, then years/dates stripped
#   6. sentence-case the remainder
# ============================================================

import re
import logging

logger = logging.getLogger(__name__)


# ============================================================
# SUBJECT ALIASES
# ============================================================
# Maps canonical subject codes (matching subjects_config.SUBJECTS)
# to the strings we look for in a filename. Add freely.

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
# (regex_pattern, canonical_label)
# Order matters: more specific patterns must come first.

TAG_PATTERNS = [
    # Somali alias for Q&A — check first, it's a distinctive phrase
    (r'\bsualo\s+iyo\s+jawabo\b',              'Q&A'),
    (r'\bquestions?\s+and\s+answers?\b',       'Q&A'),
    (r'\bquestions?\s+and\s+a\b',              'Q&A'),
    (r'\bq\s*&\s*a\b',                         'Q&A'),
    (r'\bq\s+a\b',                             'Q&A'),
    (r'\bqa\b',                                'Q&A'),
    # Individual tags
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
# GRADE / FORM TOKENS (protected from number stripping)
# ============================================================

GRADE_RE = re.compile(
    r'\b(?:form|f|grade|g)\s*([1-9]|1[0-2])\b',
    re.IGNORECASE,
)


# ============================================================
# DATE / YEAR PATTERNS
# ============================================================

DATE_RE = re.compile(
    r'\b\d{1,4}[\s\-\./]\d{1,2}[\s\-\./]\d{1,4}\b'
)

YEAR_RE = re.compile(r'\b(?:19|20)\d{2}\b')

SHORT_YEAR_RE = re.compile(r'\b\d{1,2}\b')


# ============================================================
# HELPERS
# ============================================================

def _normalize_separators(text):
    """Replace _ - . with a single space and collapse."""
    if not text:
        return ''
    text = re.sub(r'[\s_\-\.]+', ' ', text)
    return text.strip()


def _sentence_case(text):
    """Lowercase everything, capitalise the first character."""
    if not text:
        return ''
    t = text.lower()
    return t[0].upper() + t[1:]


def _pretty_subject_name(code):
    """Return the human-readable subject name for a code."""
    try:
        from subjects_config import get_subject
        subj = get_subject(code)
        return subj['name'] if subj else code
    except Exception:
        return code


# Build a reverse alias → code map and a subject regex once.

_ALIAS_TO_CODE = {}
for _code, _aliases in SUBJECT_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_CODE[_normalize_separators(_alias).lower()] = _code

# Sort longer aliases first so "af somali" wins over "somali".
_SORTED_ALIASES = sorted(_ALIAS_TO_CODE.keys(), key=len, reverse=True)

if _SORTED_ALIASES:
    _SUBJECT_RE = re.compile(
        r'\b(?:' + '|'.join(re.escape(a) for a in _SORTED_ALIASES) + r')\b',
        re.IGNORECASE,
    )
else:
    _SUBJECT_RE = None


# ============================================================
# PUBLIC API
# ============================================================

def suggest_from_filename(filename):
    """
    Derive {title, subject, chapter, tags} from a filename.

    The output is purely advisory: the admin can freely overwrite
    any field in the fulfil form.
    """
    empty = {'title': '', 'subject': '', 'chapter': '', 'tags': ''}
    if not filename:
        return dict(empty)

    # ---- Strip extension --------------------------------------------------
    base = filename
    lower = base.lower()
    for ext in ('.pdf',):
        if lower.endswith(ext):
            base = base[:-len(ext)]
            break

    # ---- Normalize separators to spaces -----------------------------------
    working = _normalize_separators(base)

    # ============================================================
    # 1. Chapter detection
    # ============================================================
    chapter = ''

    m_all = ALL_CHAPTERS_RE.search(working)
    if m_all:
        chapter = 'All chapters'
        working = working[:m_all.start()] + ' ' + working[m_all.end():]
    else:
        m_ch = CHAPTER_RE.search(working)
        if m_ch:
            # Extract the trailing number token.
            tail = m_ch.group(0).lower()
            num_match = re.search(
                r'(?:' + _NUMBER_WORDS + r')', tail, re.IGNORECASE,
            )
            if num_match:
                raw_num = num_match.group(0).lower()
                num = NUMBER_WORD_MAP.get(raw_num, raw_num)
                chapter = f'Chapter {num}'
            working = working[:m_ch.start()] + ' ' + working[m_ch.end():]

    # ============================================================
    # 2. Tag detection
    # ============================================================
    detected_tags = []
    for pattern, canonical in TAG_PATTERNS:
        matches = re.findall(pattern, working, re.IGNORECASE)
        if matches:
            detected_tags.append(canonical)
        working = re.sub(pattern, ' ', working, flags=re.IGNORECASE)

    # Dedupe preserving order.
    seen = set()
    unique_tags = []
    for t in detected_tags:
        if t not in seen:
            seen.add(t)
            unique_tags.append(t)
    tags_str = ', '.join(unique_tags)

    # ============================================================
    # 3. Subject detection
    # ============================================================
    subject_code = ''
    if _SUBJECT_RE:
        m_sub = _SUBJECT_RE.search(working)
        if m_sub:
            alias = _normalize_separators(m_sub.group(0)).lower()
            subject_code = _ALIAS_TO_CODE.get(alias, '')
            working = working[:m_sub.start()] + ' ' + working[m_sub.end():]

    # ============================================================
    # 4. Protect grade tokens before stripping numbers
    # ============================================================
    protected_grades = []

    def _stash(match):
        protected_grades.append(match.group(0))
        return f'\x00G{len(protected_grades) - 1}\x00'

    working = GRADE_RE.sub(_stash, working)

    # ============================================================
    # 5. Strip dates / years / standalone numbers
    # ============================================================
    working = DATE_RE.sub(' ', working)
    working = YEAR_RE.sub(' ', working)
    working = SHORT_YEAR_RE.sub(' ', working)

    # ============================================================
    # 6. Restore grade tokens
    # ============================================================
    for i, g in enumerate(protected_grades):
        working = working.replace(f'\x00G{i}\x00', g)

    # ============================================================
    # 7. Clean up the title
    # ============================================================
    working = re.sub(r'\s+', ' ', working).strip()
    title = _sentence_case(working)

    # ============================================================
    # 8. Fallback if stripping left nothing useful
    # ============================================================
    if not title:
        parts = []
        if subject_code:
            parts.append(_pretty_subject_name(subject_code))
        if chapter:
            parts.append(chapter.lower())
        if unique_tags:
            parts.append(unique_tags[0].lower())
        if parts:
            title = _sentence_case(' '.join(parts))
        else:
            title = _sentence_case(_normalize_separators(base))

    return {
        'title':   title,
        'subject': subject_code,
        'chapter': chapter,
        'tags':    tags_str,
    }