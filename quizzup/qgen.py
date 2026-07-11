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


# ─── Données : éléments chimiques (symbole ↔ nom) ──────────────────────────

ELEMENTS: list[tuple[str, str]] = [
    ("H", "l'hydrogène"), ("He", "l'hélium"), ("Li", "le lithium"), ("Be", "le béryllium"),
    ("B", "le bore"), ("C", "le carbone"), ("N", "l'azote"), ("O", "l'oxygène"),
    ("F", "le fluor"), ("Ne", "le néon"), ("Na", "le sodium"), ("Mg", "le magnésium"),
    ("Al", "l'aluminium"), ("Si", "le silicium"), ("P", "le phosphore"), ("S", "le soufre"),
    ("Cl", "le chlore"), ("Ar", "l'argon"), ("K", "le potassium"), ("Ca", "le calcium"),
    ("Ti", "le titane"), ("Cr", "le chrome"), ("Mn", "le manganèse"), ("Fe", "le fer"),
    ("Co", "le cobalt"), ("Ni", "le nickel"), ("Cu", "le cuivre"), ("Zn", "le zinc"),
    ("As", "l'arsenic"), ("Br", "le brome"), ("Kr", "le krypton"), ("Ag", "l'argent"),
    ("Sn", "l'étain"), ("I", "l'iode"), ("Xe", "le xénon"), ("Ba", "le baryum"),
    ("W", "le tungstène"), ("Pt", "le platine"), ("Au", "l'or"), ("Hg", "le mercure"),
    ("Pb", "le plomb"), ("Rn", "le radon"), ("Ra", "le radium"), ("U", "l'uranium"),
    ("Pu", "le plutonium"), ("Mo", "le molybdène"), ("Cd", "le cadmium"), ("Sb", "l'antimoine"),
]

# ─── Données : vocabulaire (espagnol / allemand / italien) ──────────────────

SPANISH_WORDS: list[tuple[str, str]] = [
    ("chien", "perro"), ("chat", "gato"), ("cheval", "caballo"), ("oiseau", "pájaro"),
    ("poisson", "pez"), ("vache", "vaca"), ("cochon", "cerdo"), ("lapin", "conejo"),
    ("maison", "casa"), ("porte", "puerta"), ("fenêtre", "ventana"), ("table", "mesa"),
    ("chaise", "silla"), ("lit", "cama"), ("cuisine", "cocina"), ("clé", "llave"),
    ("pain", "pan"), ("fromage", "queso"), ("lait", "leche"), ("œuf", "huevo"),
    ("pomme", "manzana"), ("fraise", "fresa"), ("orange", "naranja"), ("raisin", "uva"),
    ("eau", "agua"), ("vin", "vino"), ("sel", "sal"), ("sucre", "azúcar"),
    ("poulet", "pollo"), ("viande", "carne"), ("riz", "arroz"), ("gâteau", "pastel"),
    ("tête", "cabeza"), ("main", "mano"), ("pied", "pie"), ("cœur", "corazón"),
    ("œil", "ojo"), ("bouche", "boca"), ("nez", "nariz"), ("oreille", "oreja"),
    ("soleil", "sol"), ("lune", "luna"), ("étoile", "estrella"), ("mer", "mar"),
    ("plage", "playa"), ("montagne", "montaña"), ("fleur", "flor"), ("arbre", "árbol"),
    ("pluie", "lluvia"), ("neige", "nieve"), ("feu", "fuego"), ("vent", "viento"),
    ("livre", "libro"), ("école", "escuela"), ("ami", "amigo"), ("famille", "familia"),
    ("frère", "hermano"), ("sœur", "hermana"), ("fils", "hijo"), ("mère", "madre"),
    ("père", "padre"), ("femme", "mujer"), ("homme", "hombre"), ("enfant", "niño"),
    ("ville", "ciudad"), ("rue", "calle"), ("voiture", "coche"), ("train", "tren"),
    ("avion", "avión"), ("bateau", "barco"), ("argent", "dinero"), ("travail", "trabajo"),
    ("jour", "día"), ("nuit", "noche"), ("matin", "mañana"), ("semaine", "semana"),
    ("année", "año"), ("heure", "hora"), ("temps", "tiempo"), ("monde", "mundo"),
    ("rouge", "rojo"), ("bleu", "azul"), ("vert", "verde"), ("noir", "negro"),
    ("blanc", "blanco"), ("jaune", "amarillo"), ("petit", "pequeño"), ("grand", "grande"),
    ("chaud", "caliente"), ("froid", "frío"), ("chaussure", "zapato"), ("chemise", "camisa"),
]

GERMAN_WORDS: list[tuple[str, str]] = [
    ("chien", "Hund"), ("chat", "Katze"), ("cheval", "Pferd"), ("oiseau", "Vogel"),
    ("poisson", "Fisch"), ("vache", "Kuh"), ("cochon", "Schwein"), ("lapin", "Kaninchen"),
    ("maison", "Haus"), ("porte", "Tür"), ("fenêtre", "Fenster"), ("table", "Tisch"),
    ("chaise", "Stuhl"), ("lit", "Bett"), ("cuisine", "Küche"), ("clé", "Schlüssel"),
    ("pain", "Brot"), ("fromage", "Käse"), ("lait", "Milch"), ("œuf", "Ei"),
    ("pomme", "Apfel"), ("fraise", "Erdbeere"), ("orange", "Orange"), ("raisin", "Traube"),
    ("eau", "Wasser"), ("vin", "Wein"), ("sel", "Salz"), ("sucre", "Zucker"),
    ("poulet", "Hähnchen"), ("viande", "Fleisch"), ("riz", "Reis"), ("gâteau", "Kuchen"),
    ("tête", "Kopf"), ("main", "Hand"), ("pied", "Fuß"), ("cœur", "Herz"),
    ("œil", "Auge"), ("bouche", "Mund"), ("nez", "Nase"), ("oreille", "Ohr"),
    ("soleil", "Sonne"), ("lune", "Mond"), ("étoile", "Stern"), ("mer", "Meer"),
    ("plage", "Strand"), ("montagne", "Berg"), ("fleur", "Blume"), ("arbre", "Baum"),
    ("pluie", "Regen"), ("neige", "Schnee"), ("feu", "Feuer"), ("vent", "Wind"),
    ("livre", "Buch"), ("école", "Schule"), ("ami", "Freund"), ("famille", "Familie"),
    ("frère", "Bruder"), ("sœur", "Schwester"), ("fils", "Sohn"), ("mère", "Mutter"),
    ("père", "Vater"), ("femme", "Frau"), ("homme", "Mann"), ("enfant", "Kind"),
    ("ville", "Stadt"), ("rue", "Straße"), ("voiture", "Auto"), ("train", "Zug"),
    ("avion", "Flugzeug"), ("bateau", "Schiff"), ("argent", "Geld"), ("travail", "Arbeit"),
    ("jour", "Tag"), ("nuit", "Nacht"), ("matin", "Morgen"), ("semaine", "Woche"),
    ("année", "Jahr"), ("monde", "Welt"), ("rouge", "rot"), ("bleu", "blau"),
    ("vert", "grün"), ("noir", "schwarz"), ("blanc", "weiß"), ("jaune", "gelb"),
]

ITALIAN_WORDS: list[tuple[str, str]] = [
    ("chien", "cane"), ("chat", "gatto"), ("cheval", "cavallo"), ("oiseau", "uccello"),
    ("poisson", "pesce"), ("vache", "mucca"), ("cochon", "maiale"), ("lapin", "coniglio"),
    ("maison", "casa"), ("porte", "porta"), ("fenêtre", "finestra"), ("table", "tavolo"),
    ("chaise", "sedia"), ("lit", "letto"), ("cuisine", "cucina"), ("clé", "chiave"),
    ("pain", "pane"), ("fromage", "formaggio"), ("lait", "latte"), ("œuf", "uovo"),
    ("pomme", "mela"), ("fraise", "fragola"), ("orange", "arancia"), ("raisin", "uva"),
    ("eau", "acqua"), ("vin", "vino"), ("sel", "sale"), ("sucre", "zucchero"),
    ("poulet", "pollo"), ("viande", "carne"), ("riz", "riso"), ("gâteau", "torta"),
    ("tête", "testa"), ("main", "mano"), ("pied", "piede"), ("cœur", "cuore"),
    ("œil", "occhio"), ("bouche", "bocca"), ("nez", "naso"), ("oreille", "orecchio"),
    ("soleil", "sole"), ("lune", "luna"), ("étoile", "stella"), ("mer", "mare"),
    ("plage", "spiaggia"), ("montagne", "montagna"), ("fleur", "fiore"), ("arbre", "albero"),
    ("pluie", "pioggia"), ("neige", "neve"), ("feu", "fuoco"), ("vent", "vento"),
    ("livre", "libro"), ("école", "scuola"), ("ami", "amico"), ("famille", "famiglia"),
    ("frère", "fratello"), ("sœur", "sorella"), ("fils", "figlio"), ("mère", "madre"),
    ("père", "padre"), ("femme", "donna"), ("homme", "uomo"), ("enfant", "bambino"),
    ("ville", "città"), ("rue", "strada"), ("voiture", "macchina"), ("train", "treno"),
    ("avion", "aereo"), ("bateau", "barca"), ("argent", "denaro"), ("travail", "lavoro"),
    ("jour", "giorno"), ("nuit", "notte"), ("matin", "mattina"), ("semaine", "settimana"),
    ("année", "anno"), ("monde", "mondo"), ("rouge", "rosso"), ("bleu", "blu"),
    ("vert", "verde"), ("noir", "nero"), ("blanc", "bianco"), ("jaune", "giallo"),
]

# ─── Données : départements français (numéro, nom, préfecture) ──────────────

DEPARTEMENTS: list[tuple[str, str, str]] = [
    ("01", "l'Ain", "Bourg-en-Bresse"), ("02", "l'Aisne", "Laon"), ("03", "l'Allier", "Moulins"),
    ("04", "les Alpes-de-Haute-Provence", "Digne-les-Bains"), ("05", "les Hautes-Alpes", "Gap"),
    ("06", "les Alpes-Maritimes", "Nice"), ("07", "l'Ardèche", "Privas"),
    ("08", "les Ardennes", "Charleville-Mézières"),
    ("09", "l'Ariège", "Foix"), ("10", "l'Aube", "Troyes"), ("11", "l'Aude", "Carcassonne"),
    ("12", "l'Aveyron", "Rodez"), ("13", "les Bouches-du-Rhône", "Marseille"), ("14", "le Calvados", "Caen"),
    ("15", "le Cantal", "Aurillac"), ("16", "la Charente", "Angoulême"), ("17", "la Charente-Maritime", "La Rochelle"),
    ("18", "le Cher", "Bourges"), ("19", "la Corrèze", "Tulle"), ("2A", "la Corse-du-Sud", "Ajaccio"),
    ("2B", "la Haute-Corse", "Bastia"), ("21", "la Côte-d'Or", "Dijon"), ("22", "les Côtes-d'Armor", "Saint-Brieuc"),
    ("23", "la Creuse", "Guéret"), ("24", "la Dordogne", "Périgueux"), ("25", "le Doubs", "Besançon"),
    ("26", "la Drôme", "Valence"), ("27", "l'Eure", "Évreux"), ("28", "l'Eure-et-Loir", "Chartres"),
    ("29", "le Finistère", "Quimper"), ("30", "le Gard", "Nîmes"), ("31", "la Haute-Garonne", "Toulouse"),
    ("32", "le Gers", "Auch"), ("33", "la Gironde", "Bordeaux"), ("34", "l'Hérault", "Montpellier"),
    ("35", "l'Ille-et-Vilaine", "Rennes"), ("36", "l'Indre", "Châteauroux"), ("37", "l'Indre-et-Loire", "Tours"),
    ("38", "l'Isère", "Grenoble"), ("39", "le Jura", "Lons-le-Saunier"), ("40", "les Landes", "Mont-de-Marsan"),
    ("41", "le Loir-et-Cher", "Blois"), ("42", "la Loire", "Saint-Étienne"),
    ("43", "la Haute-Loire", "Le Puy-en-Velay"),
    ("44", "la Loire-Atlantique", "Nantes"), ("45", "le Loiret", "Orléans"), ("46", "le Lot", "Cahors"),
    ("47", "le Lot-et-Garonne", "Agen"), ("48", "la Lozère", "Mende"), ("49", "le Maine-et-Loire", "Angers"),
    ("50", "la Manche", "Saint-Lô"), ("51", "la Marne", "Châlons-en-Champagne"), ("52", "la Haute-Marne", "Chaumont"),
    ("53", "la Mayenne", "Laval"), ("54", "la Meurthe-et-Moselle", "Nancy"), ("55", "la Meuse", "Bar-le-Duc"),
    ("56", "le Morbihan", "Vannes"), ("57", "la Moselle", "Metz"), ("58", "la Nièvre", "Nevers"),
    ("59", "le Nord", "Lille"), ("60", "l'Oise", "Beauvais"), ("61", "l'Orne", "Alençon"),
    ("62", "le Pas-de-Calais", "Arras"), ("63", "le Puy-de-Dôme", "Clermont-Ferrand"),
    ("64", "les Pyrénées-Atlantiques", "Pau"), ("65", "les Hautes-Pyrénées", "Tarbes"),
    ("66", "les Pyrénées-Orientales", "Perpignan"), ("67", "le Bas-Rhin", "Strasbourg"),
    ("68", "le Haut-Rhin", "Colmar"), ("69", "le Rhône", "Lyon"), ("70", "la Haute-Saône", "Vesoul"),
    ("71", "la Saône-et-Loire", "Mâcon"), ("72", "la Sarthe", "Le Mans"), ("73", "la Savoie", "Chambéry"),
    ("74", "la Haute-Savoie", "Annecy"), ("75", "Paris", "Paris"), ("76", "la Seine-Maritime", "Rouen"),
    ("77", "la Seine-et-Marne", "Melun"), ("78", "les Yvelines", "Versailles"), ("79", "les Deux-Sèvres", "Niort"),
    ("80", "la Somme", "Amiens"), ("81", "le Tarn", "Albi"), ("82", "le Tarn-et-Garonne", "Montauban"),
    ("83", "le Var", "Toulon"), ("84", "le Vaucluse", "Avignon"), ("85", "la Vendée", "La Roche-sur-Yon"),
    ("86", "la Vienne", "Poitiers"), ("87", "la Haute-Vienne", "Limoges"), ("88", "les Vosges", "Épinal"),
    ("89", "l'Yonne", "Auxerre"), ("90", "le Territoire de Belfort", "Belfort"),
    ("91", "l'Essonne", "Évry-Courcouronnes"), ("92", "les Hauts-de-Seine", "Nanterre"),
    ("93", "la Seine-Saint-Denis", "Bobigny"), ("94", "le Val-de-Marne", "Créteil"), ("95", "le Val-d'Oise", "Cergy"),
]

# ─── Données : États américains (État, capitale) ────────────────────────────

US_STATES: list[tuple[str, str]] = [
    ("l'Alabama", "Montgomery"), ("l'Alaska", "Juneau"), ("l'Arizona", "Phoenix"),
    ("l'Arkansas", "Little Rock"), ("la Californie", "Sacramento"), ("le Colorado", "Denver"),
    ("le Connecticut", "Hartford"), ("le Delaware", "Dover"), ("la Floride", "Tallahassee"),
    ("la Géorgie", "Atlanta"), ("Hawaï", "Honolulu"), ("l'Idaho", "Boise"),
    ("l'Illinois", "Springfield"), ("l'Indiana", "Indianapolis"), ("l'Iowa", "Des Moines"),
    ("le Kansas", "Topeka"), ("le Kentucky", "Frankfort"), ("la Louisiane", "Baton Rouge"),
    ("le Maine", "Augusta"), ("le Maryland", "Annapolis"), ("le Massachusetts", "Boston"),
    ("le Michigan", "Lansing"), ("le Minnesota", "Saint Paul"), ("le Mississippi", "Jackson"),
    ("le Missouri", "Jefferson City"), ("le Montana", "Helena"), ("le Nebraska", "Lincoln"),
    ("le Nevada", "Carson City"), ("le New Hampshire", "Concord"), ("le New Jersey", "Trenton"),
    ("le Nouveau-Mexique", "Santa Fe"), ("l'État de New York", "Albany"),
    ("la Caroline du Nord", "Raleigh"), ("le Dakota du Nord", "Bismarck"), ("l'Ohio", "Columbus"),
    ("l'Oklahoma", "Oklahoma City"), ("l'Oregon", "Salem"), ("la Pennsylvanie", "Harrisburg"),
    ("le Rhode Island", "Providence"), ("la Caroline du Sud", "Columbia"),
    ("le Dakota du Sud", "Pierre"), ("le Tennessee", "Nashville"), ("le Texas", "Austin"),
    ("l'Utah", "Salt Lake City"), ("le Vermont", "Montpelier"), ("la Virginie", "Richmond"),
    ("l'État de Washington", "Olympia"), ("la Virginie-Occidentale", "Charleston"),
    ("le Wisconsin", "Madison"), ("le Wyoming", "Cheyenne"),
]

# ─── Données : monnaies du monde ────────────────────────────────────────────

CURRENCIES: list[tuple[str, str]] = [
    ("le Japon", "le yen"), ("le Royaume-Uni", "la livre sterling"), ("la Suisse", "le franc suisse"),
    ("les États-Unis", "le dollar américain"), ("le Canada", "le dollar canadien"),
    ("le Mexique", "le peso mexicain"), ("le Brésil", "le réal"), ("l'Argentine", "le peso argentin"),
    ("la Chine", "le yuan"), ("l'Inde", "la roupie indienne"), ("la Russie", "le rouble"),
    ("la Turquie", "la livre turque"), ("la Suède", "la couronne suédoise"),
    ("la Norvège", "la couronne norvégienne"), ("le Danemark", "la couronne danoise"),
    ("la Pologne", "le złoty"), ("la Hongrie", "le forint"), ("la Tchéquie", "la couronne tchèque"),
    ("la Corée du Sud", "le won"), ("la Thaïlande", "le baht"), ("le Vietnam", "le dong"),
    ("l'Indonésie", "la roupie indonésienne"), ("Israël", "le shekel"),
    ("l'Arabie saoudite", "le riyal saoudien"), ("les Émirats arabes unis", "le dirham des Émirats"),
    ("l'Égypte", "la livre égyptienne"), ("le Maroc", "le dirham marocain"),
    ("la Tunisie", "le dinar tunisien"), ("l'Algérie", "le dinar algérien"),
    ("l'Afrique du Sud", "le rand"), ("le Nigeria", "le naira"), ("le Kenya", "le shilling kényan"),
    ("l'Australie", "le dollar australien"), ("la Nouvelle-Zélande", "le dollar néo-zélandais"),
    ("les Philippines", "le peso philippin"), ("la Malaisie", "le ringgit"),
    ("Singapour", "le dollar de Singapour"), ("l'Ukraine", "la hryvnia"), ("la Roumanie", "le leu"),
    ("la Bulgarie", "le lev"), ("l'Islande", "la couronne islandaise"), ("le Pérou", "le sol"),
    ("le Chili", "le peso chilien"), ("la Colombie", "le peso colombien"),
    ("la Bolivie", "le boliviano"), ("le Paraguay", "le guarani"), ("le Guatemala", "le quetzal"),
    ("le Costa Rica", "le colón"), ("le Bangladesh", "le taka"), ("le Pakistan", "la roupie pakistanaise"),
    ("le Népal", "la roupie népalaise"), ("le Cambodge", "le riel"), ("le Laos", "le kip"),
    ("la Birmanie", "le kyat"), ("la Mongolie", "le tugrik"), ("le Kazakhstan", "le tenge"),
    ("la Géorgie", "le lari"), ("l'Arménie", "le dram"), ("l'Azerbaïdjan", "le manat"),
    ("l'Iran", "le rial iranien"),
]

# ─── Données : petits et femelles des animaux ───────────────────────────────

ANIMAL_BABIES: list[tuple[str, str]] = [
    ("la vache", "le veau"), ("la jument", "le poulain"), ("la brebis", "l'agneau"),
    ("la chèvre", "le chevreau"), ("la truie", "le porcelet"), ("la chienne", "le chiot"),
    ("la chatte", "le chaton"), ("la lapine", "le lapereau"), ("l'ourse", "l'ourson"),
    ("la louve", "le louveteau"), ("la lionne", "le lionceau"), ("l'éléphante", "l'éléphanteau"),
    ("la biche", "le faon"), ("la cane", "le caneton"), ("la poule", "le poussin"),
    ("l'oie", "l'oison"), ("la dinde", "le dindonneau"), ("l'aigle", "l'aiglon"),
    ("la girafe", "le girafeau"), ("la baleine", "le baleineau"), ("la renarde", "le renardeau"),
    ("la laie (sanglier)", "le marcassin"), ("la hase (lièvre)", "le levraut"),
    ("l'ânesse", "l'ânon"), ("la chamelle", "le chamelon"), ("la tigresse", "le tigreau"),
    ("la souris", "le souriceau"), ("la pigeonne", "le pigeonneau"), ("la cigogne", "le cigogneau"),
    ("l'hirondelle", "l'hirondeau"),
]

ANIMAL_FEMALES: list[tuple[str, str]] = [
    ("du cheval", "la jument"), ("du sanglier", "la laie"), ("du cerf", "la biche"),
    ("du lièvre", "la hase"), ("du canard", "la cane"), ("du cochon", "la truie"),
    ("du bélier", "la brebis"), ("du taureau", "la vache"), ("du jars", "l'oie"),
    ("du coq", "la poule"), ("du loup", "la louve"), ("du bouc", "la chèvre"),
    ("du singe", "la guenon"), ("du dindon", "la dinde"), ("du mulet", "la mule"),
]

# ─── Données : langues officielles ──────────────────────────────────────────

LANGUAGES: list[tuple[str, str]] = [
    ("au Brésil", "le portugais"), ("au Mexique", "l'espagnol"), ("en Égypte", "l'arabe"),
    ("en Autriche", "l'allemand"), ("en Iran", "le persan"), ("en Israël", "l'hébreu"),
    ("en Chine", "le mandarin"), ("en Grèce", "le grec"), ("aux Pays-Bas", "le néerlandais"),
    ("au Danemark", "le danois"), ("en Finlande", "le finnois"), ("en Islande", "l'islandais"),
    ("en Hongrie", "le hongrois"), ("en Pologne", "le polonais"), ("en Roumanie", "le roumain"),
    ("en Bulgarie", "le bulgare"), ("en Serbie", "le serbe"), ("en Albanie", "l'albanais"),
    ("en Turquie", "le turc"), ("au Vietnam", "le vietnamien"), ("en Thaïlande", "le thaï"),
    ("au Japon", "le japonais"), ("en Corée du Sud", "le coréen"), ("au Cambodge", "le khmer"),
    ("en Indonésie", "l'indonésien"), ("en Mongolie", "le mongol"), ("en Géorgie", "le géorgien"),
    ("en Arménie", "l'arménien"), ("en Ukraine", "l'ukrainien"), ("en Russie", "le russe"),
    ("au Portugal", "le portugais"), ("en Argentine", "l'espagnol"), ("au Maroc", "l'arabe"),
    ("en Angola", "le portugais"), ("au Mozambique", "le portugais"), ("en Somalie", "le somali"),
    ("au Pakistan", "l'ourdou"), ("au Bangladesh", "le bengali"), ("au Népal", "le népalais"),
    ("au Sri Lanka", "le cingalais"), ("en Birmanie", "le birman"), ("en Malaisie", "le malais"),
    ("en Éthiopie", "l'amharique"), ("en Croatie", "le croate"), ("en Slovaquie", "le slovaque"),
    ("en Slovénie", "le slovène"), ("en Lituanie", "le lituanien"), ("en Lettonie", "le letton"),
    ("en Estonie", "l'estonien"), ("en Norvège", "le norvégien"),
]


def _roman(n: int) -> str:
    vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
            (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out


def _pairs_topic(rng_seed: int, pairs: list[tuple[str, str]],
                 q_fwd: str, q_rev: str) -> list[dict]:
    """Thème « paire » générique : question dans les deux sens, distracteurs du pool."""
    rng = random.Random(rng_seed)
    lefts = [a for a, _ in pairs]
    rights = [b for _, b in pairs]
    out = []
    for left, right in pairs:
        out.append({"q": q_fwd.format(left), "choices": [right, *_pick_distractors(rng, right, rights)],
                    "answer": 0})
        out.append({"q": q_rev.format(right), "choices": [left, *_pick_distractors(rng, left, lefts)],
                    "answer": 0})
    return out


def build_elements() -> list[dict]:
    return _pairs_topic(11, ELEMENTS,
                        "Quel élément chimique a pour symbole « {} » ?",
                        "Quel est le symbole chimique de {} ?")


def build_espagnol() -> list[dict]:
    return _pairs_topic(12, SPANISH_WORDS,
                        "Comment dit-on « {} » en espagnol ?",
                        "Que signifie « {} » en français (depuis l'espagnol) ?")


def build_allemand() -> list[dict]:
    return _pairs_topic(13, GERMAN_WORDS,
                        "Comment dit-on « {} » en allemand ?",
                        "Que signifie « {} » en français (depuis l'allemand) ?")


def build_italien() -> list[dict]:
    return _pairs_topic(14, ITALIAN_WORDS,
                        "Comment dit-on « {} » en italien ?",
                        "Que signifie « {} » en français (depuis l'italien) ?")


def build_departements() -> list[dict]:
    rng = random.Random(15)
    names = [n for _, n, _ in DEPARTEMENTS]
    prefs = [p for _, _, p in DEPARTEMENTS]
    out = []
    for num, name, pref in DEPARTEMENTS:
        out.append({"q": f"Quel département porte le numéro {num} ?",
                    "choices": [_cap_first(name), *[_cap_first(d) for d in _pick_distractors(rng, name, names)]],
                    "answer": 0})
        out.append({"q": f"Quelle est la préfecture de {name} ({num}) ?",
                    "choices": [pref, *_pick_distractors(rng, pref, prefs)], "answer": 0})
    return out


def build_etats_usa() -> list[dict]:
    return _pairs_topic(16, [(s, c) for s, c in US_STATES],
                        "Quelle est la capitale de {} (État américain) ?",
                        "De quel État américain {} est-elle la capitale ?")


def build_monnaies() -> list[dict]:
    rng = random.Random(17)
    currs = [c for _, c in CURRENCIES]
    out = []
    for country, curr in CURRENCIES:
        out.append({"q": f"Quelle est la monnaie de {country} ?",
                    "choices": [_cap_first(curr), *[_cap_first(d) for d in _pick_distractors(rng, curr, currs)]],
                    "answer": 0})
    return out


def build_chiffres_romains() -> list[dict]:
    rng = random.Random(18)
    numbers = list(range(1, 41)) + [45, 49, 50, 55, 60, 64, 70, 75, 80, 88, 90, 94, 99,
                                    100, 150, 200, 300, 400, 444, 500, 600, 700, 800, 900,
                                    1000, 1500, 1789, 1900, 1980, 2000, 2024]
    out = []
    for n in numbers:
        r = _roman(n)
        wrongs: set[str] = set()
        while len(wrongs) < 3:
            delta = rng.choice([-10, -5, -4, -2, -1, 1, 2, 4, 5, 10])
            w = n + delta
            if w >= 1 and _roman(w) != r:
                wrongs.add(_roman(w))
        out.append({"q": f"Comment s'écrit {n} en chiffres romains ?",
                    "choices": [r, *sorted(wrongs)], "answer": 0})
        wrong_nums: set[str] = set()
        while len(wrong_nums) < 3:
            delta = rng.choice([-10, -5, -4, -2, -1, 1, 2, 4, 5, 10])
            w = n + delta
            if w >= 1 and w != n:
                wrong_nums.add(str(w))
        out.append({"q": f"Quel nombre s'écrit « {r} » en chiffres romains ?",
                    "choices": [str(n), *sorted(wrong_nums)], "answer": 0})
    return out


def build_petits_animaux() -> list[dict]:
    rng = random.Random(19)
    babies = [b for _, b in ANIMAL_BABIES]
    females = [f for _, f in ANIMAL_FEMALES]
    out = []
    for adult, baby in ANIMAL_BABIES:
        out.append({"q": f"Comment s'appelle le petit de {adult} ?",
                    "choices": [_cap_first(baby), *[_cap_first(d) for d in _pick_distractors(rng, baby, babies)]],
                    "answer": 0})
    for male, female in ANIMAL_FEMALES:
        out.append({"q": f"Comment s'appelle la femelle {male} ?",
                    "choices": [_cap_first(female), *[_cap_first(d) for d in _pick_distractors(rng, female, females)]],
                    "answer": 0})
    return out


def build_langues() -> list[dict]:
    rng = random.Random(20)
    langs = sorted({lang for _, lang in LANGUAGES})
    out = []
    for place, lang in LANGUAGES:
        out.append({"q": f"Quelle langue officielle parle-t-on {place} ?",
                    "choices": [_cap_first(lang), *[_cap_first(d) for d in _pick_distractors(rng, lang, langs)]],
                    "answer": 0})
    return out


GENERATED_TOPICS: list[dict] = [
    {"id": "elements-chimiques", "name": "Éléments chimiques", "icon": "🧪", "color": "#00838f",
     "build": build_elements},
    {"id": "espagnol", "name": "Espagnol", "icon": "🇪🇸", "color": "#bf360c",
     "build": build_espagnol},
    {"id": "allemand", "name": "Allemand", "icon": "🇩🇪", "color": "#4e342e",
     "build": build_allemand},
    {"id": "italien", "name": "Italien", "icon": "🇮🇹", "color": "#2e7d32",
     "build": build_italien},
    {"id": "departements", "name": "Départements français", "icon": "🗺️", "color": "#283593",
     "build": build_departements},
    {"id": "etats-usa", "name": "États américains", "icon": "🗽", "color": "#1565c0",
     "build": build_etats_usa},
    {"id": "monnaies", "name": "Monnaies du monde", "icon": "💰", "color": "#9e7d0a",
     "build": build_monnaies},
    {"id": "chiffres-romains", "name": "Chiffres romains", "icon": "🔢", "color": "#6a1b9a",
     "build": build_chiffres_romains},
    {"id": "petits-animaux", "name": "Petits des animaux", "icon": "🐣", "color": "#ef6c00",
     "build": build_petits_animaux},
    {"id": "langues", "name": "Langues du monde", "icon": "🗣️", "color": "#00695c",
     "build": build_langues},
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
