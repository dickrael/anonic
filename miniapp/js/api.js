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

async function uploadAvatar(initData, file) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/api/avatar`, {
    method: "POST",
    headers: { "X-Init-Data": initData },
    body: form,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Upload failed");
  }
  return res.json();
}

async function deleteAvatar(initData) {
  const res = await fetch(`${API_BASE}/api/avatar`, {
    method: "DELETE",
    headers: { "X-Init-Data": initData },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Delete failed");
  }
  return res.json();
}
