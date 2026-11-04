// Compte connecté : barre « Se connecter / Se déconnecter » (élément #user-bar) et
// adresse de la page de connexion qui ramène ensuite à la page courante.
(function () {
  "use strict";

  let state = { user: null, allow_signup: false };

  function loginUrl(mode) {
    const params = new URLSearchParams({ next: location.pathname + location.search + location.hash });
    if (mode) params.set("mode", mode);
    return `/login?${params}`;
  }

  async function logout() {
    try {
      await fetch("/auth/logout", { method: "POST" });
    } finally {
      location.reload();
    }
  }

  function render() {
    const bar = document.getElementById("user-bar");
    if (!bar) return;
    bar.replaceChildren();
    if (state.user) {
      const name = document.createElement("span");
      name.className = "user-name";
      name.textContent = state.user.username;
      name.title = "Compte connecté";
      const out = document.createElement("button");
      out.type = "button";
      out.className = "user-link";
      out.textContent = "Se déconnecter";
      out.addEventListener("click", logout);
      bar.append(name, out);
    } else {
      const login = document.createElement("a");
      login.className = "user-link";
      login.href = loginUrl();
      login.textContent = "Se connecter";
      bar.append(login);
      if (state.allow_signup) {
        const signup = document.createElement("a");
        signup.className = "user-link";
        signup.href = loginUrl("signup");
        signup.textContent = "Créer un compte";
        bar.append(signup);
      }
    }
  }

  const ready = fetch("/auth/me")
    .then((r) => (r.ok ? r.json() : state))
    .catch(() => state)
    .then((s) => {
      state = s;
      render();
      return s;
    });

  window.PaperSession = { ready, loginUrl, user: () => state.user };
})();
