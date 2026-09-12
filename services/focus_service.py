# services/focus_service.py
# ---------------------------------------------------------------
# Focus feature service.
#
# Aggregates data from the user's wrong answers to power:
#   - Suggested study sources (PDFs)
#   - Performance analytics (subject bars, accuracy line, etc.)
#   - Focus tips (rule-based narrative for level 3)
#
# Two entitlements gate the output:
#   - focus_suggestions (quota) -> how many PDF cards shown
#   - focus_analytics  (level)  -> chart depth
# ---------------------------------------------------------------

import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

from db import execute_with_retry
from subjects_config import get_subject

logger = logging.getLogger(__name__)


# ============================================
# CORE: collect the user's wrong answers
# ============================================

def _collect_misses(user_id: int) -> Dict[int, Dict[str, Any]]:
    """
    Parse every quiz attempt and collect wrong answers.

    Returns dict:
        { question_id: {'count': int, 'last_miss': ISO string} }
    """
    try:
        cursor = execute_with_retry("""
            SELECT id, answers, completed_at
            FROM quiz_attempts
            WHERE student_id = ?
            ORDER BY completed_at DESC
        """, (user_id,))
        attempts = cursor.fetchall()
    except Exception as e:
        logger.error(f"_collect_misses query failed: {e}")
        return {}

    misses: Dict[int, Dict[str, Any]] = {}

    for row in attempts:
        raw = row['answers']
        if not raw:
            continue
        try:
            answers = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(answers, list):
            continue

        for a in answers:
            if not isinstance(a, dict):
                continue
            if a.get('correct'):
                continue
            qid = a.get('question_id')
            if qid is None:
                continue
            try:
                qid = int(qid)
            except (ValueError, TypeError):
                continue

            if qid not in misses:
                misses[qid] = {
                    'count': 0,
                    'last_miss': row['completed_at'] or ''
                }
            misses[qid]['count'] += 1
            # Since attempts are sorted DESC, first-seen is most-recent.
            # No update needed after initial set.

    return misses


# ============================================
# SUGGESTED SOURCES
# ============================================

def get_suggested_sources(user_id: int,
                          limit: Optional[int] = None,
                          days: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Return PDFs to re-read, sorted by miss_count DESC, last_miss DESC.

    limit: max results (None = unlimited)
    days:  restrict to misses within N days (None = all-time)
    """
    misses = _collect_misses(user_id)
    if not misses:
        return []

    # Optional time window
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        misses = {qid: m for qid, m in misses.items()
                  if (m.get('last_miss') or '') >= cutoff}
        if not misses:
            return []

    qids = list(misses.keys())
    placeholders = ','.join('?' * len(qids))

    try:
        cursor = execute_with_retry(f"""
            SELECT id, pdf_code, pdf_page, subject_code, chapter
            FROM questions
            WHERE id IN ({placeholders})
              AND pdf_code IS NOT NULL
              AND pdf_code != ''
        """, qids)
        rows = cursor.fetchall()
    except Exception as e:
        logger.error(f"get_suggested_sources join failed: {e}")
        return []

    groups: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        code = row['pdf_code']
        qid = row['id']
        m = misses.get(qid)
        if not m:
            continue

        if code not in groups:
            groups[code] = {
                'pdf_code': code,
                'miss_count': 0,
                'pages': set(),
                'last_miss': '',
                'subjects': set(),
            }

        groups[code]['miss_count'] += m['count']
        if row['pdf_page']:
            try:
                groups[code]['pages'].add(int(row['pdf_page']))
            except (ValueError, TypeError):
                pass
        if (m.get('last_miss') or '') > (groups[code]['last_miss'] or ''):
            groups[code]['last_miss'] = m.get('last_miss') or ''
        if row['subject_code']:
            groups[code]['subjects'].add(row['subject_code'])

    # Resolve titles
    from db import get_pdf_by_code as get_main_pdf
    try:
        from bot.db import get_bot_pdf_by_code
    except Exception:
        def get_bot_pdf_by_code(_): return None

    result: List[Dict[str, Any]] = []
    for code, g in groups.items():
        main = get_main_pdf(code)
        bot = get_bot_pdf_by_code(code) if not main else None
        resolved = main or bot or {}
        result.append({
            'pdf_code': code,
            'title': resolved.get('title') or code,
            'subject': resolved.get('subject') or '',
            'is_premium': bool(resolved.get('is_premium', 0)),
            'miss_count': g['miss_count'],
            'pages': sorted(g['pages']),
            'last_miss': g['last_miss'],
            'resolvable': bool(main or bot),
        })

    # Sort: miss_count DESC, then last_miss DESC
    result.sort(key=lambda x: (x['miss_count'], x['last_miss'] or ''), reverse=True)

    if limit is not None and limit > 0:
        result = result[:limit]

    return result


# ============================================
# ANALYTICS
# ============================================

def _subject_breakdown(user_id: int) -> List[Dict[str, Any]]:
    try:
        from db import get_user_subject_performance
        return get_user_subject_performance(user_id) or []
    except Exception as e:
        logger.warning(f"_subject_breakdown failed: {e}")
        return []


def _accuracy_over_time(user_id: int, limit: int = 15) -> Dict[str, List]:
    try:
        from db import get_user_recent_scores
        scores = get_user_recent_scores(user_id, limit) or []
    except Exception as e:
        logger.warning(f"_accuracy_over_time failed: {e}")
        scores = []

    labels, values = [], []
    for s in scores:
        completed = (s.get('completed_at') or '')[:10]
        labels.append(completed)
        total = s.get('total_questions') or 0
        score = s.get('score') or 0
        values.append(round((score / total) * 100) if total else 0)

    return {'labels': labels, 'values': values}


def _miss_count_by_subject(user_id: int) -> List[Dict[str, Any]]:
    misses = _collect_misses(user_id)
    if not misses:
        return []
    qids = list(misses.keys())
    placeholders = ','.join('?' * len(qids))
    try:
        cursor = execute_with_retry(
            f"SELECT subject_code FROM questions WHERE id IN ({placeholders})",
            qids
        )
        rows = cursor.fetchall()
    except Exception as e:
        logger.warning(f"_miss_count_by_subject failed: {e}")
        return []

    counts: Dict[str, int] = {}
    for row in rows:
        code = row['subject_code']
        if code:
            counts[code] = counts.get(code, 0) + 1

    out = []
    for code, count in counts.items():
        subj = get_subject(code)
        out.append({
            'subject_code': code,
            'subject_name': subj['name'] if subj else code,
            'miss_count': count,
        })
    out.sort(key=lambda x: x['miss_count'], reverse=True)
    return out


def _difficulty_breakdown(user_id: int) -> List[Dict[str, Any]]:
    misses = _collect_misses(user_id)
    if not misses:
        return []
    qids = list(misses.keys())
    placeholders = ','.join('?' * len(qids))
    try:
        cursor = execute_with_retry(
            f"SELECT difficulty FROM questions WHERE id IN ({placeholders})",
            qids
        )
        rows = cursor.fetchall()
    except Exception as e:
        logger.warning(f"_difficulty_breakdown failed: {e}")
        return []

    counts: Dict[int, int] = {}
    for row in rows:
        d = row['difficulty'] or 1
        counts[d] = counts.get(d, 0) + 1

    return [{'difficulty': d, 'miss_count': counts.get(d, 0)}
            for d in range(1, 6) if d in counts]


def get_analytics_data(user_id: int, level: int) -> Dict[str, Any]:
    """Return analytics payload based on the user's level (0-3)."""
    data: Dict[str, Any] = {}

    if level >= 1:
        data['subject_performance'] = _subject_breakdown(user_id)

    if level >= 2:
        data['accuracy_over_time'] = _accuracy_over_time(user_id)
        data['miss_by_subject'] = _miss_count_by_subject(user_id)

    if level >= 3:
        data['difficulty_breakdown'] = _difficulty_breakdown(user_id)

    return data


# ============================================
# FOCUS TIPS (level 3)
# ============================================

def get_focus_tips(user_id: int) -> List[Dict[str, str]]:
    """Rule-based narrative tips derived from miss patterns."""
    tips: List[Dict[str, str]] = []

    misses = _collect_misses(user_id)
    if not misses:
        return [{
            'icon': '🎉',
            'text': 'No wrong answers yet — take a quiz to unlock insights.'
        }]

    total_misses = sum(m['count'] for m in misses.values())
    unique_qs = len(misses)
    tips.append({
        'icon': '📊',
        'text': f"You've missed <strong>{total_misses}</strong> questions "
                f"across <strong>{unique_qs}</strong> unique problems."
    })

    # Weakest subject
    perf = _subject_breakdown(user_id)
    if perf:
        worst = min(perf, key=lambda x: x.get('avg_score', 100))
        score = worst.get('avg_score', 0)
        if score < 60:
            tips.append({
                'icon': '🎯',
                'text': f"Your weakest subject is "
                        f"<strong>{worst.get('subject_name', 'Unknown')}</strong> "
                        f"at {score:.0f}%."
            })

    # Difficulty pattern
    diff = _difficulty_breakdown(user_id)
    if diff:
        easy = sum(d['miss_count'] for d in diff if d['difficulty'] <= 2)
        hard = sum(d['miss_count'] for d in diff if d['difficulty'] >= 4)
        if easy > hard and easy > 0:
            tips.append({
                'icon': '⚡',
                'text': "You miss <strong>easy questions</strong> more than hard "
                        "ones — slow down and re-read carefully."
            })
        elif hard > easy * 2 and hard > 0:
            tips.append({
                'icon': '💪',
                'text': "Most of your misses are on <strong>hard questions</strong> "
                        "— that's normal. Keep practicing."
            })

    # Top suggestion pointer
    from services import entitlement_service
    sl = entitlement_service.get_limit(user_id, 'focus_suggestions')
    sources = get_suggested_sources(user_id, sl if sl else None)
    if sources:
        top = sources[0]
        tips.append({
            'icon': '📚',
            'text': f"Start with <strong>{top['title']}</strong> — "
                    f"you missed {top['miss_count']} question(s) from it."
        })

    return tips


# ============================================
# COVERAGE (for admin)
# ============================================

def get_coverage_stats() -> List[Dict[str, Any]]:
    """Per-subject PDF-link coverage for admin dashboards."""
    try:
        cursor = execute_with_retry("""
            SELECT
                subject_code,
                COUNT(*) AS total,
                SUM(CASE WHEN pdf_code IS NOT NULL AND pdf_code != ''
                         THEN 1 ELSE 0 END) AS linked
            FROM questions
            WHERE status = 'active'
            GROUP BY subject_code
            ORDER BY subject_code
        """)
        rows = cursor.fetchall()
    except Exception as e:
        logger.error(f"get_coverage_stats failed: {e}")
        return []

    out = []
    for row in rows:
        total = row['total'] or 0
        linked = row['linked'] or 0
        subj = get_subject(row['subject_code'])
        out.append({
            'subject_code': row['subject_code'],
            'subject_name': subj['name'] if subj else row['subject_code'],
            'total': total,
            'linked': linked,
            'pct': round(100.0 * linked / total, 1) if total else 0.0,
        })
    return out