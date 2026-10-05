# subjects_config.py
# Static subject definitions – no database tables.

SUBJECTS = {
    'mathematics': {'name': 'Mathematics',        'name_so': 'Xisaab',       'icon': '📐'},
    'english':     {'name': 'English',            'name_so': 'Af Ingiriis',  'icon': '🇬🇧'},
    'af_somali':   {'name': 'Af-Somali',          'name_so': 'Af Soomaali',  'icon': '🇸🇴'},
    'somali':      {'name': 'Somali',             'name_so': 'Af Soomaali',  'icon': '🇸🇴'},
    'arabic':      {'name': 'Arabic',             'name_so': 'Af Carabi',    'icon': '📖'},
    'islamic':     {'name': 'Islamic Studies',    'name_so': 'Tarbiya',      'icon': '🕌'},
    'geography':   {'name': 'Geography',          'name_so': 'Juqraafi',     'icon': '🌍'},
    'history':     {'name': 'History',            'name_so': 'Taariikh',     'icon': '📜'},
    'physics':     {'name': 'Physics',            'name_so': 'Fiisikis',     'icon': '⚛️'},
    'chemistry':   {'name': 'Chemistry',          'name_so': 'Kimistari',    'icon': '🧪'},
    'biology':     {'name': 'Biology',            'name_so': 'Bayooloji',    'icon': '🧬'},
    'ict':         {'name': 'ICT',                'name_so': 'Tiknooloji',   'icon': '💻'},
    'business':    {'name': 'Business',           'name_so': 'Ganacsi',      'icon': '📊'},
    'gp':          {'name': 'Government & Policy','name_so': 'G.P',          'icon': '🏛️'},
    'agriculture': {'name': 'Agriculture',        'name_so': 'Beeraha',      'icon': '🌾'},
}

LOCATION_CURRICULA = {
    'PL': [
        {
            'id': 'general',
            'label': 'General',
            'subjects': [
                'arabic', 'islamic', 'af_somali', 'english', 'mathematics',
                'ict', 'geography', 'history', 'physics', 'chemistry', 'biology'
            ]
        },
        {
            'id': 'science',
            'label': 'Science',
            'subjects': [
                'islamic', 'arabic', 'english', 'chemistry', 'biology',
                'business', 'somali', 'ict', 'physics', 'mathematics'
            ]
        },
        {
            'id': 'arts',
            'label': 'Arts',
            'subjects': [
                'english', 'mathematics', 'af_somali', 'arabic', 'geography',
                'gp', 'history', 'ict', 'agriculture', 'islamic'
            ]
        }
    ],
    'SL': [
        {
            'id': 'default',
            'label': 'Default',
            'subjects': [
                'geography', 'history', 'af_somali', 'arabic', 'english',
                'islamic', 'chemistry', 'physics', 'ict', 'mathematics'
            ]
        }
    ],
    'SO': [
        {
            'id': 'default',
            'label': 'Default',
            'subjects': [
                'islamic', 'arabic', 'mathematics', 'history', 'physics',
                'ict',
                'geography', 'biology', 'english',
                'chemistry', 'somali', 'business'
            ]
        }
    ]
}

def get_subject(code):
    """Return subject dict or None."""
    return SUBJECTS.get(code)

def get_subjects_for_user(location, curriculum=None):
    """
    Return list of subject codes for the given location and curriculum.
    If curriculum is None and multiple curricula exist, use the first.
    """
    curricula = LOCATION_CURRICULA.get(location, [])
    if not curricula:
        return []
    if curriculum is None:
        return curricula[0]['subjects']
    for c in curricula:
        if c['id'] == curriculum:
            return c['subjects']
    return curricula[0]['subjects']

def get_all_subject_codes():
    """Return all subject codes (for filter dropdowns, etc.)."""
    return list(SUBJECTS.keys())

def get_all_subjects():
    """Return all subject dicts (for admin or global use)."""
    return [{'code': code, 'name': data['name'], 'icon': data.get('icon', '📚')} 
            for code, data in SUBJECTS.items()]



def get_subject_display_name(code, location):
    """
    Return the display name for a subject code, resolved against the
    user's location.

    SO users get the Somali exam names from the 2025–2026 federal
    schedule. Every other location returns the English catalogue name.

    Falls back to the raw code when the subject is unknown, so a
    missing entry never blanks out the UI.
    """
    subj = SUBJECTS.get(code)
    if not subj:
        return code
    if location == 'SO':
        return subj.get('name_so') or subj['name']
    return subj['name']