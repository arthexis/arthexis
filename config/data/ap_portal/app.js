const form = document.getElementById("portal-form");
const accessLinksNode = document.getElementById("access-links");
const googleLink = document.getElementById("google-link");
const googleTarget = document.getElementById("google-target");
const localArthexisLink = document.getElementById("local-arthexis-link");
const localArthexisTarget = document.getElementById("local-arthexis-target");
const emailInput = document.getElementById("email");
const existingUserInput = document.getElementById("existing-user");
const submitButton = document.getElementById("submit-button");
const statusNode = document.getElementById("status");

function setStatus(message, kind = "") {
  statusNode.textContent = message;
  statusNode.className = `status ${kind}`.trim();
}

function scheduleRedirect(url, delayMs) {
  if (!url) {
    return false;
  }
  window.setTimeout(() => {
    window.location.href = url;
  }, delayMs);
  return true;
}

function localArthexisUrl() {
  const host = window.location.hostname || "10.42.0.1";
  return `http://${host}:8000/`;
}

function suiteLoginUrl(username) {
  if (!username) {
    return "";
  }
  const url = new URL("/login/", localArthexisUrl());
  url.searchParams.set("username", username);
  url.searchParams.set("next", "/");
  return url.toString();
}

function showAccessLinks() {
  form.hidden = true;
  accessLinksNode.hidden = false;
  googleLink.href = "https://google.com/";
  googleTarget.textContent = "https://google.com/";
  localArthexisLink.href = localArthexisUrl();
  localArthexisTarget.textContent = localArthexisUrl();
}

function handleAuthorizedFlow(payload, submitDelayMs = 0) {
  if (scheduleRedirect(suiteLoginUrl(payload.suite_username), submitDelayMs || 900)) {
    form.hidden = true;
    setStatus(
      "This device is linked to an existing Arthexis user. Opening the suite login now.",
      "success",
    );
    return;
  }
  if (scheduleRedirect(payload.redirect_url, submitDelayMs || 1200)) {
    form.hidden = true;
    setStatus(
      "This device already has access. Opening the configured page now.",
      "success",
    );
    return;
  }
  showAccessLinks();
  setStatus(
    "This device already has access. Choose a destination below.",
    "success",
  );
}

async function loadStatus() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    if (payload.authorized) {
      handleAuthorizedFlow(payload);
    }
  } catch (error) {
    console.error(error);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  submitButton.disabled = true;
  setStatus("Granting access...");

  const payload = {
    email: emailInput.value,
    existing_user: existingUserInput.value,
    accept_terms: document.getElementById("accept_terms").checked,
  };

  try {
    const response = await fetch("/api/subscribe", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Access could not be granted.");
    }
    if (data.suite_username) {
      handleAuthorizedFlow(data, 350);
    } else if (scheduleRedirect(data.redirect_url, 1400)) {
      form.hidden = true;
      setStatus("Access granted. Opening the configured page now.", "success");
    } else {
      showAccessLinks();
      setStatus(
        "Access granted. Choose where to go next.",
        "success",
      );
    }
  } catch (error) {
    setStatus(error.message, "error");
    submitButton.disabled = false;
  }
});

loadStatus();
