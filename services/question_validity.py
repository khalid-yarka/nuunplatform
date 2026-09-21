# services/question_validity.py
# Duplicate finder + dismissals for questions.

import logging
from difflib import SequenceMatcher
from typing import Dict, List, Optional

from db import execute_with_retry
from question_utils import normalize_question_text, question_hash

logger = logging.getLogger(__name__)

FUZZY_THRESHOLD = 0.85
MAX_MATCHES = 8
MIN_TEXT_LENGTH = 10


def _sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _dismissed_pairs_for(question_id: int) -> set:
    """Return set of question IDs whose pair with `question_id` is dismissed."""
    try:
        cur = execute_with_retry("""
            SELECT a_id, b_id FROM question_duplicate_dismissals
            WHERE a_id = ? OR b_id = ?
        """, (question_id, question_id))
        out = set()
        for row in cur.fetchall():
            other = row['b_id'] if row['a_id'] == question_id else row['a_id']
            out.add(other)
        return out
    except Exception:
        return set()


def find_duplicate_questions(
    question_text: str,
    subject_code: str = '',
    grade: str = '',
    exclude_id: Optional[int] = None,
    fuzzy_threshold: float = FUZZY_THRESHOLD,
) -> List[Dict]:
    """Return up to MAX_MATCHES full-data duplicate candidates."""
    if not question_text or len(question_text.strip()) < MIN_TEXT_LENGTH:
        return []

    norm = normalize_question_text(question_text)
    if not norm:
        return []

    h = question_hash(question_text)
    matches: List[Dict] = []
    seen = set()

    dismissed = _dismissed_pairs_for(exclude_id) if exclude_id else set()

    SELECT_COLS = """
        id, question_text, options, correct_answer, difficulty,
        chapter, tags, explanation, pdf_code, pdf_page,
        subject_code, grade, status, created_at
    """

    # ── Exact hash ─────────────────────────────────────────
    try:
        sql = f"""
            SELECT {SELECT_COLS} FROM questions
            WHERE question_hash = ? AND status != 'archived'
        """
        params = [h]
        if exclude_id:
            sql += " AND id != ?"
            params.append(exclude_id)
        cur = execute_with_retry(sql, params)
        for row in cur.fetchall():
            d = dict(row)
            if d['id'] in seen or d['id'] in dismissed:
                continue
            seen.add(d['id'])
            d['match_type'] = 'exact'
            d['similarity'] = 1.0
            matches.append(d)
    except Exception as e:
        logger.warning(f"exact-hash search failed: {e}")

    # ── Fuzzy ──────────────────────────────────────────────
    try:
        sql = f"""
            SELECT {SELECT_COLS}, question_text_normalized
            FROM questions WHERE status != 'archived'
        """
        params = []
        if subject_code:
            sql += " AND subject_code = ?"
            params.append(subject_code)
        if grade:
            sql += " AND grade = ?"
            params.append(grade)
        if exclude_id:
            sql += " AND id != ?"
            params.append(exclude_id)
        sql += " LIMIT 1000"

        cur = execute_with_retry(sql, params)
        for row in cur.fetchall():
            d = dict(row)
            if d['id'] in seen or d['id'] in dismissed:
                continue
            other = d.pop('question_text_normalized', None) \
                or normalize_question_text(d.get('question_text') or '')
            ratio = _sim(norm, other)
            if ratio >= fuzzy_threshold:
                seen.add(d['id'])
                d['match_type'] = 'fuzzy'
                d['similarity'] = round(ratio, 4)
                matches.append(d)
    except Exception as e:
        logger.warning(f"fuzzy search failed: {e}")

    matches.sort(key=lambda x: (
        0 if x['match_type'] == 'exact' else 1, -x['similarity']
    ))
    return matches[:MAX_MATCHES]


def to_payload(matches: List[Dict]) -> List[Dict]:
    """Shape matches for the frontend — full data, safe to render inline."""
    import json as _json

    out = []
    for m in matches:
        opts_raw = m.get('options')
        if isinstance(opts_raw, str):
            try:
                opts = _json.loads(opts_raw)
            except Exception:
                opts = {}
        else:
            opts = opts_raw or {}

        txt = m.get('question_text') or ''
        out.append({
            'id':             m.get('id'),
            'text':           txt[:120] + ('…' if len(txt) > 120 else ''),
            'full_text':      txt,
            'options':        opts,
            'correct_answer': m.get('correct_answer') or 'A',
            'difficulty':     m.get('difficulty') or 1,
            'chapter':        m.get('chapter') or '',
            'tags':           m.get('tags') or '',
            'explanation':    m.get('explanation') or '',
            'pdf_code':       m.get('pdf_code') or '',
            'pdf_page':       m.get('pdf_page'),
            'subject_code':   m.get('subject_code') or '',
            'grade':          m.get('grade') or '',
            'status':         m.get('status') or 'active',
            'created_at':     (m.get('created_at') or '')[:10],
            'match_type':     m.get('match_type') or 'fuzzy',
            'similarity_pct': int(round((m.get('similarity') or 0) * 100)),
        })
    return out


# ─── Dismissals ────────────────────────────────────────────

def dismiss_duplicate_pair(a_id: int, b_id: int, actor_id: int) -> bool:
    """Store a dismissal. Always stores with a_id < b_id."""
    try:
        low, high = (a_id, b_id) if a_id < b_id else (b_id, a_id)
        execute_with_retry("""
            INSERT OR IGNORE INTO question_duplicate_dismissals
                (a_id, b_id, dismissed_by, dismissed_at)
            VALUES (?, ?, ?, datetime('now', 'localtime'))
        """, (low, high, actor_id), commit=True)
        return True
    except Exception as e:
        logger.warning(f"dismiss_duplicate_pair failed: {e}")
        return False


def undismiss_pair(a_id: int, b_id: int) -> bool:
    try:
        low, high = (a_id, b_id) if a_id < b_id else (b_id, a_id)
        execute_with_retry(
            "DELETE FROM question_duplicate_dismissals WHERE a_id = ? AND b_id = ?",
            (low, high), commit=True,
        )
        return True
    except Exception:
        return False