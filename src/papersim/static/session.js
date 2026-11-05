// Compte connecté : onglet « Compte » collé au bord droit de la fenêtre (il glisse au survol
// et montre « Se connecter / Créer un compte » ou « Se déconnecter »), et panneau coulissant
// de connexion / création de compte, sans quitter la page.
// La page /login reste disponible en accès direct.
(function () {
  "use strict";

  let state = { user: null, allow_signup: false };
  const listeners = new Set();

  function loginUrl(mode) {
    const params = new URLSearchParams({ next: location.pathname + location.search + location.hash });
    if (mode) params.set("mode", mode);
    return `/login?${params}`;
  }

  function setState(next) {
    state = { ...state, ...next };
    render();
    listeners.forEach((fn) => fn(state.user));
  }

  async function logout() {
    try {
      await fetch("/auth/logout", { method: "POST" });
    } catch { /* déconnexion locale malgré tout */ }
    setState({ user: null });
  }

  // --- Onglet « Compte » du bord droit -------------------------------------------------------
  // S'ouvre au survol de la souris, au focus clavier (Tab), ou d'un appui (écran tactile).

  let edge = null;

  function buildEdge() {
    const handle = h("button", { type: "button", className: "account-handle" });
    handle.setAttribute("aria-controls", "account-panel");
    handle.setAttribute("aria-expanded", "false");
    const panel = h("div", { className: "account-panel", id: "account-panel" });
    panel.setAttribute("role", "region");
    panel.setAttribute("aria-label", "Compte");
    const root = h("div", { className: "account-edge" }, handle, panel);
    document.body.prepend(root);  // en tête de page : atteint dès la première pression sur Tab

    const pinned = () => root.classList.contains("open");
    const setExpanded = (on) => handle.setAttribute("aria-expanded", String(on));
    root.addEventListener("mouseenter", () => setExpanded(true));
    root.addEventListener("mouseleave", () => setExpanded(pinned()));
    root.addEventListener("focusin", () => setExpanded(true));
    root.addEventListener("focusout", (e) => { if (!root.contains(e.relatedTarget)) setExpanded(pinned()); });
    // Appui ou clic sur l'onglet : ouvert jusqu'au prochain appui (ou un appui ailleurs).
    handle.addEventListener("click", () => setExpanded(root.classList.toggle("open")));
    document.addEventListener("click", (e) => { if (pinned() && !root.contains(e.target)) collapseEdge(); });
    root.addEventListener("keydown", (e) => { if (e.key === "Escape") collapseEdge(); });
    edge = { root, handle, panel };
  }

  function collapseEdge() {
    if (!edge) return;
    edge.root.classList.remove("open");
    edge.handle.setAttribute("aria-expanded", "false");
    if (edge.root.contains(document.activeElement)) document.activeElement.blur();
  }

  function actionButton(text, onClick, secondary = false) {
    const b = h("button", { type: "button", className: "account-btn" + (secondary ? " secondary" : ""), textContent: text });
    b.addEventListener("click", onClick);
    return b;
  }

  function render() {
    if (!edge) buildEdge();
    const { handle, panel } = edge;
    panel.replaceChildren();
    if (state.user) {
      const name = state.user.username;
      handle.textContent = name;
      handle.classList.add("signed-in");
      handle.title = `Compte : ${name}`;
      panel.append(
        h("p", {}, "Connecté en tant que ", h("strong", { textContent: name })),
        actionButton("Se déconnecter", () => { collapseEdge(); logout(); }, true),
      );
    } else {
      handle.textContent = "Compte";
      handle.classList.remove("signed-in");
      handle.title = "Se connecter ou créer un compte";
      panel.append(
        h("p", { textContent: "Connectez-vous pour enregistrer vos favoris." }),
        actionButton("Se connecter", () => openLogin("login", { trigger: handle })),
      );
      if (state.allow_signup) {
        panel.append(actionButton("Créer un compte", () => openLogin("signup", { trigger: handle }), true));
      }
    }
  }

  // --- Panneau coulissant ------------------------------------------------------------------

  let drawer = null;   // éléments du panneau, créés à la première ouverture
  let mode = "login";
  let opened = null;   // { trigger, onSuccess } de l'ouverture en cours

  function h(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    Object.assign(node, props);
    for (const c of children) if (c != null) node.append(c);
    return node;
  }

  function build() {
    const backdrop = h("div", { className: "drawer-backdrop", hidden: true });
    const title = h("h2", { id: "drawer-title" });
    const close = h("button", { type: "button", className: "drawer-close", textContent: "×", title: "Fermer" });
    close.setAttribute("aria-label", "Fermer");
    const lead = h("p", { className: "drawer-lead" });
    const error = h("div", { className: "drawer-error", role: "alert", hidden: true });
    const username = h("input", { name: "username", autocomplete: "username", required: true, maxLength: 32, spellcheck: false });
    username.setAttribute("autocapitalize", "off");
    const password = h("input", { name: "password", type: "password", required: true, maxLength: 256 });
    const confirm = h("input", { name: "confirm", type: "password", autocomplete: "new-password", maxLength: 256 });
    const hint = h("small", { textContent: "8 caractères au moins" });
    const confirmRow = h("label", {}, "Confirmer le mot de passe", confirm);
    const submit = h("button", { type: "submit", className: "drawer-submit" });
    const form = h("form", { noValidate: true }, error,
      h("label", {}, "Identifiant", username),
      h("label", {}, "Mot de passe ", hint, password),
      confirmRow, submit);
    const switcher = h("p", { className: "drawer-switch" });
    const panel = h("aside", { className: "drawer", hidden: true },
      h("div", { className: "drawer-head" }, title, close), lead, form, switcher);
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-labelledby", "drawer-title");
    document.body.append(backdrop, panel);

    backdrop.addEventListener("click", closeLogin);
    close.addEventListener("click", closeLogin);
    panel.addEventListener("keydown", onKeydown);
    form.addEventListener("submit", onSubmit);
    drawer = { backdrop, panel, title, lead, error, username, password, confirm, confirmRow, hint, submit, switcher };
  }

  function setMode(m) {
    mode = m === "signup" && state.allow_signup ? "signup" : "login";
    const signup = mode === "signup";
    const d = drawer;
    d.title.textContent = signup ? "Créer un compte" : "Se connecter";
    d.lead.textContent = signup
      ? "Un compte garde vos favoris, séparés de ceux des autres utilisateurs."
      : "Connectez-vous pour retrouver vos favoris.";
    d.submit.textContent = signup ? "Créer le compte" : "Se connecter";
    d.password.autocomplete = signup ? "new-password" : "current-password";
    d.confirmRow.hidden = !signup;
    d.hint.hidden = !signup;
    d.error.hidden = true;
    d.switcher.replaceChildren();
    if (!state.allow_signup) {
      d.switcher.textContent = "Pas de compte ? Demandez-en un à l'administrateur.";
      return;
    }
    const toggle = h("button", { type: "button", textContent: signup ? "Se connecter" : "Créer un compte" });
    toggle.addEventListener("click", () => { setMode(signup ? "login" : "signup"); d.username.focus(); });
    d.switcher.append(signup ? "Déjà un compte ? " : "Pas encore de compte ? ", toggle);
  }

  function openLogin(m = "login", { trigger = null, onSuccess = null } = {}) {
    if (!drawer) build();
    collapseEdge();
    opened = { trigger: trigger || document.activeElement, onSuccess };
    setMode(m);
    drawer.password.value = drawer.confirm.value = "";
    drawer.backdrop.hidden = drawer.panel.hidden = false;
    document.body.classList.add("drawer-open");
    // Lecture d'une dimension = mise en page immédiate : la transition d'entrée part bien de
    // la position « hors écran » (sans dépendre des images d'animation du navigateur).
    void drawer.panel.offsetWidth;
    drawer.backdrop.classList.add("open");
    drawer.panel.classList.add("open");
    drawer.username.focus();
  }

  function closeLogin() {
    if (!drawer || drawer.panel.hidden) return;
    drawer.backdrop.classList.remove("open");
    drawer.panel.classList.remove("open");
    document.body.classList.remove("drawer-open");
    const done = () => { drawer.backdrop.hidden = drawer.panel.hidden = true; };
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) done();
    else setTimeout(done, 260);
    const trigger = opened && opened.trigger;
    opened = null;
    if (trigger && document.contains(trigger)) trigger.focus();
  }

  // Échap ferme ; Tab reste à l'intérieur du panneau.
  function onKeydown(e) {
    if (e.key === "Escape") { e.preventDefault(); closeLogin(); return; }
    if (e.key !== "Tab") return;
    const items = [...drawer.panel.querySelectorAll("button, input, a[href]")]
      .filter((el) => !el.disabled && el.offsetParent !== null);
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  function fail(message, field) {
    drawer.error.textContent = message;
    drawer.error.hidden = false;
    if (field) field.focus();
  }

  async function onSubmit(e) {
    e.preventDefault();
    const d = drawer;
    const username = d.username.value.trim();
    const password = d.password.value;
    if (!username) return fail("Indiquez votre identifiant.", d.username);
    if (!password) return fail("Indiquez votre mot de passe.", d.password);
    if (mode === "signup") {
      if (!/^[A-Za-z0-9._-]{3,32}$/.test(username)) {
        return fail("Identifiant : 3 à 32 caractères parmi lettres, chiffres, « . », « _ » et « - ».", d.username);
      }
      if (password.length < 8) return fail("Mot de passe trop court : 8 caractères au moins.", d.password);
      if (password !== d.confirm.value) return fail("Les deux mots de passe ne correspondent pas.", d.confirm);
    }
    d.submit.disabled = true;
    try {
      const resp = await fetch(mode === "signup" ? "/auth/signup" : "/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = typeof data.detail === "string" ? data.detail : `Erreur du serveur (${resp.status}).`;
        return fail(detail, resp.status === 409 ? d.username : d.password);
      }
      const onSuccess = opened && opened.onSuccess;
      d.password.value = d.confirm.value = "";
      closeLogin();
      setState({ user: data.user });
      if (onSuccess) onSuccess();
    } catch {
      fail("Le serveur ne répond pas. Est-il lancé (papersim serve) ?");
    } finally {
      d.submit.disabled = false;
    }
  }

  // --- Démarrage ---------------------------------------------------------------------------

  const ready = fetch("/auth/me")
    .then((r) => (r.ok ? r.json() : state))
    .catch(() => state)
    .then((s) => {
      state = s;
      render();
      return s;
    });

  window.PaperSession = {
    ready,
    loginUrl,
    openLogin,
    closeLogin,
    user: () => state.user,
    // Appelé à chaque connexion / déconnexion (avec le compte, ou null).
    onChange: (fn) => listeners.add(fn),
  };
})();
