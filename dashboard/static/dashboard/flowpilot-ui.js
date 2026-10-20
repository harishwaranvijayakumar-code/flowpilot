
/* FlowPilot UI interactions.
   Progressive enhancement; Django routes and forms remain unchanged. */
(() => {
  "use strict";

  const root = document.documentElement;
  const body = document.body;
  const themeKey = "flowpilot-theme";
  const sidebarKey = "flowpilot-sidebar-collapsed";
  const mobileBreakpoint = 760;

  const safeStorage = {
    get(key) {
      try {
        return window.localStorage.getItem(key);
      } catch {
        return null;
      }
    },

    set(key, value) {
      try {
        window.localStorage.setItem(key, value);
      } catch {
        // The UI remains usable when storage is unavailable.
      }
    }
  };

  const makeIcon = (name) => {
    const paths = {
      sun: `
        <circle cx="12" cy="12" r="4"></circle>
        <path d="M12 2v2m0 16v2M4.93 4.93l1.42 1.42
          m11.3 11.3 1.42 1.42M2 12h2m16 0h2
          M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42"></path>
      `,

      moon: `
        <path d="M20.7 13.1A8.5 8.5 0 0 1 10.9 3.3
          8.5 8.5 0 1 0 20.7 13.1Z"></path>
      `,

      spectrum: `
        <circle cx="12" cy="12" r="3"></circle>
        <path d="M12 2v3m0 14v3M2 12h3m14 0h3
          M4.93 4.93l2.12 2.12m9.9 9.9 2.12 2.12
          M19.07 4.93l-2.12 2.12m-9.9 9.9-2.12 2.12"></path>
      `,

      menu: `
        <path d="M4 7h16M4 12h16M4 17h16"></path>
      `,

      close: `
        <path d="m6 6 12 12M18 6 6 18"></path>
      `,

      collapse: `
        <path d="m14 5-7 7 7 7"></path>
      `,

      expand: `
        <path d="m10 5 7 7-7 7"></path>
      `
    };

    const svg = document.createElementNS(
      "http://www.w3.org/2000/svg",
      "svg"
    );

    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", "18");
    svg.setAttribute("height", "18");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.7");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = paths[name] || "";

    return svg;
  };

  /* -------------------------------------------------------
     Theme
     ------------------------------------------------------- */

  const validThemes = ["light", "dark", "spectrum"];

  const themeConfig = {
    light: {
      label: "Light",
      next: "dark",
      icon: "sun"
    },

    dark: {
      label: "Dark",
      next: "spectrum",
      icon: "moon"
    },

    spectrum: {
      label: "Spectrum",
      next: "light",
      icon: "spectrum"
    }
  };

  const systemPrefersDark = Boolean(
    window.matchMedia &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
  );

  const storedTheme = safeStorage.get(themeKey);

  const initialTheme = validThemes.includes(storedTheme)
    ? storedTheme
    : systemPrefersDark
      ? "dark"
      : "light";

  const applyTheme = (theme) => {
    const activeTheme = validThemes.includes(theme)
      ? theme
      : "light";

    const config = themeConfig[activeTheme];

    root.dataset.theme = activeTheme;
    safeStorage.set(themeKey, activeTheme);

    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.replaceChildren(makeIcon(config.icon));

      const label = document.createElement("span");
      label.className = "theme-toggle-label";
      label.textContent = config.label;
      button.appendChild(label);

      const nextLabel = themeConfig[config.next].label;
      const accessibleLabel =
        `Current theme: ${config.label}. Switch to ${nextLabel} theme`;

      button.setAttribute("aria-label", accessibleLabel);
      button.setAttribute("title", accessibleLabel);
      button.setAttribute("aria-pressed", "true");
      button.dataset.activeTheme = activeTheme;
    });
  };

  // Apply the saved theme before adding page controls.
  root.dataset.theme = initialTheme;

  const createThemeButton = () => {
    const button = document.createElement("button");

    button.type = "button";
    button.className = "theme-toggle";
    button.dataset.themeToggle = "";

    button.addEventListener("click", () => {
      const currentTheme = validThemes.includes(root.dataset.theme)
        ? root.dataset.theme
        : "light";

      applyTheme(themeConfig[currentTheme].next);
    });

    return button;
  };

  /* -------------------------------------------------------
     Application navigation
     ------------------------------------------------------- */

  const sidebar = document.querySelector(".sidebar");
  const nav = sidebar?.querySelector(".main-nav");
  const topbar = document.querySelector(".topbar");

  if (sidebar && nav && topbar) {
    const actions = document.createElement("div");
    actions.className = "shell-actions";

    const menuButton = document.createElement("button");
    menuButton.type = "button";
    menuButton.className = "menu-toggle";
    menuButton.setAttribute("aria-controls", "flowpilot-main-nav");
    menuButton.setAttribute("aria-expanded", "false");
    menuButton.setAttribute("aria-label", "Collapse sidebar");
    menuButton.title = "Collapse sidebar";
    menuButton.appendChild(makeIcon("collapse"));

    nav.id = nav.id || "flowpilot-main-nav";

    const backdrop = document.createElement("button");
    backdrop.type = "button";
    backdrop.className = "mobile-nav-backdrop";
    backdrop.setAttribute("aria-label", "Close navigation");
    backdrop.tabIndex = -1;
    document.body.appendChild(backdrop);

    const isMobile = () => window.innerWidth <= mobileBreakpoint;

    const setDesktopCollapsed = (collapsed) => {
      body.classList.toggle("sidebar-collapsed", collapsed);
      safeStorage.set(sidebarKey, String(collapsed));

      menuButton.setAttribute("aria-expanded", String(!collapsed));
      menuButton.setAttribute(
        "aria-label",
        collapsed ? "Expand sidebar" : "Collapse sidebar"
      );
      menuButton.title = collapsed ? "Expand sidebar" : "Collapse sidebar";

      menuButton.replaceChildren(
        makeIcon(collapsed ? "expand" : "collapse")
      );
    };

    const closeMobileNav = ({ restoreFocus = false } = {}) => {
      nav.classList.remove("is-open");
      backdrop.classList.remove("is-visible");
      menuButton.setAttribute("aria-expanded", "false");
      menuButton.setAttribute("aria-label", "Open navigation");
      menuButton.title = "Open navigation";
      menuButton.replaceChildren(makeIcon("menu"));
      body.classList.remove("nav-open");

      if (restoreFocus) {
        menuButton.focus();
      }
    };

    const openMobileNav = () => {
      nav.classList.add("is-open");
      backdrop.classList.add("is-visible");
      menuButton.setAttribute("aria-expanded", "true");
      menuButton.setAttribute("aria-label", "Close navigation");
      menuButton.title = "Close navigation";
      menuButton.replaceChildren(makeIcon("close"));
      body.classList.add("nav-open");
    };

    const storedCollapsed = safeStorage.get(sidebarKey) === "true";
    setDesktopCollapsed(storedCollapsed);

    menuButton.addEventListener("click", () => {
      if (isMobile()) {
        if (nav.classList.contains("is-open")) {
          closeMobileNav();
        } else {
          openMobileNav();
        }

        return;
      }

      setDesktopCollapsed(
        !body.classList.contains("sidebar-collapsed")
      );
    });

    backdrop.addEventListener("click", () => closeMobileNav());

    nav.addEventListener("click", (event) => {
      if (event.target.closest("a")) {
        closeMobileNav();
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && nav.classList.contains("is-open")) {
        closeMobileNav({ restoreFocus: true });
      }
    });

    window.addEventListener("resize", () => {
      if (!isMobile()) {
        closeMobileNav();
        setDesktopCollapsed(
          body.classList.contains("sidebar-collapsed")
        );
      }
    });

    actions.appendChild(menuButton);
    actions.appendChild(createThemeButton());

    const connection = topbar.querySelector(".connection-label");

    if (connection) {
      topbar.insertBefore(actions, connection);
    } else {
      topbar.appendChild(actions);
    }
  } else {
    /*
     * Authentication pages have no workspace topbar.
     * Add a theme control without changing their forms.
     */
    const authPage = document.querySelector(".auth-page");

    if (authPage) {
      const themeButton = createThemeButton();

      themeButton.style.position = "fixed";
      themeButton.style.top = "18px";
      themeButton.style.right = "18px";
      themeButton.style.zIndex = "50";

      authPage.appendChild(themeButton);
    }
  }

  // Refresh every theme control after it has been created.
  applyTheme(initialTheme);

  /* -------------------------------------------------------
     Current-page navigation and optional interactions
     ------------------------------------------------------- */

  const currentPath =
    window.location.pathname.replace(/\/+$/, "") || "/";

  document.querySelectorAll(".main-nav a[href]").forEach((link) => {
    try {
      const linkPath =
        new URL(link.href, window.location.origin)
          .pathname.replace(/\/+$/, "") || "/";

      if (linkPath === currentPath) {
        link.setAttribute("aria-current", "page");
      }
    } catch {
      // Ignore malformed links without breaking navigation.
    }
  });

  /* Keep in-page anchor scrolling smooth where supported. */
  document.querySelectorAll('a[href^="#"]').forEach((link) => {
    link.addEventListener("click", (event) => {
      const id = link.getAttribute("href").slice(1);
      const target = id ? document.getElementById(id) : null;

      if (!target) {
        return;
      }

      event.preventDefault();

      const prefersReducedMotion = window.matchMedia(
        "(prefers-reduced-motion: reduce)"
      ).matches;

      target.scrollIntoView({
        behavior: prefersReducedMotion ? "auto" : "smooth",
        block: "start"
      });

      if (window.history.replaceState) {
        window.history.replaceState(
          null,
          "",
          `#${encodeURIComponent(id)}`
        );
      }
    });
  });
})();
