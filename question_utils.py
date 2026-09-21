# question_utils.py
# Normalization + hashing for question deduplication.

import hashlib
import re

VALID_GRADES = ('F4', 'F3', 'G8', 'G7')
DEFAULT_GRADE = 'F4'

_GRADE_LABELS = {
    'F4': 'Form 4',
    'F3': 'Form 3',
    'G8': 'Grade 8',
    'G7': 'Grade 7',
}

_ARABIC_DIACRITICS = re.compile(r'[\u064B-\u065F\u0670]')
_WHITESPACE = re.compile(r'\s+')
_PUNCT = re.compile(r'[^\w\s\u0600-\u06FF]', re.UNICODE)


def normalize_question_text(text: str) -> str:
    if not text:
        return ''
    text = _ARABIC_DIACRITICS.sub('', text)
    text = text.lower()
    text = _PUNCT.sub(' ', text)
    text = _WHITESPACE.sub(' ', text)
    return text.strip()


def question_hash(text: str) -> str:
    norm = normalize_question_text(text)
    return hashlib.sha256(norm.encode('utf-8')).hexdigest()[:32]


def normalize_grade(raw: str) -> str:
    g = (raw or '').strip().upper()
    return g if g in VALID_GRADES else DEFAULT_GRADE


def grade_label(g: str) -> str:
    return _GRADE_LABELS.get((g or '').strip().upper(), g or '')