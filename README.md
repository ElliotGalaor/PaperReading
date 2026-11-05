# PaperReading

Recherche de papiers scientifiques similaires (paquet Python et commande : `papersim`). Cette version est le **MVP (étape 1)** : abstracts
via arXiv / OpenAlex / Semantic Scholar, embeddings SPECTER2, recherche hybride fusionnée par RRF.

## Démarrage

Prérequis : Python ≥ 3.10, Docker (pour PostgreSQL + pgvector).

```bash
docker compose up -d
python -m venv .venv
.venv\Scripts\activate        # Windows ; source .venv/bin/activate ailleurs
pip install -e ".[embeddings,dev]"
copy .env.example .env         # puis renseigner les clés OpenAlex et Semantic Scholar
papersim init-db
```

Constituer un corpus :

```bash
papersim ingest-arxiv 'cat:cond-mat.supr-con AND abs:"pseudogap"' --max 300
papersim ingest-openalex "topological insulator" --max 200 --filter "publication_year:>2018"
papersim ingest-s2 "quantum spin liquid" --max 200 --year 2018- --fields Physics
```

Interroger :

```bash
papersim similar arxiv:2101.00001
papersim similar arxiv:2101.00001 --from 2015 --to 2020   # filtre par année
papersim search "suppression of superconductivity by disorder"
papersim serve                 # interface sur http://127.0.0.1:8000, API sur /docs
```

### Interface web

La page s'ouvre sur l'onglet **Bibliothèque** : les papiers de la base, 25 par page, avec une
recherche (titre, résumé ou auteur), un filtre par thème et un tri (plus récents, plus anciens,
titre, derniers importés). Le bouton **Papiers similaires** d'un papier lance la recherche de ses
voisins dans l'onglet Recherche, sans déposer de fichier. Adresses directes : `/` (Bibliothèque),
`/#recherche`, `/#favoris`.

Dans l'onglet **Recherche**, glissez un article (PDF, `.txt` ou `.md`, 20 Mo au plus) :

- si la première page porte un identifiant arXiv ou un DOI, et que le titre retrouvé en ligne
  correspond au fichier, le papier est **ajouté au corpus** et comparé avec tous les signaux
  (sémantique, mots, citations) ;
- sinon, la recherche se fait sur le **titre et l'abstract extraits** du texte (sans citations).

Le cadre du papier déposé affiche aussi ses **thèmes** et environ **8 mots-clés** :

- thèmes : catégories arXiv du papier, en clair (« Supraconductivité »…). Pour un texte non
  reconnu, ce sont les catégories les plus fréquentes parmi les papiers similaires ;
- mots-clés : extraits du titre et de l'abstract par YAKE (méthode statistique, sans modèle ni
  réseau), puis nettoyés (mots vides, verbes, doublons). Quelques expressions imparfaites restent
  possibles : il n'y a pas d'analyse grammaticale.

Chaque **nom d'auteur** est un lien vers ses publications, sur le site d'où vient le papier :
recherche d'auteur arXiv (format « Nom, Prénom », le plus précis) pour un papier arXiv,
Semantic Scholar pour un papier qui en vient, Google Scholar sinon. C'est une recherche par nom :
des homonymes peuvent apparaître. « et N autres » déplie la liste complète des auteurs.

Les **formules LaTeX** des titres et résumés (`$T_c$`, `$H$-$φ$`…) sont mises en forme avec
KaTeX, chargé depuis un CDN (jsDelivr) : sans connexion Internet, le texte brut reste affiché.

**Comptes** : la Bibliothèque et la recherche sont ouvertes à tous ; les favoris demandent un
compte, et chaque compte a les siens. L'onglet **Compte**, collé au bord droit de la fenêtre,
glisse au survol de la souris (ou au clavier avec Tab, ou d'un appui sur écran tactile) et
montre « Se connecter » / « Créer un compte », ou le nom du compte et « Se déconnecter ». Ces
boutons ouvrent un panneau coulissant de connexion, sans quitter la page ; une étoile cliquée sans être
connecté l'ouvre aussi, et le papier est mis en favori dès la connexion. La page `/login` reste
disponible en accès direct. `PAPERSIM_ALLOW_SIGNUP=false` ferme les inscriptions, les comptes se créent alors
avec `papersim create-user NOM` (le mot de passe est demandé sans être affiché). Les favoris
enregistrés avant l'arrivée des comptes reviennent au **premier compte créé**.

Sécurité : mots de passe hachés avec scrypt ; session dans un cookie HttpOnly dont la base ne
garde que l'empreinte ; attente imposée après 5 échecs de connexion. Le serveur écoute sur
`127.0.0.1` (cette machine seulement). Pour l'ouvrir à d'autres machines
(`papersim serve --host 0.0.0.0`), utilisez un réseau de confiance ou un proxy HTTPS, puis
`PAPERSIM_COOKIE_SECURE=true` : en HTTP simple, les mots de passe circulent en clair.

**Favoris** : l'étoile ☆ à côté d'un papier (résultats, papier reconnu, pages de classement)
l'enregistre ; ★ le retire. L'onglet **Favoris** (ou l'adresse `/#favoris`) liste les papiers
enregistrés, du plus récent au plus ancien, avec leur date d'ajout. Les favoris sont stockés dans
la base PostgreSQL (table `favorites`, créée automatiquement au démarrage du serveur) : ils ne
dépendent pas du navigateur. Un papier saisi en texte libre, non reconnu, n'est pas dans le
corpus et ne peut donc pas être mis en favori.

Une fois les résultats affichés :

- le **curseur Années** (deux poignées, bornées aux années du corpus) filtre par année de
  publication, sans renvoyer le fichier. Poignées en butée = pas de filtre de ce côté ;
- le choix **Affichage** montre au choix le titre et les auteurs (**Small**), les informations
  de base : année, revue, signaux (**Medium**), ou en plus le résumé (**Large**) ;
- un clic sur une étiquette **Mots** ou **Sémantique** ouvre, dans un nouvel onglet, les
  15 premiers papiers de ce seul classement (avant fusion), pour la même recherche et la même
  période. Le papier cliqué y est mis en évidence. Ce choix est mémorisé dans le navigateur.

Le filtre d'années s'applique avant la sélection des meilleurs candidats : on obtient
les papiers les plus proches dans la période choisie. Les papiers sans année sont alors exclus.

Seules les 3 premières pages sont lues, et le fichier n'est pas conservé. Les sources externes
indisponibles (arXiv, OpenAlex, Semantic Scholar) n'empêchent pas la recherche : un
avertissement s'affiche. Un PDF scanné (image sans texte) est refusé.

Le premier calcul d'embeddings télécharge SPECTER2 (~500 Mo).

### Embeddings : local ou Semantic Scholar

`PAPERSIM_EMBEDDING_SOURCE` choisit d'où viennent les vecteurs SPECTER2 :

- `local` (défaut) : calculés sur cette machine. Nécessite `pip install -e ".[embeddings]"` (torch).
- `s2` : téléchargés depuis Semantic Scholar, y compris pour les papiers importés depuis arXiv ou
  OpenAlex (recherche par identifiant arXiv / DOI). Pas de torch ni de GPU. Demandez une clé
  gratuite : sans clé, l'API répond presque toujours « 429 Too Many Requests ».

Les deux familles de vecteurs sont rangées sous des noms de modèle distincts
(`allenai/specter2` et `semantic-scholar/specter_v2`) et ne sont jamais comparées entre elles,
car leur compatibilité exacte n'est pas vérifiée. Changer de source demande de lancer
`papersim embed`. En mode `s2`, la recherche en texte libre n'utilise que le signal lexical
(aucun modèle local ne peut encoder la requête).

## API

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/papers` | `{"arxiv_ids": [...], "dois": [...]}` : import + indexation |
| `GET` | `/papers?q=&category=&sort=&page=&per_page=` | Parcours paginé du corpus (Bibliothèque) |
| `GET` | `/papers/{id}` | Fiche d'un papier |
| `GET` | `/papers/{id}/similar?k=10&year_min=&year_max=` | Papiers similaires, avec rang par signal |
| `GET` | `/search?q=...&year_min=&year_max=` | Recherche en texte libre |
| `POST` | `/search/text` | `{"query", "k", "year_min", "year_max"}` : idem pour un texte long |
| `GET` | `/papers/{id}/ranking/{signal}?k=15` | Classement d'un seul signal (`semantic`, `lexical`, `citations`) |
| `POST` | `/search/text/ranking` | `{"query", "signal", "k", "year_min", "year_max"}` : idem pour un texte |
| `GET` | `/favorites` | Favoris du compte connecté (plus récent d'abord) |
| `PUT` | `/favorites/{id}` | Ajoute un favori (compte connecté) |
| `DELETE` | `/favorites/{id}` | Retire un favori (compte connecté) |
| `POST` | `/auth/signup`, `/auth/login` | `{"username", "password"}` : inscription, connexion (cookie de session) |
| `POST` | `/auth/logout` | Déconnexion |
| `GET` | `/auth/me` | Compte connecté (ou `null`) |
| `GET` | `/corpus/categories` | Thèmes présents dans le corpus, avec leur nombre de papiers |
| `GET` | `/corpus/years` | Années extrêmes du corpus |
| `POST` | `/search/file` | Fichier en `multipart/form-data` (`file`, `k`, `year_min`, `year_max`) |
| `GET` | `/` | Interface web |
| `GET` | `/ranking` | Page du classement d'un signal |
| `POST` | `/compare` | `{"a": id, "b": id}` : cosinus, références communes, catégories communes |

## Organisation

```
src/papersim/
  ingest/arxiv.py     API arXiv (Atom), avec garde-fous (voir plus bas)
  ingest/openalex.py  API OpenAlex : abstracts, références (graphe de citations)
  ingest/semantic_scholar.py  API Semantic Scholar : métadonnées + vecteurs SPECTER2
  ids.py              normalisation arXiv / DOI / OpenAlex (déduplication)
  db.py, schema.sql   PostgreSQL : papers, paper_embeddings (par aspect), paper_references
  embeddings.py       SPECTER2 (adapter proximity ; adhoc_query pour le texte libre)
  pipeline.py         stockage -> enrichissement OpenAlex -> embeddings
  extract.py          texte, titre, abstract, identifiants d'un fichier déposé
  filesearch.py       recherche à partir d'un fichier (papier reconnu ou texte libre)
  keywords.py         mots-clés (YAKE) et thèmes (catégories arXiv) du papier déposé
  static/index.html   interface web (glisser-déposer)
  static/ranking.html page du classement d'un seul signal
  static/authors.js   liens des auteurs vers leur bibliographie (partagé par les deux pages)
  static/math.js      rendu des formules LaTeX avec KaTeX (partagé par les deux pages)
  static/favorites.js boutons étoile des favoris (partagé par les deux pages)
  static/session.js   barre « Se connecter / Se déconnecter » (partagé par les deux pages)
  static/login.html   page de connexion et d'inscription
  auth.py             comptes, mots de passe (scrypt), sessions, limitation des essais
  search.py           signaux semantic / lexical / citations + fusion RRF
  api.py, cli.py      FastAPI et ligne de commande
```

Identifiant interne : `arxiv:<id>` en priorité, sinon `doi:<doi>`, `openalex:<W…>` ou `s2:<paperId>`.
Un papier déjà connu par l'un de ses identifiants est fusionné, pas dupliqué.

## Choix de conception

- **RRF plutôt qu'une somme pondérée** : les scores des signaux n'ont pas la même échelle. Les
  poids (`weights=`) se règleront avec le jeu d'évaluation.
- **Table `paper_embeddings` par aspect** : l'aspect `topic` (titre + abstract) existe déjà ;
  `problem`, `method`, `limitations` s'ajouteront sans changer le schéma.
- **Couplage bibliographique dès le MVP** : les papiers arXiv sont enrichis par OpenAlex
  (via le DOI `10.48550/arXiv.<id>`) pour récupérer leurs références.
- **Lexical** : plein texte PostgreSQL (`ts_rank`), proche de BM25 sans en être. Pour un vrai BM25,
  voir l'extension ParadeDB `pg_search`.

### Garde-fous arXiv

L'API arXiv est gratuite, mais ses conditions d'utilisation imposent au plus une requête toutes les
3 secondes, sur une seule connexion, pour l'ensemble de vos machines. arXiv peut bloquer un accès
qui ne les respecte pas. Le projet applique :

- **une requête à la fois, 3 s d'écart minimum**, pour tout le programme (requêtes simultanées
  de l'API web comprises) et entre programmes lancés en même temps sur la machine (verrou fichier
  `arxiv.lock` dans `PAPERSIM_CACHE_DIR`) ;
- **réessais limités** (3 au plus) sur 429 / 503, en respectant l'en-tête `Retry-After` ;
- **plafond de résultats** : `PAPERSIM_ARXIV_MAX_RESULTS` (2000 par défaut, 30 000 au plus) ;
- **cache d'une journée** : une requête identique n'est pas renvoyée le même jour.

Le verrou ne coordonne pas plusieurs machines : n'interrogez arXiv que depuis une seule.

## Limites connues

- Pour un papier arXiv, la notice OpenAlex liée au DOI arXiv a parfois moins de références que la
  version publiée en revue.
- Les papiers introuvables sur OpenAlex sont re-tentés à chaque `papersim enrich`.
- Les références viennent uniquement d'OpenAlex : un papier Semantic Scholar sans DOI ni
  identifiant arXiv n'a pas de signal de citations.
- Quand deux sources donnent un abstract différent, le premier enregistré est conservé.
- L'import via `POST /papers` est synchrone (acceptable pour quelques dizaines de papiers).

## Suite (feuille de route)

1. **Texte intégral** : pour la physique et les maths, préférer la **source LaTeX d'arXiv**
   (`https://arxiv.org/e-print/<id>`) au PDF : sections et formules sont exactes. GROBID / Marker /
   Nougat en repli pour les papiers sans source.
2. **Extraction structurée par LLM** : problème, approche, difficultés/limites, contexte, avec
   une reformulation indépendante du domaine et le passage source de chaque champ. Vectoriser
   chaque champ comme un nouvel aspect, et versionner modèle + prompt.
3. **Re-ranking et explications** : un LLM reçoit les ~20 premiers candidats et explique la proximité.
4. **Évaluation** : 15-20 papiers-requêtes avec résultats jugés à la main, mesure nDCG@10.
5. **Interface** : liste de similaires, vue comparative, carte UMAP.
