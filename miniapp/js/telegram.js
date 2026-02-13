/**
 * Telegram WebApp SDK helper.
 * Works inside Telegram and gracefully falls back in a regular browser.
 */

const TG = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

/** Initialize the WebApp (call early). */
function initWebApp() {
  if (!TG) return;
  TG.ready();
  TG.expand();
}

/** Get initData string for authenticated requests. */
function getInitData() {
  return TG ? TG.initData : "";
}

/** Get current user id (0 if unavailable). */
function getUserId() {
  if (TG && TG.initDataUnsafe && TG.initDataUnsafe.user) {
    return TG.initDataUnsafe.user.id;
  }
  return 0;
}

/** Trigger haptic feedback if available. */
function hapticFeedback(type) {
  if (!TG || !TG.HapticFeedback) return;
  switch (type) {
    case "success":
      TG.HapticFeedback.notificationOccurred("success");
      break;
    case "error":
      TG.HapticFeedback.notificationOccurred("error");
      break;
    case "impact":
      TG.HapticFeedback.impactOccurred("light");
      break;
    default:
      TG.HapticFeedback.selectionChanged();
  }
}

/** Close the mini app. */
function closeMiniApp() {
  if (TG) TG.close();
}

/** Show back button with callback. */
function showBackButton(callback) {
  if (!TG || !TG.BackButton) return;
  TG.BackButton.show();
  TG.BackButton.onClick(callback);
}

/** Hide back button. */
function hideBackButton() {
  if (!TG || !TG.BackButton) return;
  TG.BackButton.hide();
}

/** Check if running inside Telegram. */
function isInsideTelegram() {
  return !!TG;
}
