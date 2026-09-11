# subjects_config.py
# Static subject definitions – no database tables.

SUBJECTS = {
    'mathematics': {'name': 'Mathematics', 'name_so': 'Xisaab', 'icon': '📐'},
    'english': {'name': 'English', 'name_so': 'Ingiriis', 'icon': '🇬🇧'},
    'af_somali': {'name': 'Af-Somali', 'name_so': 'Af-Soomaali', 'icon': '🇸🇴'},
    'arabic': {'name': 'Arabic', 'name_so': 'Carabi', 'icon': '📖'},
    'islamic': {'name': 'Islamic Studies', 'name_so': 'Islaamka', 'icon': '🕌'},
    'geography': {'name': 'Geography', 'name_so': 'Juqraafi', 'icon': '🌍'},
    'history': {'name': 'History', 'name_so': 'Taariikh', 'icon': '📜'},
    'physics': {'name': 'Physics', 'name_so': 'Fisikis', 'icon': '⚛️'},
    'chemistry': {'name': 'Chemistry', 'name_so': 'Kimistari', 'icon': '🧪'},
    'biology': {'name': 'Biology', 'name_so': 'Bayloji', 'icon': '🧬'},
    'ict': {'name': 'ICT', 'name_so': 'Teknoloji', 'icon': '💻'},
    'business': {'name': 'Business', 'name_so': 'Ganacsi', 'icon': '📊'},
    'gp': {'name': 'Government and Policy', 'name_so': 'G.P', 'icon': '🏛️'},
    'agriculture': {'name': 'Agriculture', 'name_so': 'Beeraha', 'icon': '🌾'},
    'somali': {'name': 'Somali', 'name_so': 'Soomaali', 'icon': '🇸🇴'},
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