# docs/context.py
# ============================================================
# Fake data for the mockup templates.
#
# Every mockup is a Jinja template that mirrors a real
# dashboard page. It needs the same shape of data the real
# page receives — but without touching the DB, Flask session,
# or any service.
#
# This module exposes a single dict `MOCK` plus module-level
# names for the most-used pieces. Mockup templates receive
# these values automatically via the docs route handler.
# ============================================================

# ---- User --------------------------------------------------

MOCK_USER = {
    'id': 1,
    'first_name': 'Amina',
    'middle_name': 'Hassan',
    'last_name': 'Omar',
    'name': 'Amina Hassan Omar',
    'public_id': 'A4K7',
    'phone': '+252 61 234 5678',
    'location': 'SO',
    'city': 'Mogadishu',
    'school': 'Hodan Secondary School',
    'grade': 'F4',
    'tier': 'premium',
    'is_verified': True,
    'is_admin': False,
    'total_points': 128,
}


# ---- Stats / dashboard numbers -----------------------------

MOCK_STATS = {
    'mastery_pct':      72,
    'correct_answers':  48,
    'quizzes_taken':    12,
    'subjects_attempted': 6,
    'streak_days':      5,
    'level':            13,
    'total_points':     128,
    'xp_in_level':      8,
    'xp_needed':        10,
    'xp_to_next':       2,
    'xp_percent':       80,
    'quiz_remaining':   7,
    'history_retention': 180,
    'history_max':      500,
}


# ---- Quiz (regular) ----------------------------------------

MOCK_QUIZ = {
    'id': 901,
    'subject_code': 'mathematics',
    'subject_name': 'Mathematics',
    'question_count': 10,
    'current_index': 2,       # 0-based
    'score': 2,
    'question': {
        'id': 401,
        'text': 'What is 15% of 240?',
        'options': {
            'A': '24',
            'B': '36',
            'C': '40',
            'D': '48',
        },
        'correct_answer': 'B',
    },
    'feedback': {
        'correct': True,
        'explanation': '15% of 240 = 0.15 × 240 = 36.',
    },
}


# ---- Quiz (live) -------------------------------------------

MOCK_LIVE = {
    'id': 502,
    'title': 'Friday Challenge',
    'subject_name': 'Mathematics',
    'join_code': 'A3B9-X7K2',
    'question_count': 10,
    'time_per_question': 30,
    'is_public': True,
    'participants': [
        {'name': 'Amina H.', 'public_id': 'A4K7', 'is_you': True,  'ready': True,  'status': 'active'},
        {'name': 'Yusuf M.', 'public_id': 'B2N9', 'is_you': False, 'ready': True,  'status': 'active'},
        {'name': 'Sagal A.', 'public_id': 'C8P1', 'is_you': False, 'ready': False, 'status': 'active'},
    ],
    'leaderboard': [
        {'rank': 1, 'name': 'Amina H.', 'score': 8,  'is_you': True},
        {'rank': 2, 'name': 'Yusuf M.', 'score': 6,  'is_you': False},
        {'rank': 3, 'name': 'Sagal A.', 'score': 4,  'is_you': False},
    ],
    'current_question': {
        'index': 3,
        'total': 10,
        'text': 'Which planet is known as the Red Planet?',
        'options': {'A': 'Venus', 'B': 'Mars', 'C': 'Jupiter'},
    },
}


# ---- Content lists -----------------------------------------

MOCK_SUBJECTS = [
    {'code': 'mathematics', 'name': 'Mathematics', 'icon': '📐'},
    {'code': 'english',     'name': 'English',     'icon': '🇬🇧'},
    {'code': 'physics',     'name': 'Physics',     'icon': '⚛️'},
    {'code': 'biology',     'name': 'Biology',     'icon': '🧬'},
    {'code': 'chemistry',   'name': 'Chemistry',   'icon': '🧪'},
    {'code': 'history',     'name': 'History',     'icon': '📜'},
]

MOCK_PDFS = [
    {'id': 1, 'code': 'MATH-CH3-A1B2', 'title': 'Algebra — Chapter 3',        'subject': 'Mathematics', 'class': 'F4', 'views': 142, 'is_premium': False},
    {'id': 2, 'code': 'PHYS-CH5-C3D4', 'title': 'Forces and Motion',          'subject': 'Physics',     'class': 'F4', 'views': 98,  'is_premium': True},
    {'id': 3, 'code': 'BIOL-CH2-E5F6', 'title': 'Cell Structure',             'subject': 'Biology',     'class': 'F3', 'views': 76,  'is_premium': False},
    {'id': 4, 'code': 'CHEM-CH1-G7H8', 'title': 'Periodic Table Basics',      'subject': 'Chemistry',   'class': 'F4', 'views': 54,  'is_premium': False},
]

MOCK_GROUPS = [
    {'id': 1, 'name': 'Mogadishu Math Warriors', 'platform': 'whatsapp', 'category': 'Mathematics', 'curriculum': 'SO', 'clicks': 234, 'locked': False},
    {'id': 2, 'name': 'Form 4 Science Squad',    'platform': 'telegram', 'category': 'Science',     'curriculum': 'SO', 'clicks': 189, 'locked': False},
    {'id': 3, 'name': 'Puntland Physics Club',   'platform': 'whatsapp', 'category': 'Physics',     'curriculum': 'PL', 'clicks': 112, 'locked': True},
]

MOCK_LEADERBOARD = [
    {'rank': 1, 'name': 'Yusuf M.',   'school': 'Al-Nuur Secondary', 'public_id': 'B2N9', 'points': 342},
    {'rank': 2, 'name': 'Amina H.',   'school': 'Hodan Secondary',   'public_id': 'A4K7', 'points': 128, 'is_you': True},
    {'rank': 3, 'name': 'Sagal A.',   'school': 'Hodan Secondary',   'public_id': 'C8P1', 'points': 121},
    {'rank': 4, 'name': 'Mohamed K.', 'school': 'Al-Nuur Secondary', 'public_id': 'D5R3', 'points': 98},
    {'rank': 5, 'name': 'Hodan W.',   'school': 'Hodan Secondary',   'public_id': 'E1T8', 'points': 87},
]

MOCK_HISTORY = [
    {'type': 'quiz_attempt', 'icon': '📝', 'title': 'Completed a <strong>Mathematics</strong> quiz', 'meta': '8/10 correct · 80%', 'time': '2h ago',  'points': '+8'},
    {'type': 'achievement',  'icon': '🏆', 'title': 'Unlocked <strong>Quiz Master</strong>',          'meta': 'Badge earned',       'time': '5h ago',  'points': ''},
    {'type': 'live_quiz',    'icon': '⚡', 'title': 'Played a <strong>Physics</strong> live quiz',    'meta': 'Rank #2 of 12',      'time': '1d ago',  'points': '+6'},
    {'type': 'pdf_view',     'icon': '📄', 'title': 'Viewed <strong>Algebra — Chapter 3</strong>',    'meta': 'PDF',                'time': '2d ago',  'points': ''},
]

MOCK_NOTIFICATIONS = [
    {'id': 1, 'icon': '🏆', 'title': 'Achievement unlocked',   'body': 'You earned the Quiz Master badge.',   'unread': True,  'time': '2h ago'},
    {'id': 2, 'icon': '⚡', 'title': 'Live quiz starting soon', 'body': '"Friday Challenge" starts in 10 min.', 'unread': True,  'time': '15m ago'},
    {'id': 3, 'icon': '📄', 'title': 'New PDF uploaded',        'body': 'Physics — Chapter 5 is now available.', 'unread': False, 'time': '1d ago'},
]

MOCK_ACHIEVEMENTS = [
    {'icon': '🥇', 'name': 'First Quiz',     'desc': 'Complete your first quiz',        'earned': True},
    {'icon': '🎯', 'name': 'Quiz Master',    'desc': 'Score 100% on a quiz',            'earned': True},
    {'icon': '⚡', 'name': 'Live Participant','desc': 'Join a live quiz',               'earned': True},
    {'icon': '📚', 'name': 'Studious',       'desc': 'Read 10 PDFs',                    'earned': False},
    {'icon': '🔥', 'name': 'Streak Keeper',  'desc': 'Practice 7 days in a row',        'earned': False},
    {'icon': '👑', 'name': 'Premium Learner','desc': 'Reach Premium tier',             'earned': False},
]


# ---- Aggregate ---------------------------------------------

MOCK = {
    'user':          MOCK_USER,
    'stats':         MOCK_STATS,
    'quiz':          MOCK_QUIZ,
    'live':          MOCK_LIVE,
    'subjects':      MOCK_SUBJECTS,
    'pdfs':          MOCK_PDFS,
    'groups':        MOCK_GROUPS,
    'leaderboard':   MOCK_LEADERBOARD,
    'history':       MOCK_HISTORY,
    'notifications': MOCK_NOTIFICATIONS,
    'achievements':  MOCK_ACHIEVEMENTS,
}