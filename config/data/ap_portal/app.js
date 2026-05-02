const form = document.querySelector("#consent-form");
const statusEl = document.querySelector("#status");
const sourceLink = document.querySelector("#source-link");

function redirectAfterDelay(url, delayMs) {
  if (!url) {
    return;
  }
  window.setTimeout(() => {
    window.location.href = url;
  }, delayMs ?? 3000);
}

async function loadStatus() {
  const response = await fetch("/api/status", { cache: "no-store" });
  if (!response.ok) {
    throw new Error("Unable to load AP status.");
  }
  const payload = await response.json();
  if (payload.source_code_url) {
    sourceLink.href = payload.source_code_url;
  }
  if (payload.authorized) {
    statusEl.textContent = "This device is already authorized. Opening gallery.";
    form.hidden = true;
    redirectAfterDelay(payload.authorized_redirect_url, payload.redirect_delay_ms);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusEl.textContent = "";
  const button = form.querySelector("button");
  button.disabled = true;

  try {
    const payload = {
      email: form.email.value,
      accept_terms: form.accept_terms.checked,
    };
    const response = await fetch("/api/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "Unable to authorize this device.");
    }
    statusEl.textContent = "Access recorded. Opening gallery.";
    redirectAfterDelay(result.redirect_url || "/", result.redirect_delay_ms);
  } catch (error) {
    statusEl.textContent = error.message;
    button.disabled = false;
  }
});

loadStatus().catch((error) => {
  statusEl.textContent = error.message;
});
