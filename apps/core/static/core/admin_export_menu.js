(() => {
  const menuSelector = ".export-menu";
  const triggerSelector = ".export-menu__trigger";

  const closeAllMenus = (except = null) => {
    document.querySelectorAll(menuSelector).forEach((menu) => {
      if (menu === except) {
        return;
      }
      menu.classList.remove("is-open");
      const trigger = menu.querySelector(triggerSelector);
      if (trigger) {
        trigger.setAttribute("aria-expanded", "false");
      }
    });
  };

  const toggleMenu = (menu) => {
    const isOpen = menu.classList.toggle("is-open");
    const trigger = menu.querySelector(triggerSelector);
    if (trigger) {
      trigger.setAttribute("aria-expanded", String(isOpen));
    }
    if (isOpen) {
      closeAllMenus(menu);
    }
  };

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest(triggerSelector);
    if (trigger) {
      const menu = trigger.closest(menuSelector);
      if (menu) {
        event.preventDefault();
        toggleMenu(menu);
      }
      return;
    }

    if (!event.target.closest(menuSelector)) {
      closeAllMenus();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeAllMenus();
    }
  });
})();
