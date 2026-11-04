// Favoris du compte connecté (API /favorites), partagés par les pages web.
// Toutes les étoiles d'une même page se mettent à jour ensemble.
// Sans compte connecté, cliquer sur une étoile mène à la page de connexion.
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

  function goToLogin() {
    location.href = window.PaperSession ? PaperSession.loginUrl() : "/login";
  }

  // Charge la liste complète ; renvoie [{ paper, added_at }, …]
  // (vide sans compte connecté ou si le serveur ne répond pas).
  async function load() {
    try {
      const resp = await fetch("/favorites");
      ids.clear();
      if (resp.ok) {
        const data = await resp.json();
        for (const f of data.favorites || []) ids.add(f.paper.id);
        notify();
        return data.favorites || [];
      }
      notify();
      return [];
    } catch {
      return [];
    }
  }

  async function toggle(paperId) {
    if (window.PaperSession && !PaperSession.user()) return goToLogin();
    const add = !ids.has(paperId);
    const resp = await fetch(`/favorites/${encodeURI(paperId)}`, { method: add ? "PUT" : "DELETE" });
    if (resp.status === 401) return goToLogin();  // session expirée
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(typeof data.detail === "string" ? data.detail : `Erreur du serveur (${resp.status}).`);
    }
    if (add) ids.add(paperId); else ids.delete(paperId);
    notify();
  }

  function update(button) {
    const on = ids.has(button.dataset.paperId);
    const loggedOut = window.PaperSession && !PaperSession.user();
    const label = loggedOut ? "Se connecter pour ajouter aux favoris" : on ? "Retirer des favoris" : "Ajouter aux favoris";
    button.setAttribute("aria-pressed", String(on));
    button.textContent = on ? "★" : "☆";
    button.title = label;
    button.setAttribute("aria-label", `${label} : ${button.dataset.title}`);
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

  // Les libellés des étoiles dépendent de la connexion : mise à jour une fois connue.
  if (window.PaperSession) PaperSession.ready.then(notify);

  window.PaperFavorites = {
    load,
    toggle,
    button,
    has: (id) => ids.has(id),
    count: () => ids.size,
    onChange: (fn) => listeners.add(fn),
  };
})();
