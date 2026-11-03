"""Mots-clés et thèmes d'un papier.

- Mots-clés : YAKE (statistique, sans modèle ni réseau) sur le titre et l'abstract, puis
  nettoyage : pas de mot vide à l'intérieur d'une expression, pas de doublons qui se
  chevauchent, pas de fragments sans intérêt.
- Thèmes : catégories arXiv du papier, avec leur nom en clair. Pour un texte non reconnu,
  catégories les plus fréquentes parmi les papiers similaires (pondérées par le rang).
"""

import re
from collections import defaultdict

TOP_KEYWORDS = 8
MAX_THEMES = 3

# Mots qui ne doivent apparaître ni au début, ni au milieu, ni à la fin d'un mot-clé.
_STOPWORDS = set(
    """a an and are as at be been being between both but by can could do does done due each
    either et etc for from further has have having here how however i if in into is it its
    may might more most much must near no nor not of on onto or other our out over per such
    than that the their them then there these they this those through thus to too under up
    upon us using via was we were what when where whether which while who whose why will with
    within without would yet also al based new show shows shown study studies studied paper
    work result results present presents presented find found propose proposed approach method
    methods effect effects first one two three high low large small different various several
    provide provides provided allow allows allowed enable enables lead leads reveal reveals
    demonstrate demonstrates exhibit exhibits observe observed suggest suggests indicate
    indicates remain remains become becomes make makes give gives obtain obtained report
    reported investigate investigated consider considered yield yields driven tool tools
    powerful generating probing direct received receive great attention excellent recent
    recently widely important interest increasing growing significant significantly""".split()
)
_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9\-]*[A-Za-z0-9]$|^[A-Za-z]$")
_LATEX = re.compile(r"\$[^$]*\$")


def _norm(token: str) -> str:
    t = token.lower()
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def _acceptable(phrase: str) -> bool:
    tokens = phrase.split()
    if not tokens:
        return False
    for t in tokens:
        if t.lower() in _STOPWORDS or not _TOKEN.match(t):
            return False
    # Un mot seul doit être assez long ou être un sigle (TiN, NbN, BKT…).
    if len(tokens) == 1 and len(tokens[0]) < 4 and not _is_acronym(tokens[0]):
        return False
    # Une expression ne se termine pas par un participe passé (« point contact coupled »).
    if len(tokens) > 1 and tokens[-1].lower().endswith("ed"):
        return False
    return True


def _is_acronym(word: str) -> bool:
    """TiN, NbN, BCS, ESM… (une majuscule après la première lettre, partie par partie :
    « Finite-Temperature » n'en est pas un)."""
    return any(any(c.isupper() for c in part[1:]) for part in word.split("-"))


def extract_keywords(text: str, top: int = TOP_KEYWORDS, body: str | None = None) -> list[str]:
    """Jusqu'à `top` mots-clés (1 à 3 mots), du plus au moins représentatif.

    `body` (l'abstract seul, sans le titre) sert à repérer les noms propres : un mot écrit
    avec une majuscule au milieu d'une phrase (Hubbard, Andreev) garde sa majuscule.
    """
    import yake

    text = _LATEX.sub(" ", text or "")
    if len(text.split()) < 8:
        return []
    candidates = yake.KeywordExtractor(lan="en", n=3, dedupLim=0.9, top=top * 6).extract_keywords(text)

    verbish = _words_before_determiners(text)
    multi: list[tuple[str, set[str]]] = []
    single: list[tuple[str, set[str]]] = []
    for phrase, _score in candidates:  # YAKE : score bas = meilleur, déjà trié
        phrase = phrase.strip(" -")
        if not _acceptable(phrase):
            continue
        words = phrase.split()
        # « disorder controls (the…) » : un mot suivi d'un déterminant est un verbe, pas un nom.
        if len(words) > 1 and words[-1].lower() in verbish:
            continue
        # Mot isolé : seuls les sigles sont fiables ; les autres servent à compléter la liste.
        bucket = multi if len(words) > 1 else single
        bucket.append((phrase, {_norm(t) for t in words}))

    chosen: list[tuple[str, set[str]]] = []
    for phrase, tokens in multi + sorted(single, key=lambda item: not _is_acronym(item[0])):
        # Écarte une expression qui recouvre en grande partie un mot-clé déjà retenu.
        if any(len(tokens & other) / min(len(tokens), len(other)) >= 0.5 for _, other in chosen):
            continue
        chosen.append((phrase, tokens))
        if len(chosen) == top:
            break
    proper = _proper_nouns(_LATEX.sub(" ", body if body is not None else text))
    return [_display(p, proper) for p, _ in chosen]


def _words_before_determiners(text: str) -> set[str]:
    """Mots placés juste avant the/a/an/its/their : verbes ou prépositions dans un texte anglais."""
    return {m.lower() for m in re.findall(r"\b([A-Za-z][\w\-]*)\s+(?:the|a|an|its|their)\b", text)}


def _proper_nouns(text: str) -> set[str]:
    """Mots écrits avec une majuscule ailleurs qu'en début de phrase."""
    found = set()
    for sentence in re.split(r"[.!?]\s+", text):
        for word in re.findall(r"[A-Za-z][\w\-]*", sentence)[1:]:
            if word[0].isupper():
                found.add(word.lower())
    return found


def _display(phrase: str, proper: set[str]) -> str:
    """Minuscules, sauf sigles et noms propres (TiN, Hubbard, Andreev-Enhanced…)."""
    out = []
    for w in phrase.split():
        if _is_acronym(w) or w.isupper():
            out.append(w)
        elif w.lower() in proper or w.split("-")[0].lower() in proper:
            out.append(w[0].upper() + w[1:].lower())
        else:
            out.append(w.lower())
    return " ".join(out)


# --- Thèmes : catégories arXiv ------------------------------------------------------------

ARXIV_NAMES = {
    # Matière condensée
    "cond-mat.dis-nn": "Systèmes désordonnés et réseaux de neurones",
    "cond-mat.mes-hall": "Physique mésoscopique et nanométrique",
    "cond-mat.mtrl-sci": "Science des matériaux",
    "cond-mat.other": "Matière condensée (autre)",
    "cond-mat.quant-gas": "Gaz quantiques",
    "cond-mat.soft": "Matière molle",
    "cond-mat.stat-mech": "Mécanique statistique",
    "cond-mat.str-el": "Électrons fortement corrélés",
    "cond-mat.supr-con": "Supraconductivité",
    # Astrophysique
    "astro-ph.CO": "Cosmologie",
    "astro-ph.EP": "Planétologie",
    "astro-ph.GA": "Astrophysique des galaxies",
    "astro-ph.HE": "Astrophysique des hautes énergies",
    "astro-ph.IM": "Instrumentation astronomique",
    "astro-ph.SR": "Physique stellaire et solaire",
    # Physique fondamentale
    "gr-qc": "Relativité générale et cosmologie quantique",
    "hep-ex": "Physique des particules (expérience)",
    "hep-lat": "Physique des particules (réseau)",
    "hep-ph": "Physique des particules (phénoménologie)",
    "hep-th": "Physique des particules (théorie)",
    "math-ph": "Physique mathématique",
    "nucl-ex": "Physique nucléaire (expérience)",
    "nucl-th": "Physique nucléaire (théorie)",
    "quant-ph": "Physique quantique",
    "nlin.AO": "Adaptation et systèmes auto-organisés",
    "nlin.CD": "Dynamique chaotique",
    "nlin.CG": "Automates cellulaires et gaz sur réseau",
    "nlin.PS": "Formation de motifs et solitons",
    "nlin.SI": "Systèmes intégrables",
    # Physique appliquée et générale
    "physics.acc-ph": "Physique des accélérateurs",
    "physics.ao-ph": "Physique de l'atmosphère et des océans",
    "physics.app-ph": "Physique appliquée",
    "physics.atm-clus": "Agrégats atomiques et moléculaires",
    "physics.atom-ph": "Physique atomique",
    "physics.bio-ph": "Biophysique",
    "physics.chem-ph": "Physique chimique",
    "physics.class-ph": "Physique classique",
    "physics.comp-ph": "Physique numérique",
    "physics.data-an": "Analyse de données et statistiques",
    "physics.ed-ph": "Enseignement de la physique",
    "physics.flu-dyn": "Dynamique des fluides",
    "physics.gen-ph": "Physique générale",
    "physics.geo-ph": "Géophysique",
    "physics.hist-ph": "Histoire et philosophie de la physique",
    "physics.ins-det": "Instrumentation et détecteurs",
    "physics.med-ph": "Physique médicale",
    "physics.optics": "Optique",
    "physics.plasm-ph": "Physique des plasmas",
    "physics.pop-ph": "Vulgarisation scientifique",
    "physics.soc-ph": "Physique et société",
    "physics.space-ph": "Physique spatiale",
    # Mathématiques
    "math.AC": "Algèbre commutative",
    "math.AG": "Géométrie algébrique",
    "math.AP": "Équations aux dérivées partielles",
    "math.AT": "Topologie algébrique",
    "math.CA": "Analyse classique et EDO",
    "math.CO": "Combinatoire",
    "math.CT": "Théorie des catégories",
    "math.CV": "Variables complexes",
    "math.DG": "Géométrie différentielle",
    "math.DS": "Systèmes dynamiques",
    "math.FA": "Analyse fonctionnelle",
    "math.GM": "Mathématiques générales",
    "math.GN": "Topologie générale",
    "math.GR": "Théorie des groupes",
    "math.GT": "Topologie géométrique",
    "math.HO": "Histoire des mathématiques",
    "math.IT": "Théorie de l'information",
    "math.KT": "K-théorie et homologie",
    "math.LO": "Logique",
    "math.MG": "Géométrie métrique",
    "math.MP": "Physique mathématique",
    "math.NA": "Analyse numérique",
    "math.NT": "Théorie des nombres",
    "math.OA": "Algèbres d'opérateurs",
    "math.OC": "Optimisation et contrôle",
    "math.PR": "Probabilités",
    "math.QA": "Algèbre quantique",
    "math.RA": "Anneaux et algèbres",
    "math.RT": "Théorie des représentations",
    "math.SG": "Géométrie symplectique",
    "math.SP": "Théorie spectrale",
    "math.ST": "Statistique (théorie)",
    # Informatique (catégories les plus courantes)
    "cs.AI": "Intelligence artificielle",
    "cs.CE": "Calcul scientifique et ingénierie",
    "cs.CL": "Traitement du langage",
    "cs.CV": "Vision par ordinateur",
    "cs.DS": "Algorithmique",
    "cs.ET": "Technologies émergentes",
    "cs.IT": "Théorie de l'information",
    "cs.LG": "Apprentissage automatique",
    "cs.NE": "Calcul neuronal et évolutionnaire",
    "stat.ML": "Apprentissage automatique (statistique)",
}
ARCHIVE_NAMES = {
    "astro-ph": "Astrophysique",
    "cond-mat": "Matière condensée",
    "physics": "Physique",
    "math": "Mathématiques",
    "nlin": "Sciences non linéaires",
    "cs": "Informatique",
    "stat": "Statistique",
    "q-bio": "Biologie quantitative",
    "q-fin": "Finance quantitative",
    "eess": "Génie électrique et traitement du signal",
    "econ": "Économie",
}
# fieldsOfStudy de Semantic Scholar
FIELD_NAMES = {
    "Physics": "Physique",
    "Mathematics": "Mathématiques",
    "Chemistry": "Chimie",
    "Materials Science": "Science des matériaux",
    "Computer Science": "Informatique",
    "Engineering": "Ingénierie",
    "Biology": "Biologie",
    "Medicine": "Médecine",
}


def theme_name(code: str) -> str:
    if code in ARXIV_NAMES:
        return ARXIV_NAMES[code]
    if code in FIELD_NAMES:
        return FIELD_NAMES[code]
    archive = code.split(".")[0]
    if archive in ARCHIVE_NAMES:
        return f"{ARCHIVE_NAMES[archive]} ({code})"
    return code


def themes_from_categories(categories: list[str]) -> list[dict]:
    """Thèmes d'un papier : ses catégories, la principale en premier (ordre arXiv)."""
    return [{"code": c, "name": theme_name(c)} for c in list(dict.fromkeys(categories))[:MAX_THEMES]]


def themes_from_results(results: list[dict]) -> list[dict]:
    """Thèmes probables d'un texte : catégories des papiers similaires, pondérées par 1/rang."""
    weights: dict[str, float] = defaultdict(float)
    for rank, r in enumerate(results, start=1):
        for c in r["paper"].get("categories") or []:
            weights[c] += 1 / rank
    best = sorted(weights.items(), key=lambda item: -item[1])[:MAX_THEMES]
    return [{"code": c, "name": theme_name(c)} for c, _ in best]
