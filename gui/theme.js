/* Theme selection is shared by every pywebview surface. */
(function () {
  const THEMES = new Set([
    "ender-observatory",
    "overworld-workshop",
    "nether-relay",
  ]);

  function applyTheme(value) {
    const theme = THEMES.has(value) ? value : "ender-observatory";
    document.documentElement.dataset.theme = theme;
    return theme;
  }

  window.AutoRewarderTheme = { applyTheme };
})();
