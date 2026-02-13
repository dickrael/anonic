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

async function fetchInbox(initData, offset = 0) {
  const url = new URL(`${API_BASE}/api/inbox`);
  if (offset > 0) url.searchParams.set("offset", offset);

  const res = await fetch(url, {
    headers: { "X-Init-Data": initData },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to load inbox");
  }
  return res.json();
}

async function markRead(messageId, initData) {
  const res = await fetch(`${API_BASE}/api/inbox/read/${messageId}`, {
    method: "POST",
    headers: { "X-Init-Data": initData },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to mark read");
  }
  return res.json();
}
