const menuButton = document.querySelector("#menuButton");
const mobileMenu = document.querySelector("#mobileMenu");
const themeButtons = [
    document.querySelector("#themeToggle"),
    document.querySelector(".theme-toggle-mobile"),
].filter(Boolean);

function setMobileMenu(open) {
    if (!menuButton || !mobileMenu) return;
    mobileMenu.classList.toggle("hidden", !open);
    menuButton.setAttribute("aria-expanded", String(open));
}

menuButton?.addEventListener("click", () => {
    setMobileMenu(mobileMenu?.classList.contains("hidden"));
});

mobileMenu?.querySelectorAll("a, form button").forEach((control) => {
    control.addEventListener("click", () => setMobileMenu(false));
});

document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (mobileMenu && !mobileMenu.classList.contains("hidden")) {
        setMobileMenu(false);
        menuButton?.focus();
    }
    document.querySelectorAll(".account-menu[open]").forEach((menu) => {
        menu.removeAttribute("open");
    });
});

document.addEventListener("click", (event) => {
    document.querySelectorAll(".account-menu[open]").forEach((menu) => {
        if (!menu.contains(event.target)) menu.removeAttribute("open");
    });
});

function syncThemeIcons() {
    const dark = document.documentElement.classList.contains("dark");
    document.querySelector(".theme-sun")?.classList.toggle("hidden", dark);
    document.querySelector(".theme-moon")?.classList.toggle("hidden", !dark);
}

function toggleTheme() {
    const dark = document.documentElement.classList.toggle("dark");
    localStorage.setItem("sham-theme", dark ? "dark" : "light");
    syncThemeIcons();
}

themeButtons.forEach((button) => button.addEventListener("click", toggleTheme));
syncThemeIcons();
