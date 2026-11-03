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
papersim search "suppression of superconductivity by disorder"
papersim serve                 # interface sur http://127.0.0.1:8000, API sur /docs
```

### Interface web

Ouvrez http://127.0.0.1:8000 et glissez un article (PDF, `.txt` ou `.md`, 20 Mo au plus) :

- si la première page porte un identifiant arXiv ou un DOI, et que le titre retrouvé en ligne
  correspond au fichier, le papier est **ajouté au corpus** et comparé avec tous les signaux
  (sémantique, mots, citations) ;
- sinon, la recherche se fait sur le **titre et l'abstract extraits** du texte (sans citations).

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
| `GET` | `/papers/{id}` | Fiche d'un papier |
| `GET` | `/papers/{id}/similar?k=10&aspect=topic` | Papiers similaires, avec rang par signal |
| `GET` | `/search?q=...` | Recherche en texte libre |
| `POST` | `/search/file` | Fichier en `multipart/form-data` (`file`, `k`) : papiers similaires |
| `GET` | `/` | Interface web |
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
  static/index.html   interface web (glisser-déposer)
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
