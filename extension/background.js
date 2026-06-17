// background.js - Perchance App Engine Chrome Extension Background Worker
// Handles isolated window creation and custom JavaScript injection scripts.

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === "launchApp") {
    const { slug } = message;
    const url = `https://perchance.org/${slug}`;
    
    // Create a standalone popup window without toolbars for that native-app feel
    chrome.windows.create({
      url: url,
      type: "popup",
      width: 960,
      height: 720
    });
    
    sendResponse({ success: true });
  }
  return true;
});

// Watch for page loaded events on perchance.org to inject custom overrides.js
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === "complete" && tab.url) {
    try {
      const url = new URL(tab.url);
      if (url.hostname.includes("perchance.org")) {
        // Extract the slug (e.g. perchance.org/ai-character-generator -> ai-character-generator)
        const pathSegments = url.pathname.split("/").filter(Boolean);
        if (pathSegments.length > 0) {
          const slug = pathSegments[0].toLowerCase();
          
          // Inject code from storage
          chrome.storage.local.get(["globalJs", "apps"], (data) => {
            const globalJs = data.globalJs || "";
            const apps = data.apps || [];
            const app = apps.find(a => a.slug === slug);
            const specificJs = app ? app.jsOverride : "";
            
            // First inject global style & general overrides JS
            if (globalJs.trim()) {
              chrome.scripting.executeScript({
                target: { tabId: tabId },
                func: (code) => {
                  try {
                    const scriptEl = document.createElement("script");
                    scriptEl.textContent = code;
                    document.documentElement.appendChild(scriptEl);
                    scriptEl.remove();
                  } catch (e) {
                    console.error("Global JS injection failed:", e);
                  }
                },
                args: [globalJs]
              }).catch(err => console.log("Failed to inject globalJs:", err));
            }
            
            // Next inject per-app custom overrides JS
            if (specificJs.trim()) {
              chrome.scripting.executeScript({
                target: { tabId: tabId },
                func: (code) => {
                  try {
                    const scriptEl = document.createElement("script");
                    scriptEl.textContent = code;
                    document.documentElement.appendChild(scriptEl);
                    scriptEl.remove();
                  } catch (e) {
                    console.error("App JS injection failed:", e);
                  }
                },
                args: [specificJs]
              }).catch(err => console.log("Failed to inject specJs:", err));
            }
          });
        }
      }
    } catch (e) {
      console.error("Error matching url in tabs.onUpdated:", e);
    }
  }
});