/* Accessible mobile navigation and local password control. Introduced 2026-11-18; revisions in Git. */
const opener = document.querySelector("[data-menu]");
if (opener) {
  document.documentElement.classList.add("enhanced");
  const toggle = (open) => opener.setAttribute("aria-expanded", String(open));
  opener.addEventListener("click", () =>
    toggle(opener.getAttribute("aria-expanded") !== "true"),
  );
  document.addEventListener("keydown", (event) => {
    if (
      event.key === "Escape" &&
      opener.getAttribute("aria-expanded") === "true"
    ) {
      toggle(false);
      opener.focus();
    }
  });
}
for (const button of document.querySelectorAll("[data-password]")) {
  button.addEventListener("click", () => {
    const input = document.getElementById(button.dataset.password);
    if (!input) return;
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    button.setAttribute("aria-pressed", String(show));
  });
}
