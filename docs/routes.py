# docs/routes.py
# ============================================================
# All /docs/* endpoints.
#
# Public read. Progress is tracked in session['docs_progress']
# as {feature_key: highest_step_seen}.
#
# Language: docs bypass the `language_somali` entitlement gate.
# If the user has chosen Somali in settings, docs render in
# Somali regardless of tier.
# ============================================================

import logging

from flask import (
    Blueprint, render_template, session, request,
    redirect, url_for, abort, g,
)

from docs import registry
from docs import context as docs_context


logger = logging.getLogger(__name__)

docs_bp = Blueprint('docs', __name__, url_prefix='/docs')

PROGRESS_KEY = 'docs_progress'

# Feature keys that collide with top-level docs routes.
RESERVED = frozenset({'roadmap', 'search', 'tour'})

# Tier ordering for feature gating.
TIER_LEVEL = {'free': 0, 'premium': 1, 'pro': 2}


# ============================================================
# LANGUAGE BYPASS
# ============================================================
# i18n_service gates Somali behind the `language_somali`
# entitlement. Docs are meant to be readable in both languages
# regardless of tier, so we force `g._i18n_lang` here when the
# user has explicitly chosen Somali in their settings.

@docs_bp.before_request
def _force_docs_language():
    try:
        prefs = session.get('settings') or {}
        if prefs.get('appearance.language') == 'so':
            g._i18n_lang = 'so'
    except Exception:
        pass


# ============================================================
# PROGRESS HELPERS
# ============================================================

def _get_progress():
    return dict(session.get(PROGRESS_KEY) or {})


def _mark_step(feature_key, step_number):
    progress = _get_progress()
    current = int(progress.get(feature_key, 0) or 0)
    if step_number > current:
        progress[feature_key] = step_number
        session[PROGRESS_KEY] = progress
        session.modified = True


def _feature_progress(feature_key, total):
    seen = int(_get_progress().get(feature_key, 0) or 0)
    seen = min(seen, total) if total else 0
    pct = int(round((seen / total) * 100)) if total else 0
    return {
        'seen':     seen,
        'total':    total,
        'percent':  pct,
        'complete': bool(total) and seen >= total,
    }


def _overall_progress():
    features = registry.all_features()
    total = sum(f.step_count for f in features)
    if not total:
        return {'seen': 0, 'total': 0, 'percent': 0, 'complete': False}
    progress = _get_progress()
    seen = 0
    for f in features:
        seen += min(int(progress.get(f.key, 0) or 0), f.step_count)
    return {
        'seen':     seen,
        'total':    total,
        'percent':  int(round((seen / total) * 100)),
        'complete': seen >= total,
    }


def _tour_progress():
    seq = registry.tour_sequence()
    total = len(seq)
    if not total:
        return {'position': 0, 'total': 0, 'percent': 0, 'complete': False}
    progress = _get_progress()
    reached = 0
    for i, entry in enumerate(seq):
        if int(progress.get(entry.feature_key, 0) or 0) >= entry.step_number:
            reached = i + 1
        else:
            break
    return {
        'position': reached,
        'total':    total,
        'percent':  int(round((reached / total) * 100)),
        'complete': reached >= total,
    }


# ============================================================
# TIER GATING
# ============================================================

def _user_tier_level():
    try:
        from tier_config import normalize_tier
        return TIER_LEVEL.get(normalize_tier(session.get('tier')), 0)
    except Exception:
        return 0


def _feature_accessible(feature):
    return _user_tier_level() >= TIER_LEVEL.get(feature.tier, 0)


# ============================================================
# SIDEBAR / BREADCRUMB
# ============================================================

def _build_sidebar(current_key=None):
    progress = _get_progress()
    sections = []
    for phase in registry.all_phases():
        items = []
        for f in registry.features_for_phase(phase.key):
            seen = min(int(progress.get(f.key, 0) or 0), f.step_count)
            items.append({
                'key':        f.key,
                'icon':       f.icon,
                'title':      f.title,
                'tier':       f.tier,
                'step_count': f.step_count,
                'seen':       seen,
                'complete':   seen >= f.step_count,
                'accessible': _feature_accessible(f),
                'active':     f.key == current_key,
            })
        sections.append({
            'key':      phase.key,
            'number':   phase.number,
            'title':    phase.title,
            'features': items,
        })
    return sections


def _build_breadcrumb(feature=None, step=None):
    crumbs = [{'label': 'docs.breadcrumb.home', 'url': url_for('docs.home')}]
    if feature:
        crumbs.append({
            'label': feature.title,
            'url':   url_for('docs.feature_start', feature_key=feature.key),
        })
    if feature and step:
        crumbs.append({
            'label': f'Step {step.number}',
            'url':   None,
        })
    return crumbs


# ============================================================
# HOME / ROADMAP / SEARCH
# ============================================================

@docs_bp.route('/')
def home():
    feature_cards = []
    for f in registry.all_features():
        feature_cards.append({
            'key':        f.key,
            'icon':       f.icon,
            'title':      f.title,
            'tagline':    f.tagline,
            'tier':       f.tier,
            'step_count': f.step_count,
            'accessible': _feature_accessible(f),
            **_feature_progress(f.key, f.step_count),
        })

    return render_template(
        'docs/home.html',
        features=feature_cards,
        phases=registry.all_phases(),
        sidebar=_build_sidebar(),
        overall=_overall_progress(),
        tour=_tour_progress(),
    )


@docs_bp.route('/roadmap')
def roadmap():
    phases_data = []
    for p in registry.all_phases():
        phases_data.append({
            'key':     p.key,
            'number':  p.number,
            'title':   p.title,
            'summary': p.summary,
            'features': [
                {
                    'key':        f.key,
                    'icon':       f.icon,
                    'title':      f.title,
                    'tagline':    f.tagline,
                    'tier':       f.tier,
                    'step_count': f.step_count,
                    'accessible': _feature_accessible(f),
                }
                for f in registry.features_for_phase(p.key)
            ],
        })
    return render_template(
        'docs/roadmap.html',
        phases=phases_data,
        sidebar=_build_sidebar(),
        overall=_overall_progress(),
    )


@docs_bp.route('/search')
def search():
    q = (request.args.get('q') or '').strip()
    q_lower = q.lower()
    results = []

    if q_lower:
        for f in registry.all_features():
            haystack = ' '.join([
                f.key.lower(),
                (f.title or '').lower(),
                (f.tagline or '').lower(),
            ])
            if q_lower in haystack:
                results.append({
                    'feature_key': f.key,
                    'icon':        f.icon,
                    'title':       f.title,
                    'tagline':     f.tagline,
                    'accessible':  _feature_accessible(f),
                })

    return render_template(
        'docs/search.html',
        query=q,
        results=results,
        suggestions=[],
        not_found=False,
        sidebar=_build_sidebar(),
    )


# ============================================================
# GUIDED TOUR
# ============================================================

@docs_bp.route('/tour')
def tour_start():
    seq = registry.tour_sequence()
    if not seq:
        return redirect(url_for('docs.home'))
    return redirect(url_for('docs.tour_step', n=1))


@docs_bp.route('/tour/<int:n>')
def tour_step(n):
    seq = registry.tour_sequence()
    total = len(seq)
    if not total:
        return redirect(url_for('docs.home'))

    if n < 1:
        return redirect(url_for('docs.tour_step', n=1))
    if n > total:
        return redirect(url_for('docs.tour_step', n=total))

    entry = seq[n - 1]
    feature = registry.get_feature(entry.feature_key)
    if not feature:
        abort(404)
    step = feature.get_step(entry.step_number)
    if not step:
        abort(404)

    _mark_step(feature.key, step.number)

    return render_template(
        'docs/tour.html',
        step=step,
        feature=feature,
        mockup_template=registry.mockup_template(step.mockup),
        mock=docs_context.MOCK,
        tour={
            'position': n,
            'total':    total,
            'percent':  int(round((n / total) * 100)),
            'prev_url': url_for('docs.tour_step', n=n - 1) if n > 1 else None,
            'next_url': url_for('docs.tour_step', n=n + 1) if n < total else None,
        },
        progress=_tour_progress(),
        sidebar=_build_sidebar(current_key=feature.key),
        breadcrumb=_build_breadcrumb(feature, step),
        feature_accessible=_feature_accessible(feature),
    )


# ============================================================
# FEATURES
# ============================================================

@docs_bp.route('/<feature_key>')
def feature_start(feature_key):
    if feature_key in RESERVED:
        abort(404)
    feature = registry.get_feature(feature_key)
    if not feature:
        return _unknown_feature(feature_key)
    return redirect(url_for('docs.feature_step', feature_key=feature_key, n=1))


@docs_bp.route('/<feature_key>/<int:n>')
def feature_step(feature_key, n):
    if feature_key in RESERVED:
        abort(404)

    feature = registry.get_feature(feature_key)
    if not feature:
        return _unknown_feature(feature_key)

    total = feature.step_count
    if not total:
        return redirect(url_for('docs.home'))

    if n < 1:
        return redirect(url_for('docs.feature_step', feature_key=feature_key, n=1))
    if n > total:
        return redirect(url_for('docs.feature_step', feature_key=feature_key, n=total))

    step = feature.get_step(n)
    _mark_step(feature.key, step.number)

    related = []
    for rk in feature.related:
        rf = registry.get_feature(rk)
        if rf:
            related.append({
                'key':        rf.key,
                'icon':       rf.icon,
                'title':      rf.title,
                'tagline':    rf.tagline,
                'accessible': _feature_accessible(rf),
            })

    return render_template(
        'docs/feature.html',
        step=step,
        feature=feature,
        mockup_template=registry.mockup_template(step.mockup),
        mock=docs_context.MOCK,
        progress=_feature_progress(feature_key, total),
        sidebar=_build_sidebar(current_key=feature_key),
        breadcrumb=_build_breadcrumb(feature, step),
        prev_url=(
            url_for('docs.feature_step', feature_key=feature_key, n=n - 1)
            if n > 1 else None
        ),
        next_url=(
            url_for('docs.feature_step', feature_key=feature_key, n=n + 1)
            if n < total else None
        ),
        related=related,
        is_last_step=(n == total),
        feature_accessible=_feature_accessible(feature),
    )


# ============================================================
# RESET PROGRESS
# ============================================================

@docs_bp.route('/reset-progress', methods=['POST'])
def reset_progress():
    session.pop(PROGRESS_KEY, None)
    session.modified = True
    return redirect(url_for('docs.home'))


# ============================================================
# HELPERS
# ============================================================

def _unknown_feature(feature_key):
    keys = registry.feature_keys()
    suggestions = [
        k for k in keys
        if k.startswith(feature_key[:1]) or feature_key[:3] in k
    ][:5]

    return render_template(
        'docs/search.html',
        query=feature_key,
        results=[],
        suggestions=suggestions,
        not_found=True,
        sidebar=_build_sidebar(),
    ), 404