// Adds a small "Made by Nimble" mark to the site footer.
(() => {
  if (window.webrouterNimbleMark) return;
  window.webrouterNimbleMark = true;

  const add = () => {
    const footer = document.querySelector('[data-layout-item="footer"], #footer');
    if (!footer || footer.querySelector(".nimble-mark")) return;
    const link = document.createElement("a");
    link.className = "nimble-mark";
    link.href = "https://nimbleway.com";
    link.target = "_blank";
    link.rel = "noopener";
    link.title = "Made by Nimble";
    link.setAttribute("aria-label", "Made by Nimble");
    link.innerHTML = '<span class="nimble-mark-icon" aria-hidden="true"></span><span class="nimble-mark-text">Made by Nimble</span>';
    footer.append(link);
  };

  add();
  window.addEventListener("mintlify:navigate", add);
  new MutationObserver(add).observe(document.body, { childList: true, subtree: true });
})();
