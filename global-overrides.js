// global-overrides.js
// Injected into generator pages after load.
// Keep this file harmless: no Turnstile patching, no fetch spoofing,
// no Cloudflare manipulation.

(() => {
  const host = (location.hostname || "").toLowerCase();

  const blockedHosts = [
    "text-generation.perchance.org",
  ];

  const blockedSuffixes = [
    "challenges.cloudflare.com",
    "cloudflare.com",
  ];

  const shouldSkip =
    blockedHosts.includes(host) ||
    blockedSuffixes.some(suffix => host.endsWith(suffix));

  if (shouldSkip) {
    console.log("[PerchanceEngine] global overrides skipped on", host);
    return;
  }

  console.log("[PerchanceEngine] global-overrides.js loaded on", host);

  // Safe UI-only examples:
  // document.documentElement.style.setProperty('--bg', '#1c1b19', 'important');
  // const banners = document.querySelectorAll('[class*="app-banner"], [id*="app-banner"]');
  // banners.forEach(el => el.remove());
})();