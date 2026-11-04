// Favoris enregistrés dans la base (API /favorites), partagés par les pages web.
// Toutes les étoiles d'une même page se mettent à jour ensemble.
(function () {
  "use strict";

  const ids = new Set();
  const listeners = new Set();

  function notify() {
    document.querySelectorAll("button.fav").forEach(update);
    listeners.forEach((fn) => fn(ids));
  }

  function emitError(message) {
    window.dispatchEvent(new CustomEvent("paperfavorites:error", { detail: message }));
  }

  // Charge la liste complète ; renvoie [{ paper, added_at }, …] (vide si le serveur ne répond pas).
  async function load() {
    try {
      const resp = await fetch("/favorites");
      if (!resp.ok) return [];
      const data = await resp.json();
      ids.clear();
      for (const f of data.favorites || []) ids.add(f.paper.id);
      notify();
      return data.favorites || [];
    } catch {
      return [];
    }
  }

  async function toggle(paperId) {
    const add = !ids.has(paperId);
    const resp = await fetch(`/favorites/${encodeURI(paperId)}`, { method: add ? "PUT" : "DELETE" });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(typeof data.detail === "string" ? data.detail : `Erreur du serveur (${resp.status}).`);
    }
    if (add) ids.add(paperId); else ids.delete(paperId);
    notify();
  }

  function update(button) {
    const on = ids.has(button.dataset.paperId);
    button.setAttribute("aria-pressed", String(on));
    button.textContent = on ? "★" : "☆";
    button.title = on ? "Retirer des favoris" : "Ajouter aux favoris";
    button.setAttribute("aria-label", `${on ? "Retirer des favoris" : "Ajouter aux favoris"} : ${button.dataset.title}`);
  }

  // Bouton étoile pour un papier du corpus.
  function button(paper) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "fav";
    b.dataset.paperId = paper.id;
    b.dataset.title = paper.title || paper.id;
    update(b);
    b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        await toggle(paper.id);
      } catch (err) {
        emitError(err.message === "Failed to fetch"
          ? "Le serveur ne répond pas : favori non enregistré."
          : `Favori non enregistré : ${err.message}`);
      } finally {
        b.disabled = false;
      }
    });
    return b;
  }

  window.PaperFavorites = {
    load,
    toggle,
    button,
    has: (id) => ids.has(id),
    count: () => ids.size,
    onChange: (fn) => listeners.add(fn),
  };
})();
