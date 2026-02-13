/**
 * Telegram WebApp SDK helper.
 * Works inside Telegram and gracefully falls back in a regular browser.
 */

var TG = null;

/** Initialize the WebApp (call early). */
function initWebApp() {
  if (window.Telegram && window.Telegram.WebApp) {
    TG = window.Telegram.WebApp;
    TG.ready();
    TG.expand();
    console.log("[TG] WebApp initialized, platform:", TG.platform);
    console.log("[TG] initData length:", (TG.initData || "").length);
    console.log("[TG] initDataUnsafe:", JSON.stringify(TG.initDataUnsafe || {}));
    console.log("[TG] version:", TG.version);
  } else {
    console.log("[TG] Not inside Telegram WebApp");
  }
}

/** Get initData string for authenticated requests. */
function getInitData() {
  if (!TG) return "";
  // initData is the raw query string Telegram injects
  if (TG.initData && TG.initData.length > 0) {
    return TG.initData;
  }
  // Some Telegram clients pass data via hash fragment
  if (window.location.hash && window.location.hash.length > 1) {
    var hashData = window.location.hash.substring(1);
    // Check if it looks like Telegram initData (contains hash= param)
    if (hashData.indexOf("hash=") !== -1 || hashData.indexOf("user=") !== -1) {
      console.log("[TG] Using hash fragment as initData");
      return hashData;
    }
  }
  return "";
}

/** Check if we're inside Telegram (even if initData is empty). */
function isInsideTelegram() {
  if (!TG) return false;
  // If initDataUnsafe has a user, we're definitely inside Telegram
  if (TG.initDataUnsafe && TG.initDataUnsafe.user) return true;
  // If platform is set and not "unknown", we're inside Telegram
  if (TG.platform && TG.platform !== "unknown") return true;
  return !!TG.initData;
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
  try {
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
  } catch (e) {}
}

/** Show/hide Telegram native loading indicator. */
function showLoading() {
  if (TG && TG.MainButton) {
    try { TG.MainButton.showProgress(); } catch(e) {}
  }
}
function hideLoading() {
  if (TG && TG.MainButton) {
    try { TG.MainButton.hideProgress(); } catch(e) {}
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
