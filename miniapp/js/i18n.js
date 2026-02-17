/**
 * i18n — lightweight translation loader for Incognitus Mini Apps.
 *
 * Usage:
 *   await i18n.load("ru");       // fetch & cache /i18n/ru.json (falls back to en)
 *   i18n.t("copy");              // → "Копировать"
 *   i18n.apply();                // update all [data-i18n] elements
 *   i18n.lang;                   // current language code
 */

var i18n = (function () {
  "use strict";

  var SUPPORTED = ["en", "ru", "fa", "ar"];
  var RTL_LANGS = ["ar", "fa"];
  var cache = {};
  var fallback = {};
  var current = {};
  var lang = "en";

  /** Fetch a JSON translation file (with simple in-memory cache). */
  function fetchLang(code) {
    if (cache[code]) return Promise.resolve(cache[code]);
    return fetch("i18n/" + code + ".json")
      .then(function (res) {
        if (!res.ok) throw new Error(res.status);
        return res.json();
      })
      .then(function (data) {
        cache[code] = data;
        return data;
      });
  }

  /**
   * Load a language (and English fallback if different).
   * @param {string} code — language code (en, ru, fa, ar)
   * @returns {Promise<void>}
   */
  function load(code) {
    code = (code || "en").split("-")[0].toLowerCase();
    if (SUPPORTED.indexOf(code) === -1) code = "en";
    lang = code;
    try { localStorage.setItem("i18n_lang", lang); } catch(e) {}

    var jobs = [fetchLang(code)];
    if (code !== "en") jobs.push(fetchLang("en"));

    return Promise.all(jobs).then(function (results) {
      current = results[0];
      fallback = results[1] || results[0];
      // Apply RTL
      if (RTL_LANGS.indexOf(lang) !== -1) {
        document.documentElement.setAttribute("dir", "rtl");
      } else {
        document.documentElement.removeAttribute("dir");
      }
    });
  }

  /** Translate a single key. */
  function t(key) {
    return (current && current[key]) || (fallback && fallback[key]) || key;
  }

  /** Apply translations to all [data-i18n] elements in the DOM, then reveal. */
  function apply() {
    var els = document.querySelectorAll("[data-i18n]");
    for (var i = 0; i < els.length; i++) {
      var key = els[i].getAttribute("data-i18n");
      els[i].textContent = t(key);
    }
    document.body.classList.remove("i18n-loading");
  }

  /**
   * Detect language from Telegram user or navigator, load it, then apply.
   * @returns {Promise<string>} resolved language code
   */
  function detect() {
    var code = "en";
    var hasStored = false;
    // Prefer stored bot language (set by dashboard via /lang)
    try {
      var stored = localStorage.getItem("i18n_lang");
      if (stored) { code = stored; hasStored = true; }
    } catch(e) {}
    // Fall back to Telegram UI language only if no stored preference
    if (!hasStored) {
      var tg = window.Telegram && window.Telegram.WebApp;
      if (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) {
        code = (tg.initDataUnsafe.user.language_code || "en").split("-")[0];
      }
    }
    return load(code).then(function () {
      apply();
      return lang;
    });
  }

  return {
    load: load,
    t: t,
    apply: apply,
    detect: detect,
    get lang() { return lang; }
  };
})();
