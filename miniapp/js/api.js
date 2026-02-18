/**
 * API client for Incognitus WebApp backend.
 */

const API_BASE = "https://lazez.uz";

async function fetchLink(token) {
  const res = await fetch(`${API_BASE}/api/link/${encodeURIComponent(token)}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Link not found");
  }
  return res.json();
}

async function sendMessage(token, text) {
  const res = await fetch(`${API_BASE}/api/send`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, text }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to send");
  }
  return res.json();
}

async function fetchDashboard(initData) {
  const res = await fetch(`${API_BASE}/api/dashboard`, {
    headers: { "X-Init-Data": initData },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to load dashboard");
  }
  return res.json();
}

async function fetchProfile(profileToken) {
  const res = await fetch(`${API_BASE}/api/profile/${encodeURIComponent(profileToken)}`);
  if (res.status === 404) {
    return { not_found: true };
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to load profile");
  }
  return res.json();
}

async function updateProfileSettings(initData, settings) {
  const res = await fetch(`${API_BASE}/api/profile/settings`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Init-Data": initData,
    },
    body: JSON.stringify(settings),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to update settings");
  }
  return res.json();
}

