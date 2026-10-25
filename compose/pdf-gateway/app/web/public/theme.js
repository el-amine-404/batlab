// Runs before the page draws, so a dark system never flashes white. Follows the
// system unless the person picked Light or Dark (the only thing kept locally).
(function () {
  var choice = null;
  try { choice = localStorage.getItem("pgw-theme"); } catch (error) { /* storage blocked */ }
  var dark = choice === "dark" || (choice !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
})();
