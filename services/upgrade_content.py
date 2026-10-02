# services/upgrade_content.py
# ------------------------------------------------------------------
# Curated comparison content for the upgrade sheet.
#
# Free list is intentionally short — 4 rows. Its job is to remind
# the user what they already have, not to be an exhaustive
# reference. The Premium list carries the selling weight.
#
# Edit the lists here to change what appears in the upgrade sheet.
# Both languages live in one place so they never drift.
# ------------------------------------------------------------------


UPGRADE_CONTENT = {
    'en': {
        'free_title':        'What you already have',
        'free_subtitle':     'Free · included on every account',
        'premium_title':     'What you unlock',
        'premium_subtitle':  'Everything in Free, plus the extras.',
        'free_features': [
            'Unlimited practice sessions',
            'Every competition & group',
            '3 PDF downloads per day',
            'Basic statistics & answer review',
        ],
        'premium_features': [
            'The full PDF library unlocked',
            'Unlimited PDF downloads',
            'Direct downloads — 20 per day',
            'Progress, subject & performance charts',
            'Personal learning insights',
            'Detailed ranking & 30 focus suggestions',
            '200 saved questions — 20× more',
            'History search, trends & CSV export',
            '180 days of history',
            'Badge showcase & public ID control',
            'Full appearance customisation',
        ],
    },

    'so': {
        'free_title':        'Waxa aad hore u haysato',
        'free_subtitle':     'Bilaash · lagu daray akoon kasta',
        'premium_title':     'Waxa aad furto',
        'premium_subtitle':  'Wax kasta oo Bilaash, iyo wax dheeraad ah.',
        'free_features': [
            'Tababar aan xadidnayn',
            'Tartamo & kooxo kasta',
            '3 PDF oo maalintii',
            'Tirakoobka aasaasiga ah',
        ],
        'premium_features': [
            'Maktabadda PDF-yada oo buuxda',
            'PDF-yo aan xadidnayn',
            'Toos u soo deji — 20 maalintii',
            'Jaantusyada horumarka & maadooyinka',
            'Fikrado gaar ah oo waxbarasho',
            'Qiimeyn faahfaahsan & 30 talooyin',
            '200 su\'aalo oo la keydiyay',
            'Raadin, isbeddel & soo dejinta taariikhda',
            '180 maalmood oo taariikh ah',
            'Bandhig guulaha & maamulka aqoonsiga',
            'Isku beddelka muuqaalka oo buuxa',
        ],
    },
}


def get_content(lang: str = 'en') -> dict:
    """Return the upgrade content for the given language, falling back to English."""
    if not lang:
        lang = 'en'
    return UPGRADE_CONTENT.get(lang, UPGRADE_CONTENT['en'])