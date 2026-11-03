// Noms d'auteurs cliquables, menant à leur bibliographie sur le site d'où vient le papier :
// - papier arXiv : recherche d'auteur arXiv au format « Nom, Prénom » (le plus précis) ;
// - papier Semantic Scholar : recherche Semantic Scholar ;
// - sinon : Google Scholar (recherche « author: »).
// Recherche par nom : des homonymes peuvent apparaître.
(function () {
  "use strict";

  function arxivQuery(name) {
    const parts = name.trim().split(/\s+/);
    // Nom de famille = dernier mot ; prénom = premier mot (arXiv complète le reste).
    return parts.length > 1 ? `${parts[parts.length - 1]}, ${parts[0]}` : name.trim();
  }

  function authorLink(name, paper) {
    if (paper.arxiv_id) {
      const params = new URLSearchParams({
        searchtype: "author", query: arxivQuery(name), abstracts: "show",
        order: "-announced_date_first", size: "50",
      });
      return { url: `https://arxiv.org/search/?${params}`, site: "arXiv" };
    }
    if (paper.s2_id) {
      return { url: `https://www.semanticscholar.org/search?${new URLSearchParams({ q: name })}`, site: "Semantic Scholar" };
    }
    return { url: `https://scholar.google.com/scholar?${new URLSearchParams({ q: `author:"${name}"` })}`, site: "Google Scholar" };
  }

  function authorsNode(paper, max = 3) {
    const authors = paper.authors || [];
    if (!authors.length) return null;
    const span = document.createElement("span");
    span.className = "authors";

    function render(all) {
      span.replaceChildren();
      (all ? authors : authors.slice(0, max)).forEach((name, i) => {
        if (i) span.append(", ");
        const { url, site } = authorLink(name, paper);
        const a = document.createElement("a");
        a.className = "author";
        a.href = url;
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = name;
        a.title = `Publications de ${name} sur ${site}`;
        span.append(a);
      });
      if (authors.length > max) {
        const more = document.createElement("button");
        more.type = "button";
        more.className = "more-authors";
        more.textContent = all ? "réduire" : `et ${authors.length - max} autre${authors.length - max > 1 ? "s" : ""}`;
        more.setAttribute("aria-expanded", String(all));
        more.addEventListener("click", () => {
          render(!all);
          span.querySelector(".more-authors").focus();
        });
        span.append(" ", more);
      }
    }

    render(false);
    return span;
  }

  // Ligne « auteurs · année · revue » ; details=false : auteurs seuls.
  function metaNode(paper, details = true, className = "meta") {
    const div = document.createElement("div");
    div.className = className;
    const authors = authorsNode(paper);
    const extras = details ? [paper.year, paper.venue].filter(Boolean) : [];
    if (authors) div.append(authors);
    if (extras.length) div.append((authors ? " · " : "") + extras.join(" · "));
    if (!authors && !extras.length) div.textContent = details ? paper.id : "Auteurs inconnus";
    return div;
  }

  window.PaperAuthors = { authorLink, authorsNode, metaNode };
})();
