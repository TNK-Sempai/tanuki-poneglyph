/* Déconnexion. Le cookie de session est HttpOnly : seul le serveur peut
   l'effacer, d'où l'appel à /api/logout plutôt qu'un nettoyage côté client. */
document.addEventListener("DOMContentLoaded", () => {
  const b = document.getElementById("logout");
  if (!b) return;
  b.onclick = async () => {
    b.disabled = true;
    try { await fetch("/api/logout", {method: "POST"}); } catch {}
    location.replace("/dashboard/login.html");
  };
});
