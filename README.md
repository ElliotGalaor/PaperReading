# PaperReading

Recherche de papiers scientifiques similaires (paquet Python et commande : `papersim`). Cette version est le **MVP (étape 1)** : abstracts
via arXiv / OpenAlex, embeddings SPECTER2, recherche hybride fusionnée par RRF.

## Démarrage

Prérequis : Python ≥ 3.10, Docker (pour PostgreSQL + pgvector).

```bash
docker compose up -d
python -m venv .venv
.venv\Scripts\activate        # Windows ; source .venv/bin/activate ailleurs
pip install -e ".[embeddings,dev]"
copy .env.example .env         # puis renseigner PAPERSIM_OPENALEX_MAILTO
papersim init-db
```

Constituer un corpus :

```bash
papersim ingest-arxiv 'cat:cond-mat.supr-con AND abs:"pseudogap"' --max 300
papersim ingest-openalex "topological insulator" --max 200 --filter "publication_year:>2018"
```

Interroger :

```bash
papersim similar arxiv:2101.00001
papersim search "suppression of superconductivity by disorder"
papersim serve                 # API sur http://127.0.0.1:8000/docs
```

Le premier calcul d'embeddings télécharge SPECTER2 (~500 Mo).

## API

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/papers` | `{"arxiv_ids": [...], "dois": [...]}` : import + indexation |
| `GET` | `/papers/{id}` | Fiche d'un papier |
| `GET` | `/papers/{id}/similar?k=10&aspect=topic` | Papiers similaires, avec rang par signal |
| `GET` | `/search?q=...` | Recherche en texte libre |
| `POST` | `/compare` | `{"a": id, "b": id}` : cosinus, références communes, catégories communes |

## Organisation

```
src/papersim/
  ingest/arxiv.py     API arXiv (Atom), 1 requête / 3 s
  ingest/openalex.py  API OpenAlex : abstracts, références (graphe de citations)
  ids.py              normalisation arXiv / DOI / OpenAlex (déduplication)
  db.py, schema.sql   PostgreSQL : papers, paper_embeddings (par aspect), paper_references
  embeddings.py       SPECTER2 (adapter proximity ; adhoc_query pour le texte libre)
  pipeline.py         stockage -> enrichissement OpenAlex -> embeddings
  search.py           signaux semantic / lexical / citations + fusion RRF
  api.py, cli.py      FastAPI et ligne de commande
```

Identifiant interne : `arxiv:<id>` en priorité, sinon `doi:<doi>`, sinon `openalex:<W…>`.
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

## Limites connues

- Pour un papier arXiv, la notice OpenAlex liée au DOI arXiv a parfois moins de références que la
  version publiée en revue.
- Les papiers introuvables sur OpenAlex sont re-tentés à chaque `papersim enrich`.
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
