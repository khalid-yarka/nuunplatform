# docs/content/getting_started.py
# ============================================================
# Three features: register, login, first-look.
#
# Every step must:
#   - reference a real mockup id (see docs/registry.py MOCKUP_TEMPLATES)
#   - reference hotspot names that exist in that mockup template
#     as `data-docs-anchor="..."` attributes.
#
# Step numbering is auto-assigned by Feature.__post_init__ —
# do NOT pass `number=`.
# ============================================================

from docs.models import Feature, Step


# ------------------------------------------------------------
# REGISTER
# ------------------------------------------------------------

register = Feature(
    key='register',
    title='docs.register.title',
    tagline='docs.register.tagline',
    icon='📝',
    phase='phase-1-core',
    tier='free',
    steps=(
        Step(
            title='docs.register.step1.title',
            action='docs.register.step1.action',
            mockup='login_page',
            hotspots=('register_link',),
            try_url='/register',
        ),
        Step(
            title='docs.register.step2.title',
            action='docs.register.step2.action',
            mockup='register_page',
            hotspots=('phone_field', 'password_field'),
            tip='docs.register.step2.tip',
            try_url='/register',
        ),
        Step(
            title='docs.register.step3.title',
            action='docs.register.step3.action',
            mockup='register_page',
            hotspots=('name_field', 'location_field', 'city_field'),
            try_url='/register',
        ),
        Step(
            title='docs.register.step4.title',
            action='docs.register.step4.action',
            mockup='register_page',
            hotspots=('school_field', 'grade_field', 'submit_btn'),
            tip='docs.register.step4.tip',
            try_url='/register',
        ),
    ),
    prerequisites=(),
    related=('login', 'first-look'),
)


# ------------------------------------------------------------
# LOGIN
# ------------------------------------------------------------

login = Feature(
    key='login',
    title='docs.login.title',
    tagline='docs.login.tagline',
    icon='🔑',
    phase='phase-1-core',
    tier='free',
    steps=(
        Step(
            title='docs.login.step1.title',
            action='docs.login.step1.action',
            mockup='login_page',
            hotspots=('phone_field', 'password_field'),
            try_url='/login',
        ),
        Step(
            title='docs.login.step2.title',
            action='docs.login.step2.action',
            mockup='login_page',
            hotspots=('submit_btn',),
            tip='docs.login.step2.tip',
            try_url='/login',
        ),
    ),
    prerequisites=('register',),
    related=('first-look',),
)


# ------------------------------------------------------------
# FIRST LOOK — orientation on the home dashboard
# ------------------------------------------------------------

first_look = Feature(
    key='first-look',
    title='docs.first-look.title',
    tagline='docs.first-look.tagline',
    icon='🏠',
    phase='phase-1-core',
    tier='free',
    steps=(
        Step(
            title='docs.first-look.step1.title',
            action='docs.first-look.step1.action',
            mockup='home_page',
            hotspots=('home_hero',),
            try_url='/home',
        ),
        Step(
            title='docs.first-look.step2.title',
            action='docs.first-look.step2.action',
            mockup='home_page',
            hotspots=('stats_grid',),
            tip='docs.first-look.step2.tip',
            try_url='/home',
        ),
        Step(
            title='docs.first-look.step3.title',
            action='docs.first-look.step3.action',
            mockup='home_page',
            hotspots=(
                'sidebar_quiz_link',
                'sidebar_live_link',
                'sidebar_focus_link',
                'sidebar_pdfs_link',
                'sidebar_groups_link',
            ),
            tip='docs.first-look.step3.tip',
            try_url='/home',
        ),
    ),
    prerequisites=('login',),
    related=('take-a-quiz', 'live-quiz-join'),
)


# ------------------------------------------------------------
# EXPORT
# ------------------------------------------------------------

FEATURES = [
    register,
    login,
    first_look,
]