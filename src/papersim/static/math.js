// Rendu des formules LaTeX ($…$, $$…$$, \(…\)) des titres et résumés avec KaTeX.
// KaTeX est chargé depuis un CDN : sans connexion, le texte brut reste affiché.
(function () {
  "use strict";

  const OPTIONS = {
    delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "\\(", right: "\\)", display: false },
      { left: "$", right: "$", display: false },
    ],
    throwOnError: false, // une formule invalide reste en texte au lieu de casser la page
    strict: "ignore",    // accepte les caractères Unicode (φ, ∼…) fréquents dans les abstracts
    ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code", "option", "button", "input"],
  };

  function render(element) {
    if (!element) return;
    if (typeof window.renderMathInElement !== "function") {
      // KaTeX pas encore chargé : on réessaie dès qu'il l'est.
      window.addEventListener("load", () => render(element), { once: true });
      return;
    }
    try {
      window.renderMathInElement(element, OPTIONS);
    } catch (err) {
      console.warn("Rendu des formules impossible :", err);
    }
  }

  window.PaperMath = { render };
})();
