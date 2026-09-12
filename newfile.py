#!/usr/bin/env python3
# seed_test_questions.py
# ---------------------------------------------------------------
# One-time test data seeder.
#
# Inserts 30 questions per subject for:
#   arabic · mathematics · geography · chemistry · af_somali
#
# Every inserted row is tagged with `test_seed` so it can be
# cleanly removed later (`--clean`).
#
# Usage:
#   python seed_test_questions.py            # seed
#   python seed_test_questions.py --dry-run  # preview
#   python seed_test_questions.py --clean    # delete all test rows
#   python seed_test_questions.py --clean --dry-run
# ---------------------------------------------------------------

import os
import sys
import argparse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config import Config
from db import execute_with_retry, to_json, now


TEST_TAG = 'test_seed'

# Somali subject code — change to 'somali' if your curriculum uses that key.
SOMALI_CODE = 'af_somali'


# ============================================================
# QUESTION DATA
# Format per entry:
#   (question, options_dict, correct, difficulty, chapter, tags, explanation)
# difficulty: 1 = very easy, 5 = very hard
# ============================================================

ARABIC_QUESTIONS = [
    # ---------- EASY (1-2) ----------
    ("How many letters are there in the Arabic alphabet?",
     {"A": "24", "B": "26", "C": "28"}, "C", 1, "Basics", "arabic,alphabet",
     "The Arabic alphabet (abjad) has 28 letters, all representing consonants."),

    ("In which direction is Arabic written and read?",
     {"A": "Left to right", "B": "Right to left", "C": "Top to bottom"}, "B", 1, "Basics", "arabic,writing",
     "Arabic is written and read from right to left, top to bottom."),

    ("What does 'marhaba' (مرحبا) mean in English?",
     {"A": "Goodbye", "B": "Welcome/Hello", "C": "Sorry"}, "B", 1, "Vocabulary", "arabic,greetings",
     "'Marhaba' is the standard Arabic greeting meaning 'welcome' or 'hello'."),

    ("What does 'shukran' (شكرا) mean?",
     {"A": "Please", "B": "Thank you", "C": "Excuse me"}, "B", 1, "Vocabulary", "arabic,greetings",
     "'Shukran' means 'thank you'. The reply is 'afwan' (you're welcome)."),

    ("The word 'kitab' (كتاب) means:",
     {"A": "Pen", "B": "Book", "C": "School"}, "B", 1, "Vocabulary", "arabic,vocabulary",
     "'Kitab' means 'book'. The plural is 'kutub'."),

    ("What is the Arabic definite article (equivalent to 'the')?",
     {"A": "Al- (ال)", "B": "Wa- (و)", "C": "Fi- (في)"}, "A", 2, "Grammar", "arabic,grammar,article",
     "'Al-' is the definite article. It attaches to the beginning of the noun: 'al-kitab' = 'the book'."),

    ("Which of these is the Arabic word for 'peace'?",
     {"A": "Harb (حرب)", "B": "Salam (سلام)", "C": "Nar (نار)"}, "B", 1, "Vocabulary", "arabic,vocabulary",
     "'Salam' (سلام) means 'peace'. 'Harb' means war; 'nar' means fire."),

    ("What does 'ahlan wa sahlan' (أهلا وسهلا) mean?",
     {"A": "Good night", "B": "Welcome", "C": "See you later"}, "B", 2, "Vocabulary", "arabic,greetings",
     "'Ahlan wa sahlan' is a warm greeting meaning 'welcome'."),

    # ---------- MEDIUM (3) ----------
    ("What is the plural of 'kalima' (كلمة, 'word')?",
     {"A": "Kalimat (كلمات)", "B": "Kalam (كلام)", "C": "Kulm (كلم)"}, "A", 3, "Grammar", "arabic,plurals",
     "'Kalima' becomes 'kalimat' in the sound feminine plural. 'Kalam' means 'speech/talk'."),

    ("How many root letters do most Arabic words have?",
     {"A": "Two", "B": "Three", "C": "Five"}, "B", 3, "Morphology", "arabic,roots",
     "Most Arabic words are built on a three-letter root (ثلاثي), e.g. ك-ت-ب (k-t-b) → kataba, kitab, maktab."),

    ("The root ك-ت-ب (k-t-b) is related to which concept?",
     {"A": "Writing", "B": "Running", "C": "Eating"}, "A", 3, "Morphology", "arabic,roots",
     "The root k-t-b relates to writing: kataba (he wrote), kitab (book), maktab (office/desk), maktaba (library)."),

    ("What does 'madrasa' (مدرسة) mean?",
     {"A": "Teacher", "B": "School", "C": "Lesson"}, "B", 3, "Vocabulary", "arabic,vocabulary",
     "'Madrasa' means 'school'. It shares the d-r-s root with 'dars' (lesson) and 'mudarris' (teacher)."),

    ("Which word means 'the' before a noun starting with a 'sun letter' (like ش sh)?",
     {"A": "Al- (pronounced clearly)", "B": "Al- (assimilated into the sun letter)", "C": "There is no article"}, "B", 3, "Phonology", "arabic,phonology,sun-moon",
     "With sun letters (الحروف الشمسية) the 'l' of 'al-' assimilates: 'ash-shams' not 'al-shams'."),

    ("In Arabic, what is 'idafa' (إضافة)?",
     {"A": "A verb tense", "B": "A possessive construct", "C": "A negation particle"}, "B", 3, "Grammar", "arabic,grammar,idafa",
     "'Idafa' expresses possession: 'kitab al-mudarris' = 'the teacher's book'. The first noun loses 'al-'."),

    ("Which Arabic word means 'heart'?",
     {"A": "Qalb (قلب)", "B": "Ayn (عين)", "C": "Yad (يد)"}, "A", 3, "Vocabulary", "arabic,vocabulary,body",
     "'Qalb' = heart. 'Ayn' = eye/spring; 'yad' = hand."),

    ("In Arabic grammar, 'raf'' (رفع) refers to which case?",
     {"A": "Accusative", "B": "Genitive", "C": "Nominative"}, "C", 3, "Grammar", "arabic,grammar,cases",
     "Raf' = nominative (subject of a sentence). Nasb = accusative; jarr = genitive."),

    ("What does the phrase 'insha'allah' (إن شاء الله) literally mean?",
     {"A": "Thanks be to God", "B": "If God wills", "C": "God is great"}, "B", 3, "Vocabulary", "arabic,phrases",
     "'Insha'allah' means 'if God wills'. Said about future events."),

    ("Which Arabic pronoun means 'we'?",
     {"A": "Ana (أنا)", "B": "Nahnu (نحن)", "C": "Hum (هم)"}, "B", 3, "Grammar", "arabic,pronouns",
     "'Nahnu' = we; 'ana' = I; 'hum' = they (masc.)."),

    ("What does 'sabr' (صبر) mean?",
     {"A": "Patience", "B": "Anger", "C": "Fear"}, "A", 3, "Vocabulary", "arabic,vocabulary,values",
     "'Sabr' means patience, endurance, and perseverance — a core value in Arabic culture."),

    # ---------- HARD (4-5) ----------
    ("Which verb form (bâb) of the Arabic verb is typically intensive/frequentative?",
     {"A": "Form I (فَعَلَ)", "B": "Form II (فَعَّلَ)", "C": "Form X (اِسْتَفْعَلَ)"}, "B", 4, "Morphology", "arabic,verbs,forms",
     "Form II (fa''ala) with doubled middle letter is intensive/frequentative, e.g. 'kassara' = he smashed repeatedly."),

    ("In Arabic rhetoric (balagha), what is 'isti'ara' (استعارة)?",
     {"A": "A metaphor", "B": "A rhyme", "C": "An abbreviation"}, "A", 4, "Rhetoric", "arabic,rhetoric",
     "'Isti'ara' is Arabic metaphor — using a word in a figurative sense, often more compressed than English metaphor."),

    ("What case does the object of a verb take in Arabic?",
     {"A": "Raf' (nominative)", "B": "Nasb (accusative)", "C": "Jarr (genitive)"}, "B", 4, "Grammar", "arabic,grammar,cases",
     "The direct object (maf'ul bihi) takes the accusative (nasb), usually shown by a fatha."),

    ("Which Arabic measure is used to form nouns of place (like 'maktab' = office)?",
     {"A": "مَفْعَل (maf'al)", "B": "فَعَّال (fa''al)", "C": "فَاعِل (fa'il)"}, "A", 4, "Morphology", "arabic,patterns",
     "'Maf'al' is the noun-of-place pattern: maktab (office), masjid (mosque), madrasa (school)."),

    ("In classical Arabic, 'hadha' (هذا) vs 'dhalika' (ذلك) differ in:",
     {"A": "Gender only", "B": "Near vs far distance", "C": "Case only"}, "B", 4, "Grammar", "arabic,demonstratives",
     "'Hadha' = this (near); 'dhalika' = that (far). Both are masculine singular."),

    ("What is 'tashdid' (تشديد) in Arabic writing?",
     {"A": "A vowel mark", "B": "A doubling mark on a consonant", "C": "An end-of-sentence mark"}, "B", 4, "Phonology", "arabic,phonology,diacritics",
     "'Tashdid' (ّ) indicates the consonant is doubled and lengthened (e.g. 'kassara' كَسَّرَ)."),

    ("Which of these is a broken plural (jam' taksir) form?",
     {"A": "Muslimun (مسلمون)", "B": "Rijal (رجال)", "C": "Mu'minat (مؤمنات)"}, "B", 4, "Grammar", "arabic,plurals",
     "'Rijal' (رجال, men) is a broken plural — the root's internal vowel pattern changes, unlike sound plurals."),

    ("In Arabic, what is the difference between 'kataba' (كَتَبَ) and 'yaktubu' (يَكْتُبُ)?",
     {"A": "Past and present tense", "B": "Singular and plural", "C": "Active and passive"}, "A", 4, "Grammar", "arabic,tenses",
     "'Kataba' is perfect (past) tense; 'yaktubu' is imperfect (present/future) tense."),

    ("Which of the following is NOT one of the Arabic 'huruf al-jarr' (prepositions)?",
     {"A": "Fi (في)", "B": "Min (من)", "C": "Qad (قد)"}, "C", 4, "Grammar", "arabic,prepositions",
     "'Fi' and 'min' are prepositions. 'Qad' is a verbal particle indicating emphasis or the perfect tense."),

    ("In Arabic, 'al-hamdu lillah' (الحمد لله) is best translated as:",
     {"A": "God is the greatest", "B": "All praise is due to God", "C": "In the name of God"}, "B", 4, "Vocabulary", "arabic,phrases",
     "'Al-hamdu lillah' = 'all praise is due to God'. Commonly said in gratitude."),
]


MATHEMATICS_QUESTIONS = [
    # ---------- EASY (1-2) ----------
    ("What is 7 + 8?",
     {"A": "13", "B": "15", "C": "17"}, "B", 1, "Arithmetic", "math,addition",
     "7 + 8 = 15."),

    ("What is the square root of 144?",
     {"A": "11", "B": "12", "C": "14"}, "B", 1, "Arithmetic", "math,roots",
     "12 × 12 = 144, so √144 = 12."),

    ("Which of these is a prime number?",
     {"A": "9", "B": "15", "C": "17"}, "C", 2, "Number Theory", "math,primes",
     "17 is only divisible by 1 and itself. 9 = 3×3; 15 = 3×5."),

    ("What is the value of π (pi) rounded to two decimal places?",
     {"A": "3.14", "B": "3.41", "C": "2.71"}, "A", 1, "Geometry", "math,pi,constants",
     "π ≈ 3.14159..., commonly rounded to 3.14. It is the ratio of a circle's circumference to its diameter."),

    ("What is 5 factorial (5!)?",
     {"A": "25", "B": "100", "C": "120"}, "C", 2, "Combinatorics", "math,factorial",
     "5! = 5 × 4 × 3 × 2 × 1 = 120."),

    ("How many sides does a hexagon have?",
     {"A": "5", "B": "6", "C": "8"}, "B", 1, "Geometry", "math,shapes",
     "A hexagon (from Greek 'hex' = six) has 6 sides."),

    ("What is 25% of 80?",
     {"A": "15", "B": "20", "C": "25"}, "B", 1, "Percentages", "math,percentages",
     "25% = ¼, so 80 ÷ 4 = 20."),

    ("Which number is the smallest?",
     {"A": "0.3", "B": "0.03", "C": "0.003"}, "C", 2, "Number Sense", "math,decimals",
     "0.003 < 0.03 < 0.3. The more zeros after the decimal point before the first non-zero, the smaller the value."),

    # ---------- MEDIUM (3) ----------
    ("What is the area of a triangle with base 10 and height 6?",
     {"A": "30", "B": "60", "C": "16"}, "A", 3, "Geometry", "math,area",
     "Area = ½ × base × height = ½ × 10 × 6 = 30."),

    ("Solve for x: 2x + 5 = 15",
     {"A": "x = 5", "B": "x = 10", "C": "x = 7.5"}, "A", 3, "Algebra", "math,equations",
     "2x + 5 = 15 → 2x = 10 → x = 5."),

    ("What is the sum of the interior angles of a triangle?",
     {"A": "90°", "B": "180°", "C": "360°"}, "B", 3, "Geometry", "math,geometry,angles",
     "The interior angles of any triangle always sum to 180°."),

    ("What is the greatest common divisor (GCD) of 24 and 36?",
     {"A": "6", "B": "12", "C": "18"}, "B", 3, "Number Theory", "math,gcd",
     "24 = 2³ × 3, 36 = 2² × 3². GCD = 2² × 3 = 12."),

    ("If a car travels 240 km in 3 hours, what is its average speed?",
     {"A": "60 km/h", "B": "80 km/h", "C": "90 km/h"}, "B", 3, "Applied Math", "math,speed",
     "Speed = distance ÷ time = 240 ÷ 3 = 80 km/h."),

    ("What is 2⁵ (2 to the power of 5)?",
     {"A": "10", "B": "25", "C": "32"}, "C", 3, "Exponents", "math,exponents",
     "2⁵ = 2 × 2 × 2 × 2 × 2 = 32."),

    ("The perimeter of a square with side length 7 is:",
     {"A": "14", "B": "21", "C": "28"}, "C", 3, "Geometry", "math,perimeter",
     "Perimeter = 4 × side = 4 × 7 = 28."),

    ("What is 15% of 200?",
     {"A": "15", "B": "30", "C": "45"}, "B", 3, "Percentages", "math,percentages",
     "15% of 200 = 0.15 × 200 = 30."),

    ("Which of these is an irrational number?",
     {"A": "0.5", "B": "√2", "C": "3/4"}, "B", 3, "Number Theory", "math,irrational",
     "√2 ≈ 1.41421356... cannot be expressed as a fraction of two integers."),

    ("What is the median of the set {3, 7, 9, 15, 21}?",
     {"A": "9", "B": "11", "C": "15"}, "A", 3, "Statistics", "math,statistics",
     "The median is the middle value when sorted: 3, 7, [9], 15, 21 → 9."),

    ("If a rectangle has area 48 and width 6, its length is:",
     {"A": "6", "B": "8", "C": "10"}, "B", 3, "Geometry", "math,area",
     "Area = length × width → 48 = length × 6 → length = 8."),

    ("What is the value of 3⁰ (any non-zero number to the power of zero)?",
     {"A": "0", "B": "1", "C": "3"}, "B", 3, "Exponents", "math,exponents",
     "Any non-zero number raised to the power 0 equals 1."),

    # ---------- HARD (4-5) ----------
    ("What is the quadratic formula for ax² + bx + c = 0?",
     {"A": "x = (-b ± √(b² - 4ac)) / 2a", "B": "x = (b ± √(b² + 4ac)) / 2a", "C": "x = (-b ± √(b² + ac)) / a"}, "A", 4, "Algebra", "math,quadratic",
     "The quadratic formula is x = (-b ± √(b² - 4ac)) / 2a. The discriminant b²-4ac tells us about the roots."),

    ("What is the derivative of sin(x) with respect to x?",
     {"A": "cos(x)", "B": "-cos(x)", "C": "-sin(x)"}, "A", 4, "Calculus", "math,calculus,derivatives",
     "d/dx [sin(x)] = cos(x). Related: d/dx [cos(x)] = -sin(x)."),

    ("What is the value of i², where i is the imaginary unit?",
     {"A": "1", "B": "-1", "C": "0"}, "B", 4, "Complex Numbers", "math,complex",
     "By definition i = √(-1), so i² = -1."),

    ("What is the sum of interior angles of a hexagon (6-sided polygon)?",
     {"A": "540°", "B": "720°", "C": "900°"}, "B", 4, "Geometry", "math,geometry,angles",
     "Sum of interior angles = (n-2) × 180° = (6-2) × 180° = 720°."),

    ("If log₁₀(x) = 2, what is x?",
     {"A": "20", "B": "100", "C": "200"}, "B", 4, "Logarithms", "math,logarithms",
     "log₁₀(x) = 2 means 10² = x, so x = 100."),

    ("What is the probability of getting exactly two heads in three fair coin tosses?",
     {"A": "1/8", "B": "3/8", "C": "1/2"}, "B", 4, "Probability", "math,probability",
     "There are 3 favorable outcomes (HHT, HTH, THH) out of 8 total, giving 3/8."),

    ("The value of the infinite geometric series 1 + ½ + ¼ + ⅛ + ... is:",
     {"A": "1.5", "B": "2", "C": "∞"}, "B", 4, "Series", "math,series",
     "Sum = a/(1-r) = 1/(1-½) = 2."),

    ("What is the determinant of the 2×2 matrix [[a, b], [c, d]]?",
     {"A": "ab - cd", "B": "ad - bc", "C": "ac - bd"}, "B", 5, "Linear Algebra", "math,matrices",
     "det = ad - bc. This value tells us whether the matrix is invertible."),

    ("How many ways can you arrange the letters of the word 'LEVEL'?",
     {"A": "30", "B": "60", "C": "120"}, "A", 5, "Combinatorics", "math,permutations",
     "'LEVEL' has 5 letters with L and E each appearing twice. 5!/(2!2!) = 120/4 = 30."),

    ("What is the slope of the line perpendicular to y = 2x + 3?",
     {"A": "-2", "B": "-1/2", "C": "1/2"}, "B", 5, "Algebra", "math,slope,lines",
     "The original slope is 2. Perpendicular slope is the negative reciprocal: -1/2."),
]


GEOGRAPHY_QUESTIONS = [
    # ---------- EASY (1-2) ----------
    ("What is the largest continent by area?",
     {"A": "Africa", "B": "Asia", "C": "North America"}, "B", 1, "Continents", "geo,continents",
     "Asia is the largest continent, covering about 44.6 million km²."),

    ("What is the longest river in the world (by conventional measurement)?",
     {"A": "Amazon", "B": "Nile", "C": "Yangtze"}, "B", 2, "Rivers", "geo,rivers",
     "The Nile (about 6,650 km) is traditionally considered the world's longest river."),

    ("What is the capital city of Somalia?",
     {"A": "Hargeisa", "B": "Mogadishu", "C": "Kismayo"}, "B", 1, "Cities", "geo,africa,somalia",
     "Mogadishu (Muqdisho) is the capital of Somalia."),

    ("How many continents are there on Earth?",
     {"A": "5", "B": "6", "C": "7"}, "C", 1, "Continents", "geo,continents",
     "Seven continents: Africa, Antarctica, Asia, Australia, Europe, North America, South America."),

    ("What is the largest ocean on Earth?",
     {"A": "Atlantic", "B": "Indian", "C": "Pacific"}, "C", 1, "Oceans", "geo,oceans",
     "The Pacific Ocean covers about one-third of the Earth's surface."),

    ("In which continent is the Sahara Desert?",
     {"A": "Asia", "B": "Africa", "C": "Australia"}, "B", 1, "Deserts", "geo,deserts",
     "The Sahara is in North Africa and is the largest hot desert in the world."),

    ("What is the highest mountain in the world?",
     {"A": "K2", "B": "Mount Everest", "C": "Kilimanjaro"}, "B", 1, "Mountains", "geo,mountains",
     "Mount Everest stands at about 8,849 m in the Himalayas."),

    ("Which line of latitude is at 0°?",
     {"A": "Equator", "B": "Tropic of Cancer", "C": "Prime Meridian"}, "A", 2, "Coordinates", "geo,latitude",
     "The Equator is at 0° latitude and divides the Earth into Northern and Southern Hemispheres."),

    # ---------- MEDIUM (3) ----------
    ("Which country has the largest population in the world (2024)?",
     {"A": "China", "B": "India", "C": "USA"}, "B", 3, "Demographics", "geo,population",
     "India surpassed China in 2023 as the world's most populous country (over 1.4 billion)."),

    ("Which strait separates Africa from Europe?",
     {"A": "Strait of Hormuz", "B": "Strait of Gibraltar", "C": "Bosphorus"}, "B", 3, "Straits", "geo,straits",
     "The Strait of Gibraltar separates Spain (Europe) from Morocco (Africa)."),

    ("The Tropic of Capricorn is located at approximately:",
     {"A": "23.5° N", "B": "23.5° S", "C": "66.5° S"}, "B", 3, "Coordinates", "geo,latitude",
     "Tropic of Capricorn ≈ 23.5° S; Tropic of Cancer ≈ 23.5° N."),

    ("What is the capital city of Australia?",
     {"A": "Sydney", "B": "Melbourne", "C": "Canberra"}, "C", 3, "Cities", "geo,capitals",
     "Canberra is the capital of Australia, chosen as a compromise between Sydney and Melbourne."),

    ("Which country has the most natural lakes?",
     {"A": "Russia", "B": "Canada", "C": "Finland"}, "B", 3, "Lakes", "geo,lakes",
     "Canada contains more lakes than any other country — over 60% of the world's lakes."),

    ("The Amazon River flows primarily through which country?",
     {"A": "Peru", "B": "Brazil", "C": "Colombia"}, "B", 3, "Rivers", "geo,rivers",
     "About 60% of the Amazon basin is in Brazil; the river empties into the Atlantic."),

    ("What is a fjord?",
     {"A": "A narrow sea inlet between steep cliffs", "B": "A desert valley", "C": "A volcanic crater lake"}, "A", 3, "Landforms", "geo,landforms",
     "Fjords are narrow, deep inlets formed by glacial erosion — common in Norway."),

    ("The Prime Meridian passes through which city?",
     {"A": "Paris", "B": "Greenwich, London", "C": "New York"}, "B", 3, "Coordinates", "geo,longitude",
     "The Prime Meridian (0° longitude) passes through Greenwich, London."),

    ("Which is the smallest independent country in the world?",
     {"A": "Monaco", "B": "Vatican City", "C": "San Marino"}, "B", 3, "Countries", "geo,countries",
     "Vatican City is the smallest sovereign state at about 0.49 km²."),

    ("Mount Kilimanjaro is located in which country?",
     {"A": "Kenya", "B": "Tanzania", "C": "Uganda"}, "B", 3, "Mountains", "geo,africa",
     "Kilimanjaro is in Tanzania — the highest mountain in Africa at 5,895 m."),

    ("The Great Barrier Reef is off the coast of which country?",
     {"A": "Indonesia", "B": "Australia", "C": "Philippines"}, "B", 3, "Reefs", "geo,oceania",
     "The Great Barrier Reef is off Queensland, Australia — the world's largest coral reef system."),

    ("Which African country is entirely surrounded by South Africa?",
     {"A": "Lesotho", "B": "Eswatini", "C": "Botswana"}, "A", 3, "Countries", "geo,africa",
     "Lesotho is landlocked within South Africa. Eswatini shares borders with SA and Mozambique."),

    # ---------- HARD (4-5) ----------
    ("Which is the longest river in Africa?",
     {"A": "Congo", "B": "Nile", "C": "Niger"}, "B", 4, "Rivers", "geo,africa,rivers",
     "The Nile (about 6,650 km) is the longest river in Africa, flowing northward through 11 countries."),

    ("Which two countries share the longest international border in the world?",
     {"A": "USA – Mexico", "B": "Russia – China", "C": "USA – Canada"}, "C", 4, "Borders", "geo,borders",
     "USA–Canada border is about 8,891 km — the longest between any two countries."),

    ("What is the deepest point in the ocean?",
     {"A": "Puerto Rico Trench", "B": "Mariana Trench", "C": "Java Trench"}, "B", 4, "Oceans", "geo,oceans",
     "The Mariana Trench's Challenger Deep is about 11,034 m deep in the western Pacific."),

    ("The Andean plateau (Altiplano) is shared by which pair of countries?",
     {"A": "Brazil and Argentina", "B": "Peru and Bolivia", "C": "Chile and Ecuador"}, "B", 4, "Plateaus", "geo,americas",
     "The Altiplano spans southeastern Peru and western Bolivia, at around 3,750 m elevation."),

    ("Which country has the most time zones (including territories)?",
     {"A": "Russia", "B": "USA", "C": "France"}, "C", 4, "Time Zones", "geo,timezones",
     "France has 12 time zones when overseas territories are included; Russia has 11, USA has 11."),

    ("Which is the largest desert in the world?",
     {"A": "Sahara", "B": "Antarctic Desert", "C": "Gobi"}, "B", 5, "Deserts", "geo,deserts",
     "Antarctica is the largest desert (14 million km²), classified as a cold desert. Sahara is the largest hot desert."),

    ("What is the Ring of Fire?",
     {"A": "A volcanic belt around the Pacific", "B": "A desert region in Asia", "C": "A chain of lakes in Africa"}, "A", 4, "Tectonics", "geo,tectonics",
     "The Ring of Fire is a horseshoe-shaped zone of volcanoes and earthquakes around the Pacific Ocean."),

    ("The Danube River flows into which sea?",
     {"A": "Adriatic", "B": "Black Sea", "C": "Mediterranean"}, "B", 4, "Rivers", "geo,europe",
     "The Danube flows eastward through 10 countries and empties into the Black Sea."),

    ("Which country's territory includes the Galápagos Islands?",
     {"A": "Peru", "B": "Ecuador", "C": "Colombia"}, "B", 4, "Islands", "geo,americas",
     "The Galápagos Islands are about 1,000 km west of Ecuador, in the Pacific Ocean."),

    ("What is a 'rain shadow'?",
     {"A": "A dry region on the leeward side of a mountain", "B": "A cloud formation above cities", "C": "An area with year-round rain"}, "A", 5, "Climate", "geo,climate",
     "A rain shadow is a dry area where moisture-laden winds have lost their rain after crossing a mountain range."),
]


CHEMISTRY_QUESTIONS = [
    # ---------- EASY (1-2) ----------
    ("What is the chemical formula for water?",
     {"A": "H₂O", "B": "H₂O₂", "C": "HO₂"}, "A", 1, "Basics", "chem,formulas",
     "Water is H₂O — two hydrogen atoms bonded to one oxygen atom."),

    ("What is the chemical symbol for gold?",
     {"A": "Go", "B": "Gd", "C": "Au"}, "C", 1, "Elements", "chem,elements",
     "'Au' comes from the Latin 'aurum', the ancient name for gold."),

    ("What is NaCl commonly known as?",
     {"A": "Baking soda", "B": "Table salt", "C": "Sugar"}, "B", 1, "Compounds", "chem,compounds",
     "NaCl is sodium chloride, commonly known as table salt."),

    ("How many elements are currently on the periodic table?",
     {"A": "92", "B": "108", "C": "118"}, "C", 1, "Periodic Table", "chem,periodic",
     "As of 2024, 118 elements have been confirmed and named."),

    ("What is the lightest element?",
     {"A": "Helium", "B": "Hydrogen", "C": "Lithium"}, "B", 1, "Elements", "chem,elements",
     "Hydrogen (atomic number 1) is the lightest and most abundant element in the universe."),

    ("What is the pH of a neutral solution (like pure water) at 25°C?",
     {"A": "0", "B": "7", "C": "14"}, "B", 2, "Acids & Bases", "chem,ph",
     "Pure water has pH 7 — neutral. Below 7 is acidic; above 7 is basic."),

    ("Which gas do plants absorb for photosynthesis?",
     {"A": "Oxygen", "B": "Nitrogen", "C": "Carbon dioxide"}, "C", 1, "Biochemistry", "chem,photosynthesis",
     "Plants absorb CO₂ and release O₂ during photosynthesis."),

    ("What is the chemical symbol for carbon?",
     {"A": "Ca", "B": "C", "C": "Cr"}, "B", 1, "Elements", "chem,elements",
     "'C' is carbon. 'Ca' is calcium; 'Cr' is chromium."),

    # ---------- MEDIUM (3) ----------
    ("What is the atomic number of carbon?",
     {"A": "4", "B": "6", "C": "12"}, "B", 3, "Atomic Structure", "chem,atoms",
     "Carbon has 6 protons, so its atomic number is 6. Mass number is about 12."),

    ("What is H₂SO₄?",
     {"A": "Hydrochloric acid", "B": "Sulfuric acid", "C": "Nitric acid"}, "B", 3, "Acids", "chem,acids",
     "H₂SO₄ is sulfuric acid — one of the most important industrial chemicals."),

    ("What is the common name for sodium bicarbonate (NaHCO₃)?",
     {"A": "Baking soda", "B": "Baking powder", "C": "Washing soda"}, "A", 3, "Compounds", "chem,compounds",
     "NaHCO₃ is baking soda. Baking powder contains baking soda plus an acid."),

    ("Which subatomic particle has no charge?",
     {"A": "Proton", "B": "Electron", "C": "Neutron"}, "C", 3, "Atomic Structure", "chem,atoms",
     "Neutrons are electrically neutral. Protons are positive; electrons are negative."),

    ("What is the molar mass of water (H₂O) in g/mol?",
     {"A": "16", "B": "18", "C": "20"}, "B", 3, "Stoichiometry", "chem,stoichiometry",
     "H₂O = 2(1) + 16 = 18 g/mol."),

    ("What is the reaction between an acid and a base called?",
     {"A": "Combustion", "B": "Neutralization", "C": "Oxidation"}, "B", 3, "Reactions", "chem,reactions",
     "Acid + base → salt + water. This is neutralization."),

    ("Which element has the chemical symbol 'Fe'?",
     {"A": "Fluorine", "B": "Iron", "C": "Lead"}, "B", 3, "Elements", "chem,elements",
     "'Fe' comes from Latin 'ferrum' (iron)."),

    ("What does the '2' in H₂O indicate?",
     {"A": "The charge", "B": "The number of oxygen atoms", "C": "The number of hydrogen atoms"}, "C", 3, "Formulas", "chem,formulas",
     "The subscript 2 means two hydrogen atoms per molecule of water."),

    ("What is the pH range of an acidic solution?",
     {"A": "0 – 6.9", "B": "7.1 – 14", "C": "Only 7"}, "A", 3, "Acids & Bases", "chem,ph",
     "Solutions with pH below 7 are acidic. pH 7 is neutral; above 7 is basic."),

    ("Which gas is most abundant in Earth's atmosphere?",
     {"A": "Oxygen", "B": "Nitrogen", "C": "Carbon dioxide"}, "B", 3, "Gases", "chem,gases",
     "Nitrogen makes up about 78% of the atmosphere, oxygen about 21%."),

    ("What is an allotrope of carbon that is a good conductor and used in pencils?",
     {"A": "Diamond", "B": "Graphite", "C": "Fullerene"}, "B", 3, "Carbon", "chem,carbon",
     "Graphite is soft, conducts electricity, and is used in pencil leads (mixed with clay)."),

    ("In the periodic table, elements in the same column (group) share:",
     {"A": "The same atomic mass", "B": "The same number of valence electrons", "C": "The same number of neutrons"}, "B", 3, "Periodic Table", "chem,periodic",
     "Elements in the same group have the same number of valence (outer-shell) electrons, giving similar chemistry."),

    # ---------- HARD (4-5) ----------
    ("What is Avogadro's number (approximately)?",
     {"A": "6.022 × 10²³", "B": "3.14 × 10²³", "C": "9.81 × 10²³"}, "A", 4, "Stoichiometry", "chem,constants",
     "Avogadro's number is 6.022 × 10²³ particles per mole — a fundamental stoichiometry constant."),

    ("What is an isotope?",
     {"A": "An atom with a different number of protons", "B": "An atom with a different number of neutrons", "C": "An atom with a different charge"}, "B", 4, "Atomic Structure", "chem,isotopes",
     "Isotopes are atoms of the same element (same protons) with different numbers of neutrons."),

    ("Which is the strongest naturally occurring acid known?",
     {"A": "Sulfuric acid", "B": "Hydrochloric acid", "C": "Fluoroantimonic acid"}, "C", 5, "Acids", "chem,acids",
     "Fluoroantimonic acid (HSbF₆) is the strongest superacid — about 10¹⁶ times stronger than pure sulfuric acid."),

    ("What is the atomic mass of carbon (standard atomic weight)?",
     {"A": "6.011", "B": "12.011", "C": "14.007"}, "B", 4, "Atomic Structure", "chem,atoms",
     "Carbon's standard atomic weight is 12.011 u, reflecting the natural mix of isotopes ¹²C and ¹³C."),

    ("What is electrolysis?",
     {"A": "Splitting a compound using electricity", "B": "Combining metals by heat", "C": "Filtering a solution"}, "A", 4, "Electrochemistry", "chem,electrolysis",
     "Electrolysis uses electric current to drive a non-spontaneous chemical reaction, e.g. splitting water into H₂ and O₂."),

    ("What is the oxidation state of oxygen in most compounds?",
     {"A": "+2", "B": "-2", "C": "0"}, "B", 4, "Redox", "chem,redox",
     "Oxygen is usually -2 (except in peroxides where it is -1, and in OF₂ where it is +2)."),

    ("Which quantum number describes the shape of an electron's orbital?",
     {"A": "Principal (n)", "B": "Azimuthal (l)", "C": "Magnetic (m)", "D": "Spin (s)"}, "B", 5, "Quantum", "chem,quantum",
     "The azimuthal (angular momentum) quantum number 'l' determines orbital shape (s, p, d, f)."),

    ("What is Le Chatelier's principle about?",
     {"A": "Reaction rate depends on temperature", "B": "Equilibrium shifts to counter external stress", "C": "Gases always expand when heated"}, "B", 5, "Equilibrium", "chem,equilibrium",
     "Le Chatelier's principle states that a system at equilibrium will shift to counteract any change in concentration, pressure, or temperature."),

    ("What is the difference between a covalent and an ionic bond?",
     {"A": "Covalent shares electrons; ionic transfers electrons", "B": "Covalent uses protons; ionic uses neutrons", "C": "They are the same"}, "A", 4, "Bonding", "chem,bonding",
     "In a covalent bond, atoms share electron pairs. In an ionic bond, one atom donates electrons and the other accepts them."),

    ("Which of these is a noble gas?",
     {"A": "Chlorine", "B": "Argon", "C": "Nitrogen"}, "B", 4, "Elements", "chem,noble-gases",
     "Argon is a noble gas (Group 18). Noble gases are chemically inert due to full outer shells."),
]


SOMALI_QUESTIONS = [
    # ---------- EASY (1-2) ----------
    ("Waa immisa xaraf af-Soomaaligu?",
     {"A": "18", "B": "21", "C": "28"}, "B", 1, "Aasaas", "somali,alphabet",
     "Af-Soomaaligu wuxuu leeyahay 21 xaraf marka la isticmaalayo qoraalka Laatiinka (latin)."),

    ("Waa maxay macnaha ereyga 'nabad'?",
     {"A": "Dagaal", "B": "Nabad", "C": "Cunto"}, "B", 1, "Ereyo", "somali,vocabulary",
     "'Nabad' waa xaalad aan dagaal jirin — peace."),

    ("Waa maxay macnaha 'mahadsanid'?",
     {"A": "Thank you", "B": "Please", "C": "Sorry"}, "A", 1, "Ereyo", "somali,vocabulary",
     "'Mahadsanid' waa ereyga lagu mahadceliyo — 'thank you'."),

    ("'Hooyo' waxay ka dhigan tahay:",
     {"A": "Father", "B": "Mother", "C": "Sister"}, "B", 1, "Ereyo", "somali,vocabulary,family",
     "'Hooyo' waa mother. 'Aabbe' waa father; 'walaal' waa sibling."),

    ("'Aabbe' waa ereyga loo isticmaalo:",
     {"A": "Mother", "B": "Father", "C": "Uncle"}, "B", 1, "Ereyo", "somali,vocabulary,family",
     "'Aabbe' waa 'father'. Waa ereyga asaasiga ah ee qoyska."),

    ("Magaca caasimadda Soomaaliya waa:",
     {"A": "Hargeysa", "B": "Muqdisho", "C": "Kismaayo"}, "B", 1, "Juqraafi", "somali,geography",
     "Muqdisho waa caasimadda Soomaaliya."),

    ("Ereyga 'walaal' waxa uu tilmaamayaa:",
     {"A": "A friend of the family", "B": "A brother or sister", "C": "A neighbour"}, "B", 1, "Ereyo", "somali,vocabulary,family",
     "'Walaal' waxa loola jeedaa walaal (brother ama sister). Waxaa sidoo kale loo isticmaalaa sida sharaf u ah qof kasta."),

    ("Waa maxay macnaha 'subax wanaagsan'?",
     {"A": "Good night", "B": "Good morning", "C": "Good evening"}, "B", 2, "Salaan", "somali,greetings",
     "'Subax wanaagsan' = good morning. La mid ah: 'galab wanaagsan', 'habeen wanaagsan'."),

    # ---------- MEDIUM (3) ----------
    ("Waa maxay macnaha 'dugsi'?",
     {"A": "Teacher", "B": "School", "C": "Book"}, "B", 3, "Ereyo", "somali,vocabulary",
     "'Dugsi' waa school. 'Macallin' waa teacher; 'buug' waa book."),

    ("Ereyga 'cusub' waxa uu ka soo horjeedaa:",
     {"A": "Weyn", "B": "Hore", "C": "Yar"}, "B", 3, "Ereyo", "somali,vocabulary",
     "'Cusub' (new) wuxuu ka soo horjeedaa 'hore' (old)."),

    ("'Xornimo' waxay ka dhigan tahay:",
     {"A": "Wealth", "B": "Freedom/Independence", "C": "Strength"}, "B", 3, "Ereyo", "somali,vocabulary",
     "'Xornimo' waa freedom ama independence."),

    ("Af-Soomaaligu wuxuu ka tirsan yahay qoyska luqadaha:",
     {"A": "Bantu", "B": "Cushitic", "C": "Semitic"}, "B", 3, "Luqad", "somali,linguistics",
     "Af-Soomaaligu wuxuu ka tirsan yahay qoyska Cushitic ee luqadaha Afro-Asiatic."),

    ("Qoraalka Laatiinka ee af-Soomaaligu wuxuu si rasmi ah loo qaatay sanadkii:",
     {"A": "1960", "B": "1972", "C": "1980"}, "B", 3, "Taariikh", "somali,history",
     "Sanadkii 1972, qoraalka Laatiinka (Latin script) ayaa si rasmi ah loo qaatay af-Soomaaliga."),

    ("'Maalmaha todobada' waxay tilmaamayaan:",
     {"A": "The seven days of the week", "B": "The seven months", "C": "The seven seasons"}, "A", 3, "Ereyo", "somali,vocabulary",
     "'Maalmaha todobada' waa maalmaha usbuuca (the seven days of the week)."),

    ("Waa maxay 'gabay'?",
     {"A": "A type of poem", "B": "A type of dance", "C": "A type of food"}, "A", 3, "Dhaqan", "somali,culture",
     "'Gabay' waa nooc ka mid ah gabayada Soomaalida — traditional Somali poetry."),

    ("'Salaan' waxay ka dhigan tahay:",
     {"A": "Goodbye", "B": "Greeting", "C": "Question"}, "B", 3, "Salaan", "somali,greetings",
     "'Salaan' = greeting. Isla markaana waa ereyga loo adeegsado marka qof la salaanayo."),

    ("'Halkan' waxay tilmaamaysaa:",
     {"A": "There", "B": "Here", "C": "Everywhere"}, "B", 3, "Ereyo", "somali,vocabulary",
     "'Halkan' = here. 'Halkaas' = there."),

    ("Waa maxay 'afartan'?",
     {"A": "30", "B": "40", "C": "50"}, "B", 3, "Tirooyin", "somali,numbers",
     "'Afartan' = 40. Tusaale kale: 'toban' = 10, 'labaatan' = 20, 'soddon' = 30."),

    ("'Cunto' waa:",
     {"A": "Water", "B": "Food", "C": "Shelter"}, "B", 3, "Ereyo", "somali,vocabulary",
     "'Cunto' = food. 'Biyo' = water."),

    ("'Waddo' waxay ka dhigan tahay:",
     {"A": "Road", "B": "Vehicle", "C": "Bridge"}, "A", 3, "Ereyo", "somali,vocabulary",
     "'Waddo' = road, path."),

    # ---------- HARD (4-5) ----------
    ("Waa maxay 'suugaan'?",
     {"A": "Literature", "B": "Music", "C": "Dance"}, "A", 4, "Dhaqan", "somali,culture",
     "'Suugaan' = literature. Waxaa ka mid ah gabayada, sheekooyinka, iyo maahmaahyada."),

    ("Ereyga 'Soomaaliyeey toosoo' waxaa loo arkaa:",
     {"A": "Heesta qaranka", "B": "Maahmaah", "C": "Sheeko"}, "A", 4, "Taariikh", "somali,history",
     "'Soomaaliyeey toosoo' waa heesta qaranka Soomaaliya ee hore."),

    ("'Maahmaah' waxay ka dhigan tahay:",
     {"A": "A proverb", "B": "A song", "C": "A riddle"}, "A", 4, "Dhaqan", "somali,culture",
     "'Maahmaah' = proverb. Tusaale: 'Nin walba wuxuu cunaa waxa uu beertay'."),

    ("'Cilmi' waxay ka dhigan tahay:",
     {"A": "Knowledge", "B": "Wealth", "C": "Power"}, "A", 4, "Ereyo", "somali,vocabulary",
     "'Cilmi' = knowledge. 'Cilmi-baaris' = research; 'aqoon' sidoo kale waa knowledge."),

    ("Waa maxay macnaha 'dhaqan'?",
     {"A": "Culture", "B": "Language", "C": "Religion"}, "A", 4, "Dhaqan", "somali,culture",
     "'Dhaqan' = culture. Waxaa weheliya luqadda, cuntada, iyo caadooyinka."),

    ("'Geesinimo' waxay tilmaamaysaa:",
     {"A": "Courage/Heroism", "B": "Wealth", "C": "Wisdom"}, "A", 4, "Dhaqan", "somali,culture",
     "'Geesinimo' = courage, heroism. Waa qiime Soomaaliyeed oo la qadariyo."),

    ("Lambarka 'hal milyan' waa:",
     {"A": "100,000", "B": "1,000,000", "C": "10,000,000"}, "B", 4, "Tirooyin", "somali,numbers",
     "'Hal milyan' = 1,000,000. 'Hal bilyan' = 1,000,000,000."),

    ("'Kow iyo toban' waa lambarka:",
     {"A": "9", "B": "10", "C": "11"}, "C", 4, "Tirooyin", "somali,numbers",
     "'Kow iyo toban' = 11. 'Toban' kaligiis = 10."),

    ("Ereyga 'buug' waa:",
     {"A": "A pencil", "B": "A book", "C": "A paper"}, "B", 4, "Ereyo", "somali,vocabulary",
     "'Buug' = book. 'Qalin' = pen; 'warqad' = paper/letter."),

    ("Ereyga 'dhaqaalaha' waxay ka dhigan tahay:",
     {"A": "Education", "B": "Economy", "C": "Environment"}, "B", 5, "Ereyo", "somali,economy",
     "'Dhaqaalaha' = the economy. 'Dhaqaale' = economic/financial."),
]


QUESTIONS_BY_SUBJECT = {
    'arabic':      ARABIC_QUESTIONS,
    'mathematics': MATHEMATICS_QUESTIONS,
    'geography':   GEOGRAPHY_QUESTIONS,
    'chemistry':   CHEMISTRY_QUESTIONS,
    SOMALI_CODE:   SOMALI_QUESTIONS,
}


# ============================================================
# HELPERS
# ============================================================

def find_admin():
    """Return the first admin user (id + name) for created_by/updated_by."""
    cursor = execute_with_retry(
        "SELECT id, first_name, last_name FROM students "
        "WHERE is_admin = 1 ORDER BY id ASC LIMIT 1"
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def question_exists(subject_code, question_text):
    cursor = execute_with_retry(
        "SELECT id FROM questions "
        "WHERE subject_code = ? AND LOWER(question_text) = LOWER(?) AND status = 'active'",
        (subject_code, question_text)
    )
    return cursor.fetchone() is not None


def insert_question(subject_code, q, admin_id):
    """q is the tuple from QUESTIONS_BY_SUBJECT."""
    (question_text, options, correct, difficulty,
     chapter, tags, explanation) = q

    full_tags = tags + ',' + TEST_TAG if tags else TEST_TAG

    execute_with_retry("""
        INSERT INTO questions (
            subject_code, question_text, options, correct_answer,
            difficulty, chapter, tags, explanation,
            created_by, updated_by, status, version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?)
    """, (
        subject_code,
        question_text,
        to_json(options),
        correct,
        difficulty,
        chapter,
        full_tags,
        explanation,
        admin_id,
        admin_id,
        now(),
        now(),
    ), commit=True)


def clean_test_questions(dry_run=False):
    """Delete every question tagged test_seed."""
    if dry_run:
        cursor = execute_with_retry(
            "SELECT COUNT(*) AS c FROM questions WHERE tags LIKE ?",
            (f'%{TEST_TAG}%',)
        )
        row = cursor.fetchone()
        return row['c'] if row else 0

    cursor = execute_with_retry(
        "DELETE FROM questions WHERE tags LIKE ?",
        (f'%{TEST_TAG}%',),
        commit=True
    )
    return cursor.rowcount


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description='Seed 150 test questions across 5 subjects.'
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview what would be inserted without writing.')
    parser.add_argument('--clean', action='store_true',
                        help='Delete all previously seeded test questions first.')
    args = parser.parse_args()

    print("=" * 70)
    print("  NUUNPLATFORM — TEST QUESTION SEEDER")
    print("=" * 70)
    print(f"Database: {Config.DATABASE_PATH}")
    print(f"Tag:      {TEST_TAG}")
    if args.dry_run:
        print("Mode:     DRY RUN (no writes)")
    print("=" * 70)
    print()

    # ---- --clean branch ----
    if args.clean:
        removed = clean_test_questions(dry_run=args.dry_run)
        if args.dry_run:
            print(f"[DRY RUN] Would delete {removed} question(s) tagged '{TEST_TAG}'.")
        else:
            print(f"Deleted {removed} question(s) tagged '{TEST_TAG}'.")
        print()
        # If --clean only (no seeding), exit here
        if args.dry_run:
            return
        # If --clean AND not meant to seed after: caller can ctrl-C or we proceed
        # Proceeding: we always seed after cleaning unless user only wanted clean.
        # Comment the next return if you want --clean to also seed.
        # return

    # ---- admin lookup ----
    admin = find_admin()
    if not admin and not args.dry_run:
        print("❌ No admin user found. Create at least one admin before seeding.")
        print("   (Questions need a created_by value for the FK.)")
        sys.exit(1)
    admin_id = admin['id'] if admin else 0
    admin_name = f"{admin['first_name']} {admin['last_name']}" if admin else "(dry run)"
    print(f"Admin (created_by): #{admin_id} — {admin_name}")
    print()

    total_inserted = 0
    total_skipped = 0

    for subject_code, questions in QUESTIONS_BY_SUBJECT.items():
        print(f"── Subject: {subject_code} ({len(questions)} questions) ──")

        subject_inserted = 0
        subject_skipped = 0

        for i, q in enumerate(questions, 1):
            question_text = q[0]
            try:
                if question_exists(subject_code, question_text):
                    subject_skipped += 1
                    print(f"  [{i:02d}] SKIP (exists): {question_text[:60]}")
                    continue

                if args.dry_run:
                    subject_inserted += 1
                    print(f"  [{i:02d}] WOULD INSERT: {question_text[:60]}")
                    continue

                insert_question(subject_code, q, admin_id)
                subject_inserted += 1
                print(f"  [{i:02d}] ✓ {question_text[:60]}")

            except Exception as e:
                print(f"  [{i:02d}] ✗ FAILED: {question_text[:60]} — {e}")

        print(f"  → inserted: {subject_inserted}  skipped: {subject_skipped}")
        print()
        total_inserted += subject_inserted
        total_skipped += subject_skipped

    print("=" * 70)
    if args.dry_run:
        print(f"[DRY RUN] Would insert: {total_inserted}, Skip: {total_skipped}")
    else:
        print(f"Done. Inserted: {total_inserted}, Skipped: {total_skipped}")
    print("=" * 70)


if __name__ == '__main__':
    main()