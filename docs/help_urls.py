# docs/help_urls.py
# ============================================================
# Maps the current request to the most relevant docs walkthrough.
# Used by the navbar "?" icon to deep-link contextually.
# ============================================================

def help_url_for_request(request):
    endpoint = request.endpoint or ''
    path = request.path or ''

    if endpoint.startswith('quiz.'):
        if endpoint == 'quiz.leaderboard':
            return '/docs/leaderboard'
        return '/docs/take-a-quiz'

    if endpoint.startswith('live_quiz.'):
        if 'create' in endpoint or 'host' in endpoint:
            return '/docs/live-quiz-host'
        return '/docs/live-quiz-join'

    if endpoint.startswith('pdfs.'):
        return '/docs/pdfs'

    if endpoint.startswith('groups.'):
        return '/docs/groups'

    if endpoint.startswith('focus.'):
        return '/docs/focus'

    if endpoint.startswith('history.'):
        return '/docs/history'

    if endpoint.startswith('settings.'):
        return '/docs/settings'

    if endpoint.startswith('notifications.'):
        return '/docs/notifications'

    if endpoint.startswith('achievements.'):
        return '/docs/achievements'

    if endpoint.startswith('upgrade.'):
        return '/docs/upgrade'

    if endpoint.startswith('profile.') or endpoint == 'dashboard.profile':
        return '/docs/profile'

    if endpoint == 'dashboard.home':
        return '/docs/first-look'

    if endpoint.startswith('auth.'):
        return '/docs/login'

    if endpoint.startswith('saved_content.'):
        return '/docs/focus'

    if endpoint.startswith('interactions.'):
        return '/docs/take-a-quiz'

    return '/docs/'


__all__ = ['help_url_for_request']