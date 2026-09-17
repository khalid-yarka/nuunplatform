#!/usr/bin/env python3
"""
Merge docs.* translation keys into translations/en.json and translations/so.json.

Safe:
  - Preserves every existing key.
  - Only ADDS or UPDATES keys under the `docs.*` namespace.
  - Backs up originals before writing.

Usage:
    python scripts/merge_docs_translations.py
    python scripts/merge_docs_translations.py --dry-run
    python scripts/merge_docs_translations.py --lang en
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TRANS_DIR = BASE_DIR / 'translations'


# ============================================================
# ENGLISH
# ============================================================

EN_UI = {
    "title": "User Guide",
    "badge": "Guide",
    "steps": "steps",

    "nav.roadmap": "Roadmap",
    "nav.tour": "Guided Tour",
    "back_to_dashboard": "Back to Dashboard",
    "login": "Login",
    "register": "Register",

    "breadcrumb.home": "User Guide",

    "footer.help": "Help & FAQ",
    "footer.roadmap": "Roadmap",

    "sidebar.home": "Overview",
    "sidebar.toggle": "Toggle sidebar",
    "tier.locked": "Unavailable on your plan",

    "canvas.label": "Live preview",
    "related.title": "Related features",

    "tour.title": "Guided Tour",
    "tour.ribbon": "Guided Tour · Step {n} of {total}",
    "tour.completed": "Tour complete",

    "progress.step_of": "Step {n} of {total}",
    "progress.complete": "Complete",

    "overview.steps_seen": "Steps seen",
    "overview.features": "Features",
    "overview.overall": "Overall",

    "hero.title": "Learn NuunPlatform, one step at a time",
    "hero.subtitle": "Every feature shown as a guided walkthrough — with visual previews, "
                     "hotspots that point at exactly what to click, and a curated tour "
                     "for first-time users.",
    "hero.start_tour": "Start the Guided Tour",
    "hero.browse": "Browse All Features",

    "home.roadmap_title": "The Roadmap",
    "home.roadmap_sub": "Three phases, from getting started to advanced features.",
    "home.all_features": "All Features",
    "home.all_features_sub": "Click any feature to open its step-by-step walkthrough.",

    "cta.start": "Start",
    "cta.resume": "Resume",
    "cta.review": "Review",
    "cta.prev": "Previous",
    "cta.next": "Next",
    "cta.finish": "Finish",
    "cta.try_it": "Try it on the real site",
    "cta.zoom_in": "Zoom in",

    "roadmap.title": "Roadmap",
    "roadmap.subtitle": "How the platform unfolds — from first login to advanced features.",

    "search.title": "Search the Guide",
    "search.subtitle": "Find any feature by name or keyword.",
    "search.placeholder": "Search features…",
    "search.submit": "Search",
    "search.not_found": "No feature matches that name",
    "search.no_results": "Nothing matched your search",
    "search.try_other": "Try a shorter word, or browse the full list.",
    "search.try_these": "Try one of these:",
    "search.prompt": "Type a keyword above to search the guide.",
}

EN_PHASES = {
    "phase-1-core": {
        "title": "Getting Started",
        "summary": "Register, log in, and take your first quiz. Everything a new user needs.",
    },
    "phase-2-live": {
        "title": "Learn & Compete",
        "summary": "Live quizzes, focus suggestions, history, and ranking.",
    },
    "phase-3-premium": {
        "title": "Your Account",
        "summary": "Profile, settings, notifications, achievements, and upgrades.",
    },
}

EN_FEATURES = {
    'register': {
        'title': 'Create an Account',
        'tagline': 'Join the platform in 4 quick steps.',
        'steps': [
            {'title': 'Open the register page',
             'action': "On the login screen, click 'Create Account' below the form."},
            {'title': 'Fill in your account details',
             'action': "Enter your phone number and choose a strong password.",
             'tip': "Your phone must start with +252 and have 9 digits."},
            {'title': 'Tell us your name and location',
             'action': "Enter your first, middle, and last name — then pick your location and city."},
            {'title': 'Add your school and grade',
             'action': "Choose your school, grade, and confirm.",
             'tip': "All three names are required. Double-check before submitting."},
        ],
    },
    'login': {
        'title': 'Sign In',
        'tagline': 'Return to your dashboard in seconds.',
        'steps': [
            {'title': 'Enter your phone and password',
             'action': "Your phone number is the one you registered with."},
            {'title': 'Click Login',
             'action': "You'll land on your dashboard.",
             'tip': "Forgot your password? Use the WhatsApp contact link in the footer."},
        ],
    },
    'first-look': {
        'title': 'Your Dashboard',
        'tagline': 'A quick tour of everything you can do.',
        'steps': [
            {'title': 'Your hero panel',
             'action': "This is your level, XP progress, and daily greeting."},
            {'title': 'Your stats',
             'action': "Mastery score, quizzes taken, streak, and current level.",
             'tip': "Your streak grows every day you practice — don't break it!"},
            {'title': 'The sidebar',
             'action': "This is how you move between Quizzes, Live Quiz, Focus, PDFs, and Groups.",
             'tip': "You can collapse the sidebar from any page."},
        ],
    },
    'take-a-quiz': {
        'title': 'Take a Quiz',
        'tagline': 'Practice a subject, answer questions, see your score.',
        'steps': [
            {'title': 'Open the quiz setup page',
             'action': "Click Quizzes in the sidebar to see the setup form."},
            {'title': 'Pick a subject and number of questions',
             'action': "Choose 10, 20, 30, or a custom count — depending on your tier.",
             'tip': "Free tier allows 10 questions. Upgrade for more."},
            {'title': 'Answer the questions',
             'action': "Click an option to answer. You'll see feedback immediately."},
            {'title': 'React to each question',
             'action': "Like, save, or report questions you want to revisit.",
             'tip': "Saved questions appear in Focus → Bookmarks."},
            {'title': 'Advance or skip',
             'action': "Skip a question, or click Next to move on. End early if needed.",
             'tip': "Ending early still saves your progress."},
            {'title': 'See your results',
             'action': "Your score, percentage, and any reactions you made."},
        ],
    },
    'pdfs': {
        'title': 'PDF Library',
        'tagline': 'Browse, preview, read, and download study materials.',
        'steps': [
            {'title': 'Open the library',
             'action': "Click PDFs in the sidebar to see all study materials."},
            {'title': 'Filter and search',
             'action': "Search by keyword or filter by subject, class, and curriculum.",
             'tip': "Search is a Premium feature. Free users see the full list."},
            {'title': 'Read, download, or open in Telegram',
             'action': "Use the buttons on each card to open the PDF your way.",
             'tip': "Pro users get direct in-browser reading and download."},
        ],
    },
    'groups': {
        'title': 'Study Groups',
        'tagline': 'Join communities for your curriculum.',
        'steps': [
            {'title': 'Browse by curriculum',
             'action': "Tabs at the top switch between Somalia, Puntland, and Somaliland."},
            {'title': 'Filter and preview',
             'action': "Filter by platform or category. Click any card to see details.",
             'tip': "Featured groups are recommended — they're actively managed."},
            {'title': 'Join the group',
             'action': "Click Join to open the group rules and jump to WhatsApp or Telegram."},
        ],
    },
    'live-quiz-join': {
        'title': 'Join a Live Quiz',
        'tagline': 'Compete in real-time with other students.',
        'steps': [
            {'title': 'Open the lobby',
             'action': "Click Live Quiz in the sidebar to see open games."},
            {'title': 'Find a quiz',
             'action': "Use the search and filters to narrow down by title, status, or subject.",
             'tip': "Only quizzes marked 'Open' accept new participants."},
            {'title': 'Join with a code',
             'action': "If a friend shared a code with you, enter it here."},
            {'title': 'Wait in the lobby',
             'action': "You'll see everyone who's joined. Share the code to invite more.",
             'tip': "The host can't start until at least 2 people are ready."},
            {'title': 'Mark yourself ready',
             'action': "Tap the hand icon to signal you're ready. Leave any time before start."},
            {'title': 'Play in real-time',
             'action': "Answer each question before the timer runs out."},
            {'title': 'Watch the leaderboard',
             'action': "Scores update live as everyone answers.",
             'tip': "The final ranking appears as soon as everyone finishes."},
        ],
    },
    'live-quiz-host': {
        'title': 'Host a Live Quiz',
        'tagline': 'Create, invite, and run a real-time quiz.',
        'steps': [
            {'title': 'Create a quiz',
             'action': "From the lobby, click Create Quiz.",
             'tip': "Hosting is a Premium feature."},
            {'title': 'Set it up',
             'action': "Pick a subject, question count, title, and privacy."},
            {'title': 'Share the code',
             'action': "Your quiz gets a unique code — send it via WhatsApp or Telegram.",
             'tip': "Only public quizzes appear in the lobby."},
            {'title': 'Start when ready',
             'action': "You need at least 2 participants. Start manually or on a timer."},
            {'title': 'Watch the room',
             'action': "See live progress, timer, and rankings as everyone plays."},
        ],
    },
    'focus': {
        'title': 'Focus',
        'tagline': 'Know exactly what to study next.',
        'steps': [
            {'title': 'Your focus panel',
             'action': "At a glance: sources, bookmarks, and analytics level."},
            {'title': 'Suggested sources',
             'action': "PDFs to re-read based on the questions you missed.",
             'tip': "Click a source to jump straight to that page in the library."},
            {'title': 'Your bookmarks',
             'action': "Every question you've liked or saved, in one place."},
            {'title': 'Your performance',
             'action': "Subject strength, accuracy over time, and miss patterns."},
            {'title': 'Personalised tips',
             'action': "Short, actionable suggestions from your own data.",
             'tip': "Tips get sharper the more quizzes you take."},
        ],
    },
    'history': {
        'title': 'Your History',
        'tagline': 'Every quiz, achievement, and save — in one timeline.',
        'steps': [
            {'title': 'Your plan at a glance',
             'action': "See your tier, retention window, and entry cap.",
             'tip': "Upgrade to keep history longer and search it."},
            {'title': 'Headline stats',
             'action': "Totals for quizzes, achievements, saves, PDFs, and average score."},
            {'title': 'Filter the timeline',
             'action': "Filter by type or date. Search past entries."},
            {'title': 'Scroll the timeline',
             'action': "Recent activity appears first, grouped by day. Load more as needed."},
            {'title': 'Export to CSV',
             'action': "Download your history for offline study.",
             'tip': "CSV export is a Premium feature."},
        ],
    },
    'leaderboard': {
        'title': 'Leaderboard',
        'tagline': 'See how you rank against other students.',
        'steps': [
            {'title': 'Your rank',
             'action': "Your current position, updated as you earn points."},
            {'title': 'The podium',
             'action': "The top three students this week."},
            {'title': 'The full table',
             'action': "Every ranking down to #50. Your row is highlighted.",
             'tip': "Hide yourself from the leaderboard in Settings → Privacy."},
        ],
    },
    'profile': {
        'title': 'Your Profile',
        'tagline': 'Everything the platform knows about you.',
        'steps': [
            {'title': 'Your card',
             'action': "Your avatar, name, and public ID."},
            {'title': 'Your details',
             'action': "Phone, location, school, grade, and total points.",
             'tip': "Contact support to update details that can't be edited yet."},
        ],
    },
    'settings': {
        'title': 'Settings',
        'tagline': 'Customise every aspect of your experience.',
        'steps': [
            {'title': 'Browse the categories',
             'action': "Appearance, Quiz, Notifications, Privacy, and more."},
            {'title': 'Appearance',
             'action': "Choose theme, accent colour, and font size.",
             'tip': "Your choices sync across every page instantly."},
            {'title': 'Quiz and Notifications',
             'action': "Set quiz defaults and choose what you want to be notified about."},
            {'title': 'Privacy and Plan',
             'action': "Control who sees you, and review your tier benefits."},
        ],
    },
    'notifications': {
        'title': 'Notifications',
        'tagline': "Everything that's happened, in one list.",
        'steps': [
            {'title': 'Your unread count',
             'action': "How many notifications you haven't seen yet."},
            {'title': 'Read them',
             'action': "Click any notification to open what it's about."},
            {'title': 'Mark as read',
             'action': "Mark one at a time, or clear the whole list.",
             'tip': "You control which notifications you get in Settings."},
        ],
    },
    'achievements': {
        'title': 'Achievements',
        'tagline': 'Badges you unlock by learning.',
        'steps': [
            {'title': 'Your progress',
             'action': "How many badges you've unlocked out of the total."},
            {'title': 'The grid',
             'action': "Earned badges are coloured. Locked ones show what unlocks them."},
            {'title': 'Your showcase',
             'action': "Pick your best badges to display on your profile.",
             'tip': "Showcasing is a Premium feature."},
        ],
    },
    'upgrade': {
        'title': 'Upgrade Your Plan',
        'tagline': 'Unlock more questions, live hosting, and analytics.',
        'steps': [
            {'title': 'See the prompt',
             'action': "A banner appears at the top of your dashboard when a feature is locked."},
            {'title': 'Learn what\'s included',
             'action': "Compare Free, Premium, and Pro side by side.",
             'tip': "You can upgrade any time — your progress carries over."},
            {'title': 'From Settings',
             'action': "See your current tier in Settings → Plan & Features."},
            {'title': 'Submit a request',
             'action': "Pay via WhatsApp, then an admin activates your account.",
             'tip': "Most upgrades are processed within a few hours."},
        ],
    },
}


# ============================================================
# SOMALI
# ------------------------------------------------------------
# NOTE: These are first-pass translations. A native speaker
# should review before shipping. Keys with identical EN/SO
# text are intentional (proper nouns, feature names).
# ============================================================

SO_UI = {
    "title": "Hagaha Isticmaalaha",
    "badge": "Hage",
    "steps": "tallaabooyin",

    "nav.roadmap": "Khariidadda",
    "nav.tour": "Socdaal Hageed",
    "back_to_dashboard": "Ku noqo Dashboard",
    "login": "Gal",
    "register": "Is diwaan geli",

    "breadcrumb.home": "Hagaha Isticmaalaha",

    "footer.help": "Caawin & Su'aalo",
    "footer.roadmap": "Khariidadda",

    "sidebar.home": "Guudmar",
    "sidebar.toggle": "Beddel dhinaca",
    "tier.locked": "Lama heli karo qorshahaaga",

    "canvas.label": "Horudhac toos ah",
    "related.title": "Astaamaha la xiriira",

    "tour.title": "Socdaalka Hageed",
    "tour.ribbon": "Socdaal Hageed · Tallaabada {n} ee {total}",
    "tour.completed": "Socdaalku dhammaaday",

    "progress.step_of": "Tallaabada {n} ee {total}",
    "progress.complete": "Dhammaad",

    "overview.steps_seen": "Tallaabooyin la arkay",
    "overview.features": "Astaamo",
    "overview.overall": "Guud ahaan",

    "hero.title": "Baro NuunPlatform, tallaabo kasta",
    "hero.subtitle": "Astaamaha oo dhan waxaa lagu muujiyay socdaal hageed — "
                     "leh horudhac muuqaal ah, meelaha la riixo, iyo socdaal "
                     "loogu talagalay isticmaalayaasha cusub.",
    "hero.start_tour": "Bilow Socdaalka Hageed",
    "hero.browse": "Baadh Astaamaha Oo Dhan",

    "home.roadmap_title": "Khariidadda",
    "home.roadmap_sub": "Saddex weji, min bilow ilaa astaamo horumarsan.",
    "home.all_features": "Astaamaha Oo Dhan",
    "home.all_features_sub": "Riix astaam kasta si aad u furto socdaalkeeda tallaabo-tallaabo.",

    "cta.start": "Bilow",
    "cta.resume": "Sii wad",
    "cta.review": "Dib u eeg",
    "cta.prev": "Hore",
    "cta.next": "Xiga",
    "cta.finish": "Dhammee",
    "cta.try_it": "Ka tijaabi barta dhabta ah",
    "cta.zoom_in": "Weynayso",

    "roadmap.title": "Khariidadda",
    "roadmap.subtitle": "Sida barta u soo baxdo — min gelitaanka ugu horreeya ilaa astaamaha horumarsan.",

    "search.title": "Ka Raadi Hagaha",
    "search.subtitle": "Ka hel astaam kasta magaceeda ama erey-fur.",
    "search.placeholder": "Ka raadi astaamaha…",
    "search.submit": "Raadi",
    "search.not_found": "Astaam la mid ah magacaas lama helin",
    "search.no_results": "Waxba lama helin raadintaada",
    "search.try_other": "Isku day erey gaaban, ama baadh liiska buuxa.",
    "search.try_these": "Isku day mid ka mid ah kuwaan:",
    "search.prompt": "Qor erey kore si aad u raadsato hagaha.",
}

SO_PHASES = {
    "phase-1-core": {
        "title": "Bilowga",
        "summary": "Is diwaan geli, gal, oo qaado imtixaankaagii ugu horreeyay. Wax kasta oo isticmaale cusub u baahan yahay.",
    },
    "phase-2-live": {
        "title": "Baro & Tartan",
        "summary": "Imtixaanno toos ah, talooyin diiradda, taariikh, iyo kala-sarreyn.",
    },
    "phase-3-premium": {
        "title": "Akoonkaaga",
        "summary": "Profayl, dejinta, ogeysiisyo, guulo, iyo kor u qaadis.",
    },
}

SO_FEATURES = {
    'register': {
        'title': 'Samee Akoon',
        'tagline': 'Ku biir barta 4 tallaabo oo degdeg ah.',
        'steps': [
            {'title': 'Fur bogga is diwaan gelinta',
             'action': "Bogga gelitaanka, riix 'Samee Akoon' hoosta foomka."},
            {'title': 'Buuxi faahfaahinta akoonkaaga',
             'action': "Geli lambarkaaga taleefanka oo dooro eray sir ah oo adag.",
             'tip': "Taleefankaagu waa inuu ku bilaabmaa +252 oo uu leeyahay 9 lambar."},
            {'title': 'Noo sheeg magacaaga iyo goobtaada',
             'action': "Geli magacaaga koowaad, dhexe, iyo kan dambe — kadibna dooro goobta iyo magaalada."},
            {'title': 'Ku dar dugsigaaga iyo fasalkaaga',
             'action': "Dooro dugsigaaga, fasalkaaga, oo xaqiiji.",
             'tip': "Saddexda magac waa loo baahan yahay. Laba jeer hubi ka hor gudbinta."},
        ],
    },
    'login': {
        'title': 'Gal',
        'tagline': 'Ku noqo dashboard-kaaga ilbiriqsiyo gudahood.',
        'steps': [
            {'title': 'Geli taleefankaaga iyo eraygaaga sirta ah',
             'action': "Lambarkaaga taleefanku waa kii aad isku diwaan gelisay."},
            {'title': 'Riix Gal',
             'action': "Waxaad gaari doontaa dashboard-kaaga.",
             'tip': "Ma illaawday eraygaaga sirta ah? Isticmaal xiriirka WhatsApp ee footer-ka."},
        ],
    },
    'first-look': {
        'title': 'Dashboard-kaaga',
        'tagline': 'Socdaal degdeg ah oo wax kasta oo aad samayn karto.',
        'steps': [
            {'title': 'Qaybtaada sare',
             'action': "Kani waa heerkaaga, horumarka XP, iyo salaanta maalinlaha ah."},
            {'title': 'Tirakoobyadaada',
             'action': "Dhibcaha aqoonta, imtixaannada la qaaday, isku-xigxiga, iyo heerka hadda.",
             'tip': "Isku-xigxigaagu wuu koraa maalin kasta oo aad tababarto — ha jebin!"},
            {'title': 'Shabaqa dhinaca',
             'action': "Tani waa sida aad u dhex marto Imtixaannada, Imtixaanka Tooska, Diiradda, PDF-yada, iyo Kooxaha.",
             'tip': "Waad yareyn kartaa shabaqa bog kasta."},
        ],
    },
    'take-a-quiz': {
        'title': 'Qaado Imtixaan',
        'tagline': 'Ku tababar maado, ka jawaab su\'aalo, arag dhibcahaaga.',
        'steps': [
            {'title': 'Fur bogga diyaarinta imtixaanka',
             'action': "Riix Imtixaannada shabaqa dhinaca si aad u aragto foomka diyaarinta."},
            {'title': 'Dooro maado iyo tirada su\'aalaha',
             'action': "Dooro 10, 20, 30, ama tiro gaar ah — iyadoo ku xiran heerkaaga.",
             'tip': "Heerka Bilaash wuxuu ogolaanayaa 10 su'aalo. Kor u qaad si aad u hesho wax dheeraad ah."},
            {'title': 'Ka jawaab su\'aalaha',
             'action': "Riix ikhtiyaar si aad uga jawaabto. Isla markiiba waxaad arki doontaa jawaab celin."},
            {'title': 'Ka fal-celi su\'aal kasta',
             'action': "Jecel, kaydi, ama soo sheeg su'aalaha aad rabto inaad dib u eegto.",
             'tip': "Su'aalaha la kaydiyay waxay ka soo muuqdaan Diiradda → Calaamadaha."},
            {'title': 'Horay u soco ama bood',
             'action': "Bood su\'aal, ama riix Xiga si aad u sii gudubto. Hore u dhammee haddii loo baahdo.",
             'tip': "Hore u dhamayntu weli way kaydisaa horumarkaaga."},
            {'title': 'Arag natiijooyinkaaga',
             'action': "Dhibcahaaga, boqolkiiba, iyo falcelinta kasta oo aad samaysay."},
        ],
    },
    'pdfs': {
        'title': 'Maktabadda PDF-yada',
        'tagline': 'Baadh, horudhac, akhri, oo soo deji qalabka waxbarasho.',
        'steps': [
            {'title': 'Fur maktabadda',
             'action': "Riix PDF-yada shabaqa dhinaca si aad u aragto dhammaan qalabka waxbarasho."},
            {'title': 'Kala shaandhee oo raadi',
             'action': "Ku raadi erey ama ku shaandhee maado, fasal, iyo manhaj.",
             'tip': "Raadintu waa astaamaha Premium. Isticmaalayaasha bilaash waxay arkaan liiska buuxa."},
            {'title': 'Akhri, soo deji, ama ku fur Telegram',
             'action': "Isticmaal badhamada kaar kasta si aad u furto PDF-ka habkaaga.",
             'tip': "Isticmaalayaasha Pro waxay helaan akhris iyo soo dejisan toos ah browser-ka dhexdiisa."},
        ],
    },
    'groups': {
        'title': 'Kooxaha Waxbarasho',
        'tagline': 'Ku biir bulshooyinka manhajkaaga.',
        'steps': [
            {'title': 'Ku baadh manhajka',
             'action': "Tabs-ka kore waxay u beddelaan Soomaaliya, Puntland, iyo Somaliland."},
            {'title': 'Kala shaandhee oo horudhac',
             'action': "Ku shaandhee madal ama qayb. Riix kaar kasta si aad u aragto faahfaahinta.",
             'tip': "Kooxaha la doortay waa kuwa la taliyay — si firfircoon ayaa loo maamulaa."},
            {'title': 'Ku biir kooxda',
             'action': "Riix Ku biir si aad u furto xeerarka kooxda oo aad u booddo WhatsApp ama Telegram."},
        ],
    },
    'live-quiz-join': {
        'title': 'Ku Biir Imtixaan Toos ah',
        'tagline': 'La tartan ardayda kale waqtiga dhabta ah.',
        'steps': [
            {'title': 'Fur hoolka',
             'action': "Riix Imtixaanka Tooska shabaqa dhinaca si aad u aragto ciyaaraha furan."},
            {'title': 'Hel imtixaan',
             'action': "Isticmaal raadinta iyo shaandhaynta si aad u yareyso cinwaanka, xaaladda, ama maadada.",
             'tip': "Kaliya imtixaannada calaamadeysan 'Furan' ayaa aqbala ka qaybgalayaal cusub."},
            {'title': 'Ku biir koodh',
             'action': "Haddii saaxiib koodh kuula wadaagay, halkan geli."},
            {'title': 'Sug hoolka',
             'action': "Waxaad arki doontaa qof kasta oo ku biiray. Wadaag koodhka si aad u martiqdo wax dheeraad ah.",
             'tip': "Martigeliyuhu ma bilaabi karo ilaa ugu yaraan 2 qof diyaar yihiin."},
            {'title': 'Calaamadi naftaada diyaar',
             'action': "Taabo sumadda gacanta si aad u muujiso inaad diyaar tahay. Ka bax waqti kasta ka hor bilowga."},
            {'title': 'Ciyaar waqtiga dhabta ah',
             'action': "Ka jawaab su'aal kasta ka hor inta aanay waqtigu dhammaanin."},
            {'title': 'Eeg liiska guusha',
             'action': "Dhibcaha si toos ah ayay u cusboonaysiiyaan marka qof kastaa ka jawaabo.",
             'tip': "Kala-sarreynta ugu dambaysa waxay soo baxdaa marka qof kastaa dhammeeyo."},
        ],
    },
    'live-quiz-host': {
        'title': 'Martigeli Imtixaan Toos ah',
        'tagline': 'Abuur, martiqo, oo maamul imtixaan waqtiga dhabta ah.',
        'steps': [
            {'title': 'Abuur imtixaan',
             'action': "Hoolka, riix Abuur Imtixaan.",
             'tip': "Martigelinta waa astaamaha Premium."},
            {'title': 'Deji',
             'action': "Dooro maado, tirada su'aalaha, cinwaan, iyo asturnaan."},
            {'title': 'Wadaag koodhka',
             'action': "Imtixaankaagu wuxuu helayaa koodh gaar ah — u dir WhatsApp ama Telegram.",
             'tip': "Kaliya imtixaannada dadweynaha ayaa ka soo muuqda hoolka."},
            {'title': 'Bilow marka diyaar',
             'action': "Waxaad u baahan tahay ugu yaraan 2 ka qaybgalayaal. Si gacanta ah ama waqti ayaad ku bilaabi kartaa."},
            {'title': 'Eeg qolka',
             'action': "Arag horumarka tooska ah, waqtiga, iyo kala-sarreynta marka qof kastaa ciyaarayo."},
        ],
    },
    'focus': {
        'title': 'Diiradda',
        'tagline': 'Ogow waxa xigga ee aad barato.',
        'steps': [
            {'title': 'Qaybtaada diiradda',
             'action': "Hal eegid: ilo, calaamado, iyo heerka falanqaynta."},
            {'title': 'Ilo la soo jeediyay',
             'action': "PDF-yada dib loo akhriyo oo ku saleysan su'aalaha aad seegtay.",
             'tip': "Riix il si aad toos ugu booddo boggaas maktabadda dhexdeeda."},
            {'title': 'Calaamadahaaga',
             'action': "Su'aal kasta oo aad jecelay ama kaydisay, hal meel."},
            {'title': 'Waxqabadkaaga',
             'action': "Xoogga maadada, saxnaanta waqtiga, iyo qaababka seegitaanka."},
            {'title': 'Talooyin gaar ah',
             'action': "Talooyin gaaban oo wax-qabad leh oo ka soo baxa xogtaada.",
             'tip': "Talooyinku way sii fiiqaan marka aad imtixaanno badan qaadato."},
        ],
    },
    'history': {
        'title': 'Taariikhdaada',
        'tagline': "Imtixaan kasta, guul kasta, iyo kaydin kasta — hal jadwal.",
        'steps': [
            {'title': 'Qorshahaaga hal eegid',
             'action': "Arag heerkaaga, muddada haynta, iyo xadka gelitaanka.",
             'tip': "Kor u qaad si aad taariikhda ugu hayso waqti dheer oo aad u raadsato."},
            {'title': 'Tirakoobyada muhiimka ah',
             'action': "Wadarta imtixaannada, guulaha, kaydinta, PDF-yada, iyo celceliska dhibcaha."},
            {'title': 'Kala shaandhee jadwalka',
             'action': "Ku shaandhee nooc ama taariikh. Raadi gelitaanno hore."},
            {'title': 'Soo dhaadhac jadwalka',
             'action': "Waxqabadka ugu dambeeyay ayaa marka hore soo baxa, oo maalin kasta loo qaybiyay. Soo deji wax dheeraad ah sida loo baahdo."},
            {'title': 'U dhoofi CSV',
             'action': "Soo deji taariikhdaada si aad offline ugu barato.",
             'tip': "Dhoofinta CSV waa astaamaha Premium."},
        ],
    },
    'leaderboard': {
        'title': 'Liiska Guusha',
        'tagline': 'Arag sida aad uga hadasho ardayda kale.',
        'steps': [
            {'title': 'Kaalintaada',
             'action': "Booskaaga hadda, oo la cusboonaysiiyay marka aad dhibco kasbato."},
            {'title': 'Podium-ka',
             'action': "Saddexda arday ee ugu sarreeya toddobaadkan."},
            {'title': 'Shaxda buuxda',
             'action': "Kala-sarreyn kasta ilaa #50. Safkaaga waa la iftiimiyay.",
             'tip': "Ka qari naftaada liiska guusha Dejinta → Asturnaanta."},
        ],
    },
    'profile': {
        'title': 'Profaylkaaga',
        'tagline': 'Wax kasta oo barta ka ogtahay adiga.',
        'steps': [
            {'title': 'Kaarkaaga',
             'action': "Astaantaada, magacaaga, iyo aqoonsigaaga dadweynaha."},
            {'title': 'Faahfaahintaada',
             'action': "Taleefan, goob, dugsi, fasal, iyo wadarta dhibcaha.",
             'tip': "La xiriir taageerada si aad u cusboonaysiiso faahfaahinta aan hadda la beddeli karin."},
        ],
    },
    'settings': {
        'title': 'Dejinta',
        'tagline': 'Habee dhinac kasta oo khibraddaada ah.',
        'steps': [
            {'title': 'Baadh qaybaha',
             'action': "Muuqaalka, Imtixaanka, Ogeysiisyada, Asturnaanta, iyo wax dheeraad ah."},
            {'title': 'Muuqaalka',
             'action': "Dooro mawduuc, midab muhiim ah, iyo cabbirka qoraalka.",
             'tip': "Doorashadaadu waxay isla markiiba ku isku xirtaa bog kasta."},
            {'title': 'Imtixaanka iyo Ogeysiisyada',
             'action': "Deji caadooyinka imtixaanka oo dooro waxa aad rabto in lagu ogeysiiyo."},
            {'title': 'Asturnaanta iyo Qorshaha',
             'action': "Xakamee cidda ku arkeysa, oo dib u eeg faa'iidooyinka heerkaaga."},
        ],
    },
    'notifications': {
        'title': 'Ogeysiisyada',
        'tagline': "Wax kasta oo dhacay, hal liis.",
        'steps': [
            {'title': 'Tirakoobka aan la akhriyin',
             'action': "Immisa ogeysiis ayaadan weli arag."},
            {'title': 'Akhri',
             'action': "Riix ogeysiis kasta si aad u furto waxa uu ku saabsan yahay."},
            {'title': 'Calaamadi la akhriyay',
             'action': "Hal-hal u calaamadi, ama nadiifi liiska oo dhan.",
             'tip': "Waxaad xakameynaysaa ogeysiisyada aad hesho Dejinta dhexdeeda."},
        ],
    },
    'achievements': {
        'title': 'Guulaha',
        'tagline': 'Calaamado aad furto iyadoo aad baranaysid.',
        'steps': [
            {'title': 'Horumarkaaga',
             'action': "Immisa calaamad ayaad furtay wadarta guud."},
            {'title': 'Shabakadda',
             'action': "Calaamadaha la kasbaday waa midab. Kuwa xidhan waxay muujinayaan waxa ay furaan."},
            {'title': 'Bandhiggaaga',
             'action': "Dooro calaamadahaaga ugu fiican si aad ugu muujiso profaylkaaga.",
             'tip': "Bandhigu waa astaamaha Premium."},
        ],
    },
    'upgrade': {
        'title': 'Kor u Qaad Qorshahaaga',
        'tagline': "Fur su'aalo badan, martigelin toos ah, iyo falanqayn.",
        'steps': [
            {'title': 'Arag tilmaanta',
             'action': "Banner ayaa ka soo muuqda korka dashboard-kaaga marka astaamuhu xidhan yahay."},
            {'title': 'Baro waxa ku jira',
             'action': "Isbarbardhig Bilaash, Premium, iyo Pro dhinac-dhinac.",
             'tip': "Waqti kasta waad kor u qaadi kartaa — horumarkaagu wuu sii socdaa."},
            {'title': 'Ka soo Dejinta',
             'action': "Arag heerkaaga hadda Dejinta → Qorshaha & Astaamaha."},
            {'title': 'Gudbi codsi',
             'action': "Bixi WhatsApp, kadibna maamule ayaa hawlgeliya akoonkaaga.",
             'tip': "Inta badan kor u qaadista waxaa lagu socodsiiyaa dhowr saacadood gudahood."},
        ],
    },
}


# ============================================================
# FLATTENERS
# ============================================================

def _flatten_ui(prefix: str, mapping: dict) -> dict:
    return {f"{prefix}.{k}": v for k, v in mapping.items()}


def _flatten_phases(phases: dict) -> dict:
    out = {}
    for pkey, pdata in phases.items():
        out[f"docs.roadmap.{pkey}.title"] = pdata['title']
        out[f"docs.roadmap.{pkey}.summary"] = pdata['summary']
    return out


def _flatten_features(features: dict) -> dict:
    out = {}
    for fkey, fdata in features.items():
        out[f"docs.{fkey}.title"] = fdata['title']
        out[f"docs.{fkey}.tagline"] = fdata['tagline']
        for i, step in enumerate(fdata['steps'], start=1):
            out[f"docs.{fkey}.step{i}.title"] = step['title']
            out[f"docs.{fkey}.step{i}.action"] = step['action']
            if 'tip' in step and step['tip']:
                out[f"docs.{fkey}.step{i}.tip"] = step['tip']
    return out


def build_catalog(ui: dict, phases: dict, features: dict) -> dict:
    merged = {}
    merged.update(_flatten_ui('docs', ui))
    merged.update(_flatten_phases(phases))
    merged.update(_flatten_features(features))
    return merged


# ============================================================
# MERGE
# ============================================================

def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open('r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"ERROR: could not read {path}: {e}", file=sys.stderr)
        sys.exit(1)


def _write_json(path: Path, data: dict, dry_run: bool) -> None:
    if dry_run:
        return
    # Backup original (only once per run)
    backup = path.with_suffix(path.suffix + '.bak')
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    # Preserve key order roughly: sort by key for readability
    with path.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write('\n')


def merge_lang(lang: str, new_docs: dict, dry_run: bool) -> tuple[int, int, int]:
    path = TRANS_DIR / f'{lang}.json'
    existing = _read_json(path)

    added = 0
    updated = 0
    unchanged = 0
    for k, v in new_docs.items():
        if k not in existing:
            existing[k] = v
            added += 1
        elif existing[k] != v:
            existing[k] = v
            updated += 1
        else:
            unchanged += 1

    _write_json(path, existing, dry_run)
    return added, updated, unchanged


# ============================================================
# MAIN
# ============================================================

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dry-run', action='store_true',
                    help='Show what would change without writing files.')
    ap.add_argument('--lang', choices=('en', 'so', 'all'), default='all',
                    help='Which catalog to update (default: all).')
    args = ap.parse_args()

    en_docs = build_catalog(EN_UI, EN_PHASES, EN_FEATURES)
    so_docs = build_catalog(SO_UI, SO_PHASES, SO_FEATURES)

    print(f"docs.* keys prepared: EN={len(en_docs)}, SO={len(so_docs)}")

    targets = []
    if args.lang in ('en', 'all'):
        targets.append(('en', en_docs))
    if args.lang in ('so', 'all'):
        targets.append(('so', so_docs))

    for lang, catalog in targets:
        path = TRANS_DIR / f'{lang}.json'
        if not path.exists():
            print(f"WARNING: {path} does not exist — skipping.")
            continue
        a, u, n = merge_lang(lang, catalog, args.dry_run)
        verb = 'would be' if args.dry_run else ''
        print(f"  {lang}.json: {a} added, {u} updated, {n} unchanged {verb}")

    print("Done." if not args.dry_run else "Dry run — no files written.")


if __name__ == '__main__':
    main()