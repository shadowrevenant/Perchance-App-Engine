let apps = [
  {
    "slug": "ai-character-generator",
    "name": "AI Character Generator",
    "description": "Generates high-fidelity character descriptions, portraits, and stats for various roleplay systems.",
    "color": "#01696f",
    "isCustomFavicon": false,
    "jsOverride": "// overrides.js — Injected into AI Character Generator\nconst style = document.createElement('style');\nstyle.textContent = `\n  body { \n    background-color: #171614 !important;\n    color: #e2e1df !important;\n  }\n  .advertisement, .ads, [id*=\"google_ads\"] {\n    display: none !important;\n  }\n`;\ndocument.head.appendChild(style);\nconsole.log(\"⚡ AI Character Generator Clean theme injected.\");"
  },
  {
    "slug": "petrafied-acc",
    "name": "Petrafied Acc",
    "description": "Interactive creative writer and scenario accelerator tools.",
    "color": "#8a4fff",
    "isCustomFavicon": false,
    "jsOverride": "// overrides.js — Injected into Petrafied Acc\nconsole.log(\"✨ Petrafied Acc overrides running!\");\n// Highlight important outputs\nsetInterval(() => {\n  document.querySelectorAll('strong').forEach(el => {\n    el.style.color = '#8a4fff';\n  });\n}, 1000);"
  },
  {
    "slug": "luminara",
    "name": "Luminara",
    "description": "Luminara - AI Roleplay (now with user personas) and immersive creative writing worlds.",
    "color": "#4f98a3",
    "faviconUrl": "https://user.uploads.dev/file/1940e750f55394f4feaefe92e95250e4.png",
    "isCustomFavicon": true,
    "jsOverride": "// overrides.js — Custom roleplay presets injection\nconsole.log(\"🌌 Immersive Roleplay overlay active on Luminara.\");"
  },
  {
    "slug": "dice-roller",
    "name": "Dice Roller",
    "description": "Highly randomized board game modifier with physical log history.",
    "color": "#f59e0b",
    "isCustomFavicon": false,
    "jsOverride": "// overrides.js — Custom Dice Roller JS\nconsole.log(\"🎲 Dice Roller Loaded!\");"
  }
];
let globalJs = `// global-overrides.js — Injected into ALL perchance generators
console.log("[PCE Extension] Initiating global injections...");

// 1. Hide generic perchance ads & unwanted banners
const cleanStyle = document.createElement('style');
cleanStyle.textContent = \`
  /* Standard ad classes used on Perchance */
  .perchance-ad, .ad-container, #ad-slot, .adsbygoogle {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
  }
\`;
document.head.appendChild(cleanStyle);`;
let editingSlug = null;

// Load current configuration from storage if it exists, otherwise write initial template values
function init() {
  chrome.storage.local.get(["apps", "globalJs"], (data) => {
    if (data.apps) {
      apps = data.apps;
    } else {
      chrome.storage.local.set({ apps });
    }
    if (data.globalJs !== undefined) {
      globalJs = data.globalJs;
    } else {
      chrome.storage.local.set({ globalJs });
    }
    renderGrid();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  init();

  const searchInput = document.getElementById("search-input");
  searchInput.addEventListener("input", renderGrid);

  const addBtn = document.getElementById("add-btn");
  addBtn.addEventListener("click", openAddModal);

  const editGlobalBtn = document.getElementById("edit-global-btn");
  editGlobalBtn.addEventListener("click", openGlobalJsModal);

  const modalCancel = document.getElementById("modal-cancel");
  modalCancel.addEventListener("click", closeModal);

  const modalSave = document.getElementById("modal-save");
  modalSave.addEventListener("click", saveModal);

  const colorInput = document.getElementById("color-input");
  const colorText = document.getElementById("color-text");
  colorInput.addEventListener("input", () => {
    colorText.value = colorInput.value;
  });
  colorText.addEventListener("input", () => {
    if (/^#[0-9A-F]{6}$/i.test(colorText.value)) {
      colorInput.value = colorText.value;
    }
  });

  // Dynamic Metadata Fetch button listener for real Perchance stats integration
  const fetchMetaBtn = document.getElementById("fetch-meta-btn");
  if (fetchMetaBtn) {
    fetchMetaBtn.addEventListener("click", () => {
      const slugInput = document.getElementById("slug-input");
      const slug = slugInput.value.trim().toLowerCase().replace(/\s+/g, '-');
      if (!slug) {
        alert("Please enter a slug first!");
        return;
      }
      
      const statusText = document.getElementById("fetch-status-text");
      if (statusText) {
        statusText.style.display = "block";
        statusText.innerText = "⏳ Querying perchance.org stats...";
        statusText.style.color = "#4f98a3";
      }

      fetch(`https://perchance.org/api/getGeneratorStats?name=${slug}`)
        .then(res => res.json())
        .then(data => {
          if (data && data.status === "success" && data.data) {
            const info = data.data;
            if (info.metaData) {
              if (info.metaData.title) {
                document.getElementById("name-input").value = info.metaData.title;
              }
              if (info.metaData.description) {
                document.getElementById("desc-input").value = info.metaData.description;
              }
              if (info.metaData.image) {
                document.getElementById("favicon-input").value = info.metaData.image;
              }
              if (statusText) {
                statusText.innerText = `✓ Loaded "${info.metaData.title || slug}" metadata!`;
                statusText.style.color = "#10b981";
              }
            } else {
              if (info.name) {
                document.getElementById("name-input").value = info.name;
              }
              if (statusText) {
                statusText.innerText = "✓ Found generator (No custom description/image).";
                statusText.style.color = "#f59e0b";
              }
            }
          } else {
            if (statusText) {
              statusText.innerText = "✗ Generator not found or stats endpoint failed.";
              statusText.style.color = "#ef4444";
            }
          }
        })
        .catch(err => {
          console.error(err);
          if (statusText) {
            statusText.innerText = "✗ API fetch blocked or network offline.";
            statusText.style.color = "#ef4444";
          }
        });
    });
  }
});

function renderGrid() {
  const container = document.getElementById("grid-container");
  const searchInput = document.getElementById("search-input");
  const query = searchInput.value.trim().toLowerCase();
  
  container.innerHTML = "";
  
  const filtered = apps.filter(app => 
    app.slug.toLowerCase().includes(query) || 
    app.name.toLowerCase().includes(query) ||
    app.description.toLowerCase().includes(query)
  );

  document.getElementById("status-text").innerText = `${filtered.length} app${filtered.length !== 1 ? 's' : ''} installed`;

  if (filtered.length === 0) {
    container.innerHTML = `<div class="empty-state">No generators found</div>`;
    return;
  }

  filtered.forEach(app => {
    const card = document.createElement("div");
    card.className = "app-card";
    card.style.borderTop = `4px solid ${app.color || '#01696f'}`;
    
    // Initials color visual fallback
    const initials = app.name.split("-").map(w => w[0]).join("").substring(0, 2).toUpperCase() || app.name.substring(0, 2).toUpperCase();

    // Render image icon if faviconUrl is present, otherwise display styled text initials
    const iconHtml = app.faviconUrl 
      ? `<img class="card-icon" src="${app.faviconUrl}" alt="${app.name}" referrerpolicy="no-referrer" style="background-color: ${app.color || '#2c2a27'}">`
      : `<div class="card-icon" style="background-color: ${app.color || '#01696f'}">${initials}</div>`;

    card.innerHTML = `
      ${iconHtml}
      <div class="card-title">${app.name}</div>
      <div class="card-desc">${app.description || 'perchance.org/' + app.slug}</div>
      <div class="card-actions">
        <button class="launch-btn" data-slug="${app.slug}">Launch</button>
        <button class="edit-btn" data-slug="${app.slug}">Edit</button>
        <button class="delete-btn" data-slug="${app.slug}">Remove</button>
      </div>
    `;

    // Hook listeners
    card.querySelector(".launch-btn").addEventListener("click", (e) => {
      const slug = e.target.getAttribute("data-slug");
      chrome.runtime.sendMessage({ action: "launchApp", slug });
    });

    card.querySelector(".edit-btn").addEventListener("click", (e) => {
      const slug = e.target.getAttribute("data-slug");
      openEditModal(slug);
    });

    card.querySelector(".delete-btn").addEventListener("click", (e) => {
      const slug = e.target.getAttribute("data-slug");
      if (confirm(`Are you sure you want to remove "${slug}" from the extension?`)) {
        apps = apps.filter(item => item.slug !== slug);
        chrome.storage.local.set({ apps }, () => {
          renderGrid();
        });
      }
    });

    container.appendChild(card);
  });
}

function openAddModal() {
  editingSlug = null;
  document.getElementById("modal-title").innerText = "Add Generator App";
  document.getElementById("slug-input").disabled = false;
  document.getElementById("slug-input").value = "";
  document.getElementById("name-input").value = "";
  document.getElementById("desc-input").value = "";
  document.getElementById("favicon-input").value = "";
  document.getElementById("color-input").value = "#01696f";
  document.getElementById("color-text").value = "#01696f";
  
  const statusText = document.getElementById("fetch-status-text");
  if (statusText) statusText.style.display = "none";

  document.getElementById("override-js").value = "// overrides.js \nconsole.log('App loaded.');";
  
  document.getElementById("modal-container").classList.remove("hide");
}

function openEditModal(slug) {
  const app = apps.find(a => a.slug === slug);
  if (!app) return;

  editingSlug = slug;
  document.getElementById("modal-title").innerText = `Edit — ${slug}`;
  document.getElementById("slug-input").disabled = true;
  document.getElementById("slug-input").value = app.slug;
  document.getElementById("name-input").value = app.name;
  document.getElementById("desc-input").value = app.description || "";
  document.getElementById("favicon-input").value = app.faviconUrl || "";
  document.getElementById("color-input").value = app.color || "#01696f";
  document.getElementById("color-text").value = app.color || "#01696f";
  
  const statusText = document.getElementById("fetch-status-text");
  if (statusText) statusText.style.display = "none";

  document.getElementById("override-js").value = app.jsOverride || "";

  document.getElementById("modal-container").classList.remove("hide");
}

function openGlobalJsModal() {
  editingSlug = "__GLOBAL__";
  document.getElementById("modal-title").innerText = "Edit Global overrides.js";
  document.getElementById("slug-input").value = "Global (All Generators)";
  document.getElementById("slug-input").disabled = true;
  document.getElementById("name-input").value = "Global Logic";
  document.getElementById("name-input").disabled = true;
  document.getElementById("desc-input").value = "Runs before app-specific overrides.js files";
  document.getElementById("desc-input").disabled = true;
  
  document.getElementById("override-js").value = globalJs;
  
  document.getElementById("modal-container").classList.remove("hide");
}

function closeModal() {
  document.getElementById("slug-input").disabled = false;
  document.getElementById("name-input").disabled = false;
  document.getElementById("desc-input").disabled = false;
  
  const statusText = document.getElementById("fetch-status-text");
  if (statusText) statusText.style.display = "none";

  document.getElementById("modal-container").classList.add("hide");
}

function saveModal() {
  const overrideJsCode = document.getElementById("override-js").value;

  if (editingSlug === "__GLOBAL__") {
    globalJs = overrideJsCode;
    chrome.storage.local.set({ globalJs }, () => {
      closeModal();
    });
    return;
  }

  const slug = document.getElementById("slug-input").value.trim().toLowerCase().replace(/\s+/g, '-');
  const name = document.getElementById("name-input").value.trim() || slug;
  const desc = document.getElementById("desc-input").value.trim();
  const faviconUrl = document.getElementById("favicon-input").value.trim();
  const color = document.getElementById("color-input").value;

  if (!slug) {
    alert("Slug is required!");
    return;
  }

  if (editingSlug) {
    // Editing
    const index = apps.findIndex(a => a.slug === editingSlug);
    if (index !== -1) {
      apps[index].name = name;
      apps[index].description = desc;
      apps[index].faviconUrl = faviconUrl;
      apps[index].color = color;
      apps[index].jsOverride = overrideJsCode;
    }
  } else {
    // Check key collision
    if (apps.some(a => a.slug === slug)) {
      alert("App with this slug already exists!");
      return;
    }
    // Create new
    apps.push({
      slug,
      name,
      description: desc,
      faviconUrl,
      color,
      jsOverride: overrideJsCode
    });
  }

  chrome.storage.local.set({ apps }, () => {
    closeModal();
    renderGrid();
  });
}
