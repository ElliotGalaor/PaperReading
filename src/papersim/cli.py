"""Ligne de commande : papersim --help"""

import logging

import typer

from . import db, pipeline, search
from .config import settings
from .ingest import arxiv, openalex, semantic_scholar

app = typer.Typer(help="Recherche de papiers scientifiques similaires.", no_args_is_help=True)


@app.callback()
def _setup(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(message)s")


def _ingest(papers, *, enrich: bool, embed: bool) -> None:
    if embed and settings.embedding_source == "local":
        typer.echo("Calcul des embeddings avec SPECTER2 (local)...")
    with db.connect() as conn:
        ids = pipeline.ingest(conn, papers, enrich=enrich, embed=embed)
    typer.echo(f"{len(ids)} papiers enregistrés.")


@app.command("init-db")
def init_db() -> None:
    """Crée l'extension pgvector et les tables (sans risque sur une base existante)."""
    db.init_db()
    typer.echo("Base initialisée.")


@app.command("ingest-arxiv")
def ingest_arxiv(
    query: str = typer.Argument(..., help='Ex. \'cat:cond-mat.supr-con AND abs:"pseudogap"\''),
    max_results: int = typer.Option(100, "--max"),
    enrich: bool = typer.Option(True, help="Récupérer les références via OpenAlex"),
    embed: bool = typer.Option(True, help="Calculer ou télécharger les embeddings"),
) -> None:
    """Importe des papiers depuis arXiv."""
    papers = arxiv.search_arxiv(query, max_results)
    typer.echo(f"{len(papers)} papiers récupérés sur arXiv.")
    _ingest(papers, enrich=enrich, embed=embed)


@app.command("ingest-openalex")
def ingest_openalex(
    search_query: str = typer.Argument(..., metavar="SEARCH"),
    max_results: int = typer.Option(100, "--max"),
    filter: str | None = typer.Option(None, help="Filtre OpenAlex, ex. 'publication_year:>2018'"),
    embed: bool = typer.Option(True),
) -> None:
    """Importe des papiers depuis OpenAlex (références incluses)."""
    papers = openalex.search_openalex(search_query, max_results, filter)
    typer.echo(f"{len(papers)} papiers récupérés sur OpenAlex.")
    _ingest(papers, enrich=False, embed=embed)


@app.command("ingest-s2")
def ingest_s2(
    search_query: str = typer.Argument(..., metavar="SEARCH"),
    max_results: int = typer.Option(100, "--max", help="1000 au plus"),
    year: str | None = typer.Option(None, help="Ex. '2018-' ou '2015-2020'"),
    fields_of_study: str | None = typer.Option(
        None, "--fields", help="Ex. 'Physics,Mathematics,Chemistry'"
    ),
    enrich: bool = typer.Option(True, help="Récupérer les références via OpenAlex"),
    embed: bool = typer.Option(True),
) -> None:
    """Importe des papiers depuis Semantic Scholar (avec leurs vecteurs SPECTER2)."""
    papers = semantic_scholar.search_s2(search_query, max_results, year, fields_of_study)
    n_vec = sum(1 for p in papers if p.embedding)
    typer.echo(f"{len(papers)} papiers récupérés sur Semantic Scholar ({n_vec} avec vecteur).")
    _ingest(papers, enrich=enrich, embed=embed)


@app.command()
def enrich() -> None:
    """Complète via OpenAlex les papiers qui n'ont pas encore de références."""
    with db.connect() as conn:
        n = pipeline.enrich_from_openalex(conn)
    typer.echo(f"{n} papiers enrichis.")


@app.command()
def embed() -> None:
    """Remplit les embeddings manquants (source : PAPERSIM_EMBEDDING_SOURCE)."""
    with db.connect() as conn:
        n = pipeline.compute_embeddings(conn)
    typer.echo(f"{n} embeddings ajoutés ({settings.active_embedding_model}).")


def _print_results(results: list[dict]) -> None:
    for i, r in enumerate(results, start=1):
        p = r["paper"]
        signals = ", ".join(f"{name} #{s['rank']}" for name, s in r["signals"].items())
        typer.echo(f"{i:>2}. [{r['score']:.4f}] {p['title']} ({p['year'] or '?'})")
        typer.echo(f"      {p['id']}  —  {signals}")


@app.command()
def similar(paper_id: str, k: int = typer.Option(10, "-k")) -> None:
    """Affiche les papiers les plus proches d'un papier du corpus."""
    with db.connect() as conn:
        try:
            results = search.find_similar(conn, paper_id, k=k)
        except KeyError:
            raise typer.BadParameter(f"Papier inconnu : {paper_id}")
    _print_results(results)


@app.command("search")
def search_cmd(query: str, k: int = typer.Option(10, "-k")) -> None:
    """Recherche par texte libre."""
    embedder = None
    if settings.embedding_source == "local":
        from .embeddings import get_embedder

        typer.echo("Chargement de SPECTER2...")
        embedder = get_embedder()
    else:
        typer.echo("Mode s2 : recherche lexicale seulement (pas de modèle local pour la requête).")
    with db.connect() as conn:
        _print_results(search.search_text(conn, embedder, query, k=k))


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Lance l'API (documentation interactive sur /docs)."""
    import uvicorn

    uvicorn.run("papersim.api:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
