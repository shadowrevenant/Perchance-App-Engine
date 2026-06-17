chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === "launchApp") {
    const { slug } = message;
    const url = `https:
    
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

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if ((changeInfo.status === "loading" || changeInfo.status === "complete") && tab.url) {
    try {
      const url = new URL(tab.url);
      if (url.hostname.includes("perchance.org")) {
        const pathSegments = url.pathname.split("/").filter(Boolean);
        if (pathSegments.length > 0) {
          const slug = pathSegments[0].toLowerCase();
          
          chrome.scripting.executeScript({
            target: { tabId: tabId },
            func: (appSlug) => {
              if (window.__isolated_storage_bound) return;
              window.__isolated_storage_bound = true;

              const prefix = "__isolated_" + appSlug + "__";
              
              const saveStorageToExtension = () => {
                const dataToSave = {};
                for (let i = 0; i < localStorage.length; i++) {
                  const rawKey = localStorage.key(i);
                  if (rawKey && rawKey.startsWith(prefix)) {
                    const shortKey = rawKey.substring(prefix.length);
                    dataToSave[shortKey] = localStorage.getItem(rawKey);
                  }
                }
                const updateObject = {};
                updateObject["isolated_storage_" + appSlug] = dataToSave;
                chrome.storage.local.set(updateObject);
              };

              chrome.storage.local.get(["isolated_storage_" + appSlug], (res) => {
                let savedStore = res["isolated_storage_" + appSlug];
                
                if (!savedStore) {
                  savedStore = {};
                  for (let i = 0; i < localStorage.length; i++) {
                    const k = localStorage.key(i);
                    if (k && !k.startsWith("__isolated_")) {
                      savedStore[k] = localStorage.getItem(k);
                    }
                  }
                  const initObj = {};
                  initObj["isolated_storage_" + appSlug] = savedStore;
                  chrome.storage.local.set(initObj);
                }

                for (const [k, v] of Object.entries(savedStore)) {
                  localStorage.setItem(prefix + k, v);
                }

                const proxyScriptSource = `
                  (function() {
                    if (window.__perchance_storage_proxy_active) return;
                    window.__perchance_storage_proxy_active = true;

                    const appSlug = "${appSlug}";
                    const prefix = "__isolated_" + appSlug + "__";
                    const nativeStorage = window.localStorage;

                    const isolatedStorage = {
                      getItem(key) {
                        return nativeStorage.getItem(prefix + key);
                      },
                      setItem(key, value) {
                        nativeStorage.setItem(prefix + key, String(value));
                        window.dispatchEvent(new CustomEvent('perchance_storage_write_triggered'));
                      },
                      removeItem(key) {
                        nativeStorage.removeItem(prefix + key);
                        window.dispatchEvent(new CustomEvent('perchance_storage_write_triggered'));
                      },
                      clear() {
                        const keysToRemove = [];
                        for (let i = 0; i < nativeStorage.length; i++) {
                          const k = nativeStorage.key(i);
                          if (k && k.startsWith(prefix)) {
                            keysToRemove.push(k);
                          }
                        }
                        keysToRemove.forEach(k => nativeStorage.removeItem(k));
                        window.dispatchEvent(new CustomEvent('perchance_storage_write_triggered'));
                      },
                      key(index) {
                        const filteredKeys = [];
                        for (let i = 0; i < nativeStorage.length; i++) {
                          const k = nativeStorage.key(i);
                          if (k && k.startsWith(prefix)) {
                            filteredKeys.push(k.substring(prefix.length));
                          }
                        }
                        return filteredKeys[index] || null;
                      },
                      get length() {
                        let count = 0;
                        for (let i = 0; i < nativeStorage.length; i++) {
                          const k = nativeStorage.key(i);
                          if (k && k.startsWith(prefix)) {
                            count++;
                          }
                        }
                        return count;
                      }
                    };

                    const storageProxy = new Proxy(isolatedStorage, {
                      get(target, prop) {
                        if (typeof prop === 'symbol') return undefined;
                        if (prop in isolatedStorage) {
                          const val = isolatedStorage[prop];
                          if (typeof val === 'function') {
                            return val.bind(isolatedStorage);
                          }
                          return val;
                        }
                        return isolatedStorage.getItem(prop);
                      },
                      set(target, prop, value) {
                        if (typeof prop === 'symbol') return false;
                        if (prop in isolatedStorage) return false;
                        isolatedStorage.setItem(prop, value);
                        return true;
                      },
                      deleteProperty(target, prop) {
                        if (typeof prop === 'symbol') return false;
                        isolatedStorage.removeItem(prop);
                        return true;
                      },
                      ownKeys() {
                        const keys = [];
                        for (let i = 0; i < nativeStorage.length; i++) {
                          const k = nativeStorage.key(i);
                          if (k && k.startsWith(prefix)) {
                            keys.push(k.substring(prefix.length));
                          }
                        }
                        return keys;
                      },
                      getOwnPropertyDescriptor(target, prop) {
                        if (typeof prop === 'symbol' || prop in isolatedStorage) return undefined;
                        const val = isolatedStorage.getItem(prop);
                        if (val === null) return undefined;
                        return { value: val, writable: true, enumerable: true, configurable: true };
                      }
                    });

                    try {
                      Object.defineProperty(window, 'localStorage', {
                        value: storageProxy,
                        configurable: true,
                        writable: true
                      });
                    } catch (e) {
                      console.warn("localStorage proxy define failed, falling back to prototype override:", e);
                      try {
                        Storage.prototype.getItem = function(k) { return nativeStorage.getItem(prefix + k); };
                        Storage.prototype.setItem = function(k, v) { 
                          nativeStorage.setItem(prefix + k, String(v)); 
                          window.dispatchEvent(new CustomEvent('perchance_storage_write_triggered'));
                        };
                        Storage.prototype.removeItem = function(k) { 
                          nativeStorage.removeItem(prefix + k); 
                          window.dispatchEvent(new CustomEvent('perchance_storage_write_triggered'));
                        };
                      } catch(err) { console.error("Prototype fallback override failed:", err); }
                    }
                  })();
                `;

                const scriptEl = document.createElement("script");
                scriptEl.textContent = proxyScriptSource;
                document.documentElement.appendChild(scriptEl);
                scriptEl.remove();
              });

              window.addEventListener('perchance_storage_write_triggered', saveStorageToExtension);
              window.addEventListener('beforeunload', saveStorageToExtension);
              
              setInterval(saveStorageToExtension, 4000);
            },
            args: [slug]
          }).catch(err => console.log("Failed to inject Storage Isolator:", err));

          if (changeInfo.status === "complete") {
            chrome.storage.local.get(["globalJs", "apps"], (data) => {
              const globalJs = data.globalJs || "";
              const apps = data.apps || [];
              const app = apps.find(a => a.slug === slug);
              const specificJs = app ? app.jsOverride : "";
              
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
      }
    } catch (e) {
      console.error("Error matching url in tabs.onUpdated:", e);
    }
  }
});
