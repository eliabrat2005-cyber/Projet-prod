"""Thèmes générés programmatiquement — des centaines de questions par thème.

Chaque générateur construit un pool STABLE (seed fixe) de questions au même
format que les JSON (q / choices / answer), pour que l'anti-répétition par
hash de question fonctionne pareil partout.
"""
from __future__ import annotations

import random

# ─── Données : pays, capitale, code ISO2, continent ─────────────────────────
# Le drapeau emoji est dérivé du code ISO2 (indicateurs régionaux Unicode).

COUNTRIES: list[tuple[str, str, str, str]] = [
    ("la France", "Paris", "FR", "Europe"),
    ("l'Allemagne", "Berlin", "DE", "Europe"),
    ("l'Italie", "Rome", "IT", "Europe"),
    ("l'Espagne", "Madrid", "ES", "Europe"),
    ("le Portugal", "Lisbonne", "PT", "Europe"),
    ("le Royaume-Uni", "Londres", "GB", "Europe"),
    ("l'Irlande", "Dublin", "IE", "Europe"),
    ("la Belgique", "Bruxelles", "BE", "Europe"),
    ("les Pays-Bas", "Amsterdam", "NL", "Europe"),
    ("la Suisse", "Berne", "CH", "Europe"),
    ("l'Autriche", "Vienne", "AT", "Europe"),
    ("la Grèce", "Athènes", "GR", "Europe"),
    ("la Suède", "Stockholm", "SE", "Europe"),
    ("la Norvège", "Oslo", "NO", "Europe"),
    ("le Danemark", "Copenhague", "DK", "Europe"),
    ("la Finlande", "Helsinki", "FI", "Europe"),
    ("l'Islande", "Reykjavik", "IS", "Europe"),
    ("la Pologne", "Varsovie", "PL", "Europe"),
    ("la Tchéquie", "Prague", "CZ", "Europe"),
    ("la Slovaquie", "Bratislava", "SK", "Europe"),
    ("la Hongrie", "Budapest", "HU", "Europe"),
    ("la Roumanie", "Bucarest", "RO", "Europe"),
    ("la Bulgarie", "Sofia", "BG", "Europe"),
    ("la Croatie", "Zagreb", "HR", "Europe"),
    ("la Serbie", "Belgrade", "RS", "Europe"),
    ("la Slovénie", "Ljubljana", "SI", "Europe"),
    ("l'Albanie", "Tirana", "AL", "Europe"),
    ("l'Ukraine", "Kiev", "UA", "Europe"),
    ("la Russie", "Moscou", "RU", "Europe"),
    ("la Biélorussie", "Minsk", "BY", "Europe"),
    ("la Lituanie", "Vilnius", "LT", "Europe"),
    ("la Lettonie", "Riga", "LV", "Europe"),
    ("l'Estonie", "Tallinn", "EE", "Europe"),
    ("la Turquie", "Ankara", "TR", "Europe"),
    ("le Luxembourg", "Luxembourg", "LU", "Europe"),
    ("Malte", "La Valette", "MT", "Europe"),
    ("Chypre", "Nicosie", "CY", "Europe"),
    ("le Monténégro", "Podgorica", "ME", "Europe"),
    ("la Macédoine du Nord", "Skopje", "MK", "Europe"),
    ("la Bosnie-Herzégovine", "Sarajevo", "BA", "Europe"),
    ("la Moldavie", "Chisinau", "MD", "Europe"),
    ("les États-Unis", "Washington", "US", "Amérique"),
    ("le Canada", "Ottawa", "CA", "Amérique"),
    ("le Mexique", "Mexico", "MX", "Amérique"),
    ("le Brésil", "Brasília", "BR", "Amérique"),
    ("l'Argentine", "Buenos Aires", "AR", "Amérique"),
    ("le Chili", "Santiago", "CL", "Amérique"),
    ("le Pérou", "Lima", "PE", "Amérique"),
    ("la Colombie", "Bogota", "CO", "Amérique"),
    ("le Venezuela", "Caracas", "VE", "Amérique"),
    ("l'Équateur", "Quito", "EC", "Amérique"),
    ("la Bolivie", "La Paz", "BO", "Amérique"),
    ("le Paraguay", "Asuncion", "PY", "Amérique"),
    ("l'Uruguay", "Montevideo", "UY", "Amérique"),
    ("Cuba", "La Havane", "CU", "Amérique"),
    ("la Jamaïque", "Kingston", "JM", "Amérique"),
    ("Haïti", "Port-au-Prince", "HT", "Amérique"),
    ("la République dominicaine", "Saint-Domingue", "DO", "Amérique"),
    ("le Guatemala", "Guatemala", "GT", "Amérique"),
    ("le Panama", "Panama", "PA", "Amérique"),
    ("le Costa Rica", "San José", "CR", "Amérique"),
    ("la Chine", "Pékin", "CN", "Asie"),
    ("le Japon", "Tokyo", "JP", "Asie"),
    ("la Corée du Sud", "Séoul", "KR", "Asie"),
    ("la Corée du Nord", "Pyongyang", "KP", "Asie"),
    ("l'Inde", "New Delhi", "IN", "Asie"),
    ("le Pakistan", "Islamabad", "PK", "Asie"),
    ("le Bangladesh", "Dacca", "BD", "Asie"),
    ("le Népal", "Katmandou", "NP", "Asie"),
    ("le Sri Lanka", "Colombo", "LK", "Asie"),
    ("la Thaïlande", "Bangkok", "TH", "Asie"),
    ("le Viêt Nam", "Hanoï", "VN", "Asie"),
    ("le Cambodge", "Phnom Penh", "KH", "Asie"),
    ("le Laos", "Vientiane", "LA", "Asie"),
    ("la Birmanie", "Naypyidaw", "MM", "Asie"),
    ("la Malaisie", "Kuala Lumpur", "MY", "Asie"),
    ("Singapour", "Singapour", "SG", "Asie"),
    ("l'Indonésie", "Jakarta", "ID", "Asie"),
    ("les Philippines", "Manille", "PH", "Asie"),
    ("la Mongolie", "Oulan-Bator", "MN", "Asie"),
    ("le Kazakhstan", "Astana", "KZ", "Asie"),
    ("l'Ouzbékistan", "Tachkent", "UZ", "Asie"),
    ("l'Afghanistan", "Kaboul", "AF", "Asie"),
    ("l'Iran", "Téhéran", "IR", "Asie"),
    ("l'Irak", "Bagdad", "IQ", "Asie"),
    ("l'Arabie saoudite", "Riyad", "SA", "Asie"),
    ("les Émirats arabes unis", "Abou Dabi", "AE", "Asie"),
    ("le Qatar", "Doha", "QA", "Asie"),
    ("le Koweït", "Koweït", "KW", "Asie"),
    ("Israël", "Jérusalem", "IL", "Asie"),
    ("le Liban", "Beyrouth", "LB", "Asie"),
    ("la Jordanie", "Amman", "JO", "Asie"),
    ("la Syrie", "Damas", "SY", "Asie"),
    ("la Géorgie", "Tbilissi", "GE", "Asie"),
    ("l'Arménie", "Erevan", "AM", "Asie"),
    ("l'Azerbaïdjan", "Bakou", "AZ", "Asie"),
    ("l'Égypte", "Le Caire", "EG", "Afrique"),
    ("le Maroc", "Rabat", "MA", "Afrique"),
    ("l'Algérie", "Alger", "DZ", "Afrique"),
    ("la Tunisie", "Tunis", "TN", "Afrique"),
    ("la Libye", "Tripoli", "LY", "Afrique"),
    ("le Sénégal", "Dakar", "SN", "Afrique"),
    ("la Côte d'Ivoire", "Yamoussoukro", "CI", "Afrique"),
    ("le Ghana", "Accra", "GH", "Afrique"),
    ("le Nigeria", "Abuja", "NG", "Afrique"),
    ("le Cameroun", "Yaoundé", "CM", "Afrique"),
    ("le Gabon", "Libreville", "GA", "Afrique"),
    ("la RD Congo", "Kinshasa", "CD", "Afrique"),
    ("le Congo", "Brazzaville", "CG", "Afrique"),
    ("l'Éthiopie", "Addis-Abeba", "ET", "Afrique"),
    ("le Kenya", "Nairobi", "KE", "Afrique"),
    ("la Tanzanie", "Dodoma", "TZ", "Afrique"),
    ("l'Ouganda", "Kampala", "UG", "Afrique"),
    ("l'Afrique du Sud", "Pretoria", "ZA", "Afrique"),
    ("le Zimbabwe", "Harare", "ZW", "Afrique"),
    ("la Zambie", "Lusaka", "ZM", "Afrique"),
    ("l'Angola", "Luanda", "AO", "Afrique"),
    ("le Mozambique", "Maputo", "MZ", "Afrique"),
    ("Madagascar", "Antananarivo", "MG", "Afrique"),
    ("le Mali", "Bamako", "ML", "Afrique"),
    ("le Niger", "Niamey", "NE", "Afrique"),
    ("le Tchad", "N'Djamena", "TD", "Afrique"),
    ("le Soudan", "Khartoum", "SD", "Afrique"),
    ("la Guinée", "Conakry", "GN", "Afrique"),
    ("le Burkina Faso", "Ouagadougou", "BF", "Afrique"),
    ("le Bénin", "Porto-Novo", "BJ", "Afrique"),
    ("le Togo", "Lomé", "TG", "Afrique"),
    ("le Rwanda", "Kigali", "RW", "Afrique"),
    ("la Namibie", "Windhoek", "NA", "Afrique"),
    ("le Botswana", "Gaborone", "BW", "Afrique"),
    ("la Mauritanie", "Nouakchott", "MR", "Afrique"),
    ("l'Australie", "Canberra", "AU", "Océanie"),
    ("la Nouvelle-Zélande", "Wellington", "NZ", "Océanie"),
    ("les Fidji", "Suva", "FJ", "Océanie"),
    ("la Papouasie-Nouvelle-Guinée", "Port Moresby", "PG", "Océanie"),
]

# ─── Données : vocabulaire français → anglais ───────────────────────────────

ENGLISH_WORDS: list[tuple[str, str]] = [
    ("chien", "dog"), ("chat", "cat"), ("oiseau", "bird"), ("cheval", "horse"),
    ("poisson", "fish"), ("vache", "cow"), ("mouton", "sheep"), ("ours", "bear"),
    ("renard", "fox"), ("lapin", "rabbit"), ("souris", "mouse"), ("abeille", "bee"),
    ("papillon", "butterfly"), ("araignée", "spider"), ("grenouille", "frog"),
    ("maison", "house"), ("cuisine", "kitchen"), ("chambre", "bedroom"),
    ("escalier", "stairs"), ("toit", "roof"), ("fenêtre", "window"), ("porte", "door"),
    ("clé", "key"), ("mur", "wall"), ("sol", "floor"), ("plafond", "ceiling"),
    ("pomme", "apple"), ("fraise", "strawberry"), ("raisin", "grape"),
    ("pêche", "peach"), ("poire", "pear"), ("cerise", "cherry"), ("ananas", "pineapple"),
    ("pain", "bread"), ("beurre", "butter"), ("fromage", "cheese"), ("lait", "milk"),
    ("œuf", "egg"), ("poulet", "chicken"), ("riz", "rice"), ("miel", "honey"),
    ("sel", "salt"), ("poivre", "pepper"), ("sucre", "sugar"), ("farine", "flour"),
    ("couteau", "knife"), ("fourchette", "fork"), ("cuillère", "spoon"),
    ("assiette", "plate"), ("verre", "glass"), ("bouteille", "bottle"),
    ("tête", "head"), ("main", "hand"), ("pied", "foot"), ("bras", "arm"),
    ("jambe", "leg"), ("cœur", "heart"), ("dent", "tooth"), ("cheveux", "hair"),
    ("épaule", "shoulder"), ("genou", "knee"), ("dos", "back"), ("peau", "skin"),
    ("soleil", "sun"), ("lune", "moon"), ("étoile", "star"), ("nuage", "cloud"),
    ("pluie", "rain"), ("neige", "snow"), ("vent", "wind"), ("orage", "storm"),
    ("arbre", "tree"), ("fleur", "flower"), ("feuille", "leaf"), ("herbe", "grass"),
    ("forêt", "forest"), ("montagne", "mountain"), ("rivière", "river"), ("mer", "sea"),
    ("plage", "beach"), ("île", "island"), ("désert", "desert"), ("lac", "lake"),
    ("voiture", "car"), ("vélo", "bicycle"), ("avion", "plane"), ("bateau", "boat"),
    ("train", "train"), ("camion", "truck"), ("route", "road"), ("pont", "bridge"),
    ("livre", "book"), ("stylo", "pen"), ("crayon", "pencil"), ("cahier", "notebook"),
    ("école", "school"), ("professeur", "teacher"), ("élève", "pupil"),
    ("bureau", "desk"), ("horloge", "clock"), ("ciseaux", "scissors"),
    ("chaussure", "shoe"), ("chaussette", "sock"), ("chemise", "shirt"),
    ("pantalon", "trousers"), ("manteau", "coat"), ("chapeau", "hat"),
    ("gant", "glove"), ("écharpe", "scarf"), ("robe", "dress"), ("jupe", "skirt"),
    ("matin", "morning"), ("soir", "evening"), ("nuit", "night"), ("semaine", "week"),
    ("mois", "month"), ("année", "year"), ("aujourd'hui", "today"), ("demain", "tomorrow"),
    ("hier", "yesterday"), ("heure", "hour"), ("argent", "money"), ("travail", "work"),
    ("ami", "friend"), ("famille", "family"), ("frère", "brother"), ("sœur", "sister"),
    ("fils", "son"), ("fille", "daughter"), ("mari", "husband"), ("femme", "wife"),
    ("roi", "king"), ("reine", "queen"), ("guerre", "war"), ("paix", "peace"),
    ("ville", "city"), ("pays", "country"), ("monde", "world"), ("carte", "map"),
]


def _flag(iso2: str) -> str:
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in iso2)


def _pick_distractors(rng: random.Random, correct: str, pool: list[str], n: int = 3) -> list[str]:
    candidates = [x for x in pool if x != correct]
    return rng.sample(candidates, n)


def build_capitales() -> list[dict]:
    """Capitale → pays et pays → capitale, distracteurs du même continent."""
    rng = random.Random(2024)
    out = []
    for country, capital, _iso, continent in COUNTRIES:
        same = [c for _, c, _, cont in COUNTRIES if cont == continent and c != capital]
        pool = same if len(same) >= 3 else [c for _, c, _, _ in COUNTRIES]
        distr = _pick_distractors(rng, capital, pool)
        out.append({"q": f"Quelle est la capitale de {country} ?",
                    "choices": [capital, *distr], "answer": 0})
        same_countries = [p for p, _, _, cont in COUNTRIES if cont == continent and p != country]
        pool_c = same_countries if len(same_countries) >= 3 else [p for p, _, _, _ in COUNTRIES]
        distr_c = _pick_distractors(rng, country, pool_c)
        out.append({"q": f"De quel pays {capital} est-elle la capitale ?",
                    "choices": [_cap_first(country), *[_cap_first(d) for d in distr_c]],
                    "answer": 0})
    return out


def _cap_first(name: str) -> str:
    # « la France » → « La France » pour l'affichage en bouton
    return name[0].upper() + name[1:]


def build_drapeaux() -> list[dict]:
    """Drapeau emoji → pays, distracteurs du même continent."""
    rng = random.Random(77)
    out = []
    for country, _cap, iso, continent in COUNTRIES:
        same = [p for p, _, _, cont in COUNTRIES if cont == continent and p != country]
        pool = same if len(same) >= 3 else [p for p, _, _, _ in COUNTRIES]
        distr = _pick_distractors(rng, country, pool)
        out.append({"q": f"À quel pays appartient ce drapeau : {_flag(iso)} ?",
                    "choices": [_cap_first(country), *[_cap_first(d) for d in distr]],
                    "answer": 0})
    return out


def build_calcul() -> list[dict]:
    """Calcul mental : pool stable de ~400 opérations variées."""
    rng = random.Random(42)
    out, seen = [], set()

    def add(q: str, ans: int, spread: int) -> None:
        if q in seen:
            return
        seen.add(q)
        wrongs: set[int] = set()
        while len(wrongs) < 3:
            w = ans + rng.choice([-3, -2, -1, 1, 2, 3]) * max(1, spread)
            if w != ans and w >= 0:
                wrongs.add(w)
            spread += 1  # élargit si blocage
        out.append({"q": q, "choices": [str(ans), *[str(w) for w in sorted(wrongs)]],
                    "answer": 0})

    for _ in range(120):
        a, b = rng.randint(12, 89), rng.randint(12, 89)
        add(f"Combien font {a} + {b} ?", a + b, 3)
    for _ in range(90):
        a, b = rng.randint(30, 99), rng.randint(11, 29)
        add(f"Combien font {a} − {b} ?", a - b, 3)
    for _ in range(90):
        a, b = rng.randint(3, 12), rng.randint(6, 19)
        add(f"Combien font {a} × {b} ?", a * b, max(2, a))
    for n in range(11, 26):
        add(f"Combien font {n} au carré ?", n * n, n)
    for _ in range(50):
        pct = rng.choice([10, 20, 25, 50, 75])
        base = rng.choice([40, 60, 80, 120, 160, 200, 240, 300, 400, 500])
        add(f"Combien font {pct} % de {base} ?", base * pct // 100, max(4, base // 20))
    for _ in range(40):
        a = rng.randint(13, 60)
        add(f"Quel est le double de {a} ?", a * 2, 4)
        b = rng.choice(range(22, 120, 2))
        add(f"Quelle est la moitié de {b} ?", b // 2, 3)
    return out


def build_anglais() -> list[dict]:
    """Traductions FR → EN et EN → FR."""
    rng = random.Random(7)
    fr_all = [f for f, _ in ENGLISH_WORDS]
    en_all = [e for _, e in ENGLISH_WORDS]
    out = []
    for fr, en in ENGLISH_WORDS:
        distr = _pick_distractors(rng, en, en_all)
        out.append({"q": f"Comment dit-on « {fr} » en anglais ?",
                    "choices": [en, *distr], "answer": 0})
        distr_fr = _pick_distractors(rng, fr, fr_all)
        out.append({"q": f"Que signifie « {en} » en français ?",
                    "choices": [fr, *distr_fr], "answer": 0})
    return out


GENERATED_TOPICS: list[dict] = [
    {"id": "capitales", "name": "Capitales du monde", "icon": "🏙️", "color": "#2e86c1",
     "build": build_capitales},
    {"id": "drapeaux", "name": "Drapeaux", "icon": "🚩", "color": "#cb4335",
     "build": build_drapeaux},
    {"id": "calcul", "name": "Calcul mental", "icon": "🧮", "color": "#7d3c98",
     "build": build_calcul},
    {"id": "anglais", "name": "Anglais", "icon": "🇬🇧", "color": "#1f618d",
     "build": build_anglais},
]


def generated_topics() -> dict[str, dict]:
    """Construit les thèmes générés au format standard (avec cache implicite côté questions.py)."""
    topics = {}
    for spec in GENERATED_TOPICS:
        questions = spec["build"]()
        topics[spec["id"]] = {"id": spec["id"], "name": spec["name"],
                              "icon": spec["icon"], "color": spec["color"],
                              "questions": questions}
    return topics
