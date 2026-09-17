# docs/content/learning.py
# ============================================================
# Eight learning-related features.
#
# Phase 1 (core):    take-a-quiz, pdfs, groups
# Phase 2 (live):    live-quiz-join, live-quiz-host, focus,
#                    history, leaderboard
#
# Anchor names must exist as data-docs-anchor="..." in the
# corresponding mockup templates.
# ============================================================

from docs.models import Feature, Step


# ------------------------------------------------------------
# TAKE-A-QUIZ
# ------------------------------------------------------------

take_a_quiz = Feature(
    key='take-a-quiz',
    title='docs.take-a-quiz.title',
    tagline='docs.take-a-quiz.tagline',
    icon='📝',
    phase='phase-1-core',
    tier='free',
    steps=(
        Step(
            title='docs.take-a-quiz.step1.title',
            action='docs.take-a-quiz.step1.action',
            mockup='quiz_setup',
            hotspots=('setup_card', 'subject_select'),
            try_url='/quiz/',
        ),
        Step(
            title='docs.take-a-quiz.step2.title',
            action='docs.take-a-quiz.step2.action',
            mockup='quiz_setup',
            hotspots=('count_options', 'start_btn'),
            tip='docs.take-a-quiz.step2.tip',
            try_url='/quiz/',
        ),
        Step(
            title='docs.take-a-quiz.step3.title',
            action='docs.take-a-quiz.step3.action',
            mockup='quiz_play',
            hotspots=('question_card', 'option_a', 'option_b', 'option_c'),
            try_url='/quiz/play',
        ),
        Step(
            title='docs.take-a-quiz.step4.title',
            action='docs.take-a-quiz.step4.action',
            mockup='quiz_play',
            hotspots=('reaction_like', 'reaction_save', 'reaction_report'),
            tip='docs.take-a-quiz.step4.tip',
            try_url='/quiz/play',
        ),
        Step(
            title='docs.take-a-quiz.step5.title',
            action='docs.take-a-quiz.step5.action',
            mockup='quiz_play',
            hotspots=('next_btn', 'skip_btn', 'end_btn'),
            tip='docs.take-a-quiz.step5.tip',
            try_url='/quiz/play',
        ),
        Step(
            title='docs.take-a-quiz.step6.title',
            action='docs.take-a-quiz.step6.action',
            mockup='quiz_results',
            hotspots=('score_ring', 'result_message'),
            try_url='/quiz/results',
        ),
    ),
    prerequisites=('first-look',),
    related=('focus', 'leaderboard', 'history'),
)


# ------------------------------------------------------------
# PDFS
# ------------------------------------------------------------

pdfs = Feature(
    key='pdfs',
    title='docs.pdfs.title',
    tagline='docs.pdfs.tagline',
    icon='📄',
    phase='phase-1-core',
    tier='free',
    steps=(
        Step(
            title='docs.pdfs.step1.title',
            action='docs.pdfs.step1.action',
            mockup='pdfs_page',
            hotspots=('pdf_grid', 'pdf_card'),
            try_url='/pdfs/',
        ),
        Step(
            title='docs.pdfs.step2.title',
            action='docs.pdfs.step2.action',
            mockup='pdfs_page',
            hotspots=('search_input', 'subject_filter'),
            tip='docs.pdfs.step2.tip',
            try_url='/pdfs/',
        ),
        Step(
            title='docs.pdfs.step3.title',
            action='docs.pdfs.step3.action',
            mockup='pdfs_page',
            hotspots=('read_btn', 'download_btn', 'preview_btn', 'telegram_btn'),
            tip='docs.pdfs.step3.tip',
            try_url='/pdfs/',
        ),
    ),
    prerequisites=('first-look',),
    related=('focus',),
)


# ------------------------------------------------------------
# GROUPS
# ------------------------------------------------------------

groups = Feature(
    key='groups',
    title='docs.groups.title',
    tagline='docs.groups.tagline',
    icon='👥',
    phase='phase-1-core',
    tier='free',
    steps=(
        Step(
            title='docs.groups.step1.title',
            action='docs.groups.step1.action',
            mockup='groups_page',
            hotspots=('curriculum_tabs', 'featured_section'),
            try_url='/groups/',
        ),
        Step(
            title='docs.groups.step2.title',
            action='docs.groups.step2.action',
            mockup='groups_page',
            hotspots=('filter_bar', 'group_card'),
            tip='docs.groups.step2.tip',
            try_url='/groups/',
        ),
        Step(
            title='docs.groups.step3.title',
            action='docs.groups.step3.action',
            mockup='groups_page',
            hotspots=('join_btn',),
            try_url='/groups/',
        ),
    ),
    prerequisites=('first-look',),
    related=(),
)


# ------------------------------------------------------------
# LIVE-QUIZ-JOIN
# ------------------------------------------------------------

live_quiz_join = Feature(
    key='live-quiz-join',
    title='docs.live-quiz-join.title',
    tagline='docs.live-quiz-join.tagline',
    icon='⚡',
    phase='phase-2-live',
    tier='premium',
    steps=(
        Step(
            title='docs.live-quiz-join.step1.title',
            action='docs.live-quiz-join.step1.action',
            mockup='live_lobby',
            hotspots=('stats_row', 'quiz_card'),
            try_url='/live-quiz/lobby',
        ),
        Step(
            title='docs.live-quiz-join.step2.title',
            action='docs.live-quiz-join.step2.action',
            mockup='live_lobby',
            hotspots=('search_input', 'status_filter'),
            tip='docs.live-quiz-join.step2.tip',
            try_url='/live-quiz/lobby',
        ),
        Step(
            title='docs.live-quiz-join.step3.title',
            action='docs.live-quiz-join.step3.action',
            mockup='live_join',
            hotspots=('code_input', 'join_btn'),
            try_url='/live-quiz/join',
        ),
        Step(
            title='docs.live-quiz-join.step4.title',
            action='docs.live-quiz-join.step4.action',
            mockup='live_waiting',
            hotspots=('quiz_info', 'join_code', 'participants_list'),
            tip='docs.live-quiz-join.step4.tip',
            try_url='/live-quiz/waiting-room/1',
        ),
        Step(
            title='docs.live-quiz-join.step5.title',
            action='docs.live-quiz-join.step5.action',
            mockup='live_waiting',
            hotspots=('ready_btn', 'leave_btn'),
            try_url='/live-quiz/waiting-room/1',
        ),
        Step(
            title='docs.live-quiz-join.step6.title',
            action='docs.live-quiz-join.step6.action',
            mockup='live_play',
            hotspots=('timer_display', 'question_card', 'option_a', 'option_b', 'option_c'),
            try_url='/live-quiz/play/1',
        ),
        Step(
            title='docs.live-quiz-join.step7.title',
            action='docs.live-quiz-join.step7.action',
            mockup='live_play',
            hotspots=('leaderboard_mini', 'participant_progress'),
            tip='docs.live-quiz-join.step7.tip',
            try_url='/live-quiz/play/1',
        ),
    ),
    prerequisites=('first-look',),
    related=('live-quiz-host', 'leaderboard', 'history'),
)


# ------------------------------------------------------------
# LIVE-QUIZ-HOST
# ------------------------------------------------------------

live_quiz_host = Feature(
    key='live-quiz-host',
    title='docs.live-quiz-host.title',
    tagline='docs.live-quiz-host.tagline',
    icon='🎯',
    phase='phase-2-live',
    tier='premium',
    steps=(
        Step(
            title='docs.live-quiz-host.step1.title',
            action='docs.live-quiz-host.step1.action',
            mockup='live_lobby',
            hotspots=('create_btn',),
            tip='docs.live-quiz-host.step1.tip',
            try_url='/live-quiz/create',
        ),
        Step(
            title='docs.live-quiz-host.step2.title',
            action='docs.live-quiz-host.step2.action',
            mockup='live_lobby',
            hotspots=('quiz_card',),
            try_url='/live-quiz/create',
        ),
        Step(
            title='docs.live-quiz-host.step3.title',
            action='docs.live-quiz-host.step3.action',
            mockup='live_waiting',
            hotspots=('join_code', 'copy_btn', 'share_btn'),
            tip='docs.live-quiz-host.step3.tip',
            try_url='/live-quiz/waiting-room/1',
        ),
        Step(
            title='docs.live-quiz-host.step4.title',
            action='docs.live-quiz-host.step4.action',
            mockup='live_waiting',
            hotspots=('participants_list', 'start_btn', 'delete_btn'),
            try_url='/live-quiz/waiting-room/1',
        ),
        Step(
            title='docs.live-quiz-host.step5.title',
            action='docs.live-quiz-host.step5.action',
            mockup='live_play',
            hotspots=('quiz_header', 'total_timer', 'participant_progress'),
            try_url='/live-quiz/play/1',
        ),
    ),
    prerequisites=('first-look',),
    related=('live-quiz-join',),
)


# ------------------------------------------------------------
# FOCUS
# ------------------------------------------------------------

focus = Feature(
    key='focus',
    title='docs.focus.title',
    tagline='docs.focus.tagline',
    icon='🎯',
    phase='phase-2-live',
    tier='premium',
    steps=(
        Step(
            title='docs.focus.step1.title',
            action='docs.focus.step1.action',
            mockup='focus_page',
            hotspots=('focus_hero',),
            try_url='/focus/',
        ),
        Step(
            title='docs.focus.step2.title',
            action='docs.focus.step2.action',
            mockup='focus_page',
            hotspots=('sources_section', 'source_card'),
            tip='docs.focus.step2.tip',
            try_url='/focus/',
        ),
        Step(
            title='docs.focus.step3.title',
            action='docs.focus.step3.action',
            mockup='focus_page',
            hotspots=('bookmarks_section', 'bookmark_card'),
            try_url='/focus/',
        ),
        Step(
            title='docs.focus.step4.title',
            action='docs.focus.step4.action',
            mockup='focus_page',
            hotspots=('analytics_section',),
            try_url='/focus/',
        ),
        Step(
            title='docs.focus.step5.title',
            action='docs.focus.step5.action',
            mockup='focus_page',
            hotspots=('tips_section',),
            tip='docs.focus.step5.tip',
            try_url='/focus/',
        ),
    ),
    prerequisites=('take-a-quiz',),
    related=('pdfs', 'history'),
)


# ------------------------------------------------------------
# HISTORY
# ------------------------------------------------------------

history = Feature(
    key='history',
    title='docs.history.title',
    tagline='docs.history.tagline',
    icon='📊',
    phase='phase-2-live',
    tier='free',
    steps=(
        Step(
            title='docs.history.step1.title',
            action='docs.history.step1.action',
            mockup='history_page',
            hotspots=('tier_panel',),
            tip='docs.history.step1.tip',
            try_url='/history/',
        ),
        Step(
            title='docs.history.step2.title',
            action='docs.history.step2.action',
            mockup='history_page',
            hotspots=('stats_row',),
            try_url='/history/',
        ),
        Step(
            title='docs.history.step3.title',
            action='docs.history.step3.action',
            mockup='history_page',
            hotspots=('filter_bar',),
            try_url='/history/',
        ),
        Step(
            title='docs.history.step4.title',
            action='docs.history.step4.action',
            mockup='history_page',
            hotspots=('timeline', 'load_more'),
            try_url='/history/',
        ),
        Step(
            title='docs.history.step5.title',
            action='docs.history.step5.action',
            mockup='history_page',
            hotspots=('export_btn',),
            tip='docs.history.step5.tip',
            try_url='/history/',
        ),
    ),
    prerequisites=('first-look',),
    related=('focus', 'leaderboard'),
)


# ------------------------------------------------------------
# LEADERBOARD
# ------------------------------------------------------------

leaderboard = Feature(
    key='leaderboard',
    title='docs.leaderboard.title',
    tagline='docs.leaderboard.tagline',
    icon='🏆',
    phase='phase-2-live',
    tier='free',
    steps=(
        Step(
            title='docs.leaderboard.step1.title',
            action='docs.leaderboard.step1.action',
            mockup='leaderboard_page',
            hotspots=('user_rank',),
            try_url='/quiz/leaderboard',
        ),
        Step(
            title='docs.leaderboard.step2.title',
            action='docs.leaderboard.step2.action',
            mockup='leaderboard_page',
            hotspots=('podium',),
            try_url='/quiz/leaderboard',
        ),
        Step(
            title='docs.leaderboard.step3.title',
            action='docs.leaderboard.step3.action',
            mockup='leaderboard_page',
            hotspots=('table', 'you_row'),
            tip='docs.leaderboard.step3.tip',
            try_url='/quiz/leaderboard',
        ),
    ),
    prerequisites=('take-a-quiz',),
    related=('history',),
)


# ------------------------------------------------------------
# EXPORT
# ------------------------------------------------------------

FEATURES = [
    take_a_quiz,
    pdfs,
    groups,
    live_quiz_join,
    live_quiz_host,
    focus,
    history,
    leaderboard,
]