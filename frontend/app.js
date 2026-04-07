// MySecondMind — Frontend app
const API = "";  // Same origin — FastAPI serves this file

// ── State ──────────────────────────────────────────────────────────────────────
let allNotes = [];
let activeNoteId = null;
let network = null;


// ── Boot ───────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  await loadNotes();
  await loadGraph();
  bindEvents();
});


// ── Notes ──────────────────────────────────────────────────────────────────────
async function loadNotes() {
  try {
    const res = await fetch(`${API}/notes`);
    allNotes = await res.json();
    renderNotesList(allNotes);
  } catch (e) {
    console.error("Failed to load notes", e);
  }
}

function renderNotesList(notes) {
  const container = document.getElementById("notes-list");
  document.getElementById("notes-count").textContent = `${notes.length} Notes`;

  if (!notes.length) {
    container.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text-dim)">No notes yet.<br>Paste a URL above to get started.</div>`;
    return;
  }

  container.innerHTML = notes.map(note => {
    const typeTag = note.source_url
      ? `<span class="tag tag-link">link</span>`
      : `<span class="tag tag-note">note</span>`;
    const domain = note.domain ? `<span style="color:var(--text-dim)">${note.domain}</span>` : "";
    const date = note.updated_at ? note.updated_at.slice(0, 10) : "";
    return `
      <div class="note-item ${activeNoteId === note.id ? 'active' : ''}"
           data-id="${note.id}" onclick="openNote('${note.id}')">
        <div class="note-item-title">${escHtml(note.title)}</div>
        <div class="note-item-meta">${typeTag}${domain}<span>${date}</span></div>
      </div>`;
  }).join("");
}

async function openNote(noteId) {
  activeNoteId = noteId;
  renderNotesList(allNotes);  // Re-render to update active state

  // Find in already-loaded list
  const brief = allNotes.find(n => n.id === noteId);
  if (!brief) return;

  // Fetch full note content
  try {
    const res = await fetch(`${API}/notes/${noteId}`);
    const note = await res.json();
    showNoteDetail(note);

    // Focus the node in graph
    if (network) {
      network.focus(noteId, { animation: true, scale: 1.2 });
      network.selectNodes([noteId]);
    }
  } catch (e) {
    showNoteDetail(brief);
  }
}

function showNoteDetail(note) {
  document.getElementById("detail-title").textContent = note.title;

  const body = document.getElementById("detail-body");
  body.classList.remove("empty");

  const content = note.content || note.summary || "";
  if (content) {
    body.innerHTML = renderMarkdown(content);
    // Make wikilinks clickable (search for matching note by title)
    body.querySelectorAll(".wikilink").forEach(el => {
      el.addEventListener("click", () => {
        const target = el.dataset.target;
        const found = allNotes.find(n => n.title.toLowerCase() === target.toLowerCase());
        if (found) openNote(found.id);
      });
    });
  } else {
    body.innerHTML = `<em style="color:var(--text-dim)">No content available.</em>`;
  }

  // Audio
  const audioPlayer = document.getElementById("audio-player");
  const audioEl = document.getElementById("audio-el");
  if (note.audio_path) {
    audioEl.src = `${API}/audio/${note.id}`;
    audioPlayer.style.display = "flex";
  } else {
    audioPlayer.style.display = "none";
  }
}


// ── Ingest ─────────────────────────────────────────────────────────────────────
async function doIngest() {
  const url = document.getElementById("ingest-url").value.trim();
  const text = document.getElementById("ingest-text").value.trim();
  const title = document.getElementById("ingest-title").value.trim();
  const fileInput = document.getElementById("file-upload");
  const file = fileInput.files[0];
  const status = document.getElementById("ingest-status");
  const btn = document.getElementById("ingest-btn");
  const label = document.getElementById("ingest-btn-label");

  if (!url && !text && !file) {
    setStatus(status, "Paste a URL, some text, or upload a file.", "error");
    return;
  }

  btn.disabled = true;
  label.innerHTML = `<span class="spinner"></span> Processing...`;
  setStatus(status, file ? "Parsing file, summarizing..." : "Fetching, summarizing, generating audio...", "");

  try {
    let res;
    if (file) {
      const fd = new FormData();
      fd.append("file", file);
      if (title) fd.append("title", title);
      res = await fetch(`${API}/upload`, { method: "POST", body: fd });
    } else {
      res = await fetch(`${API}/ingest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url || null, text: text || null, title: title || null }),
      });
    }

    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const body = await res.text();
        const json = JSON.parse(body);
        detail = json.detail || detail;
      } catch (_) { /* non-JSON error body */ }
      throw new Error(detail);
    }

    const data = await res.json();
    setStatus(status, `✓ Added: ${data.note.title}`, "success");

    // Clear form
    document.getElementById("ingest-url").value = "";
    document.getElementById("ingest-text").value = "";
    document.getElementById("ingest-title").value = "";
    clearFileInput();

    // Refresh UI
    await loadNotes();
    await loadGraph();
    openNote(data.note.id);
  } catch (e) {
    setStatus(status, `Error: ${e.message}`, "error");
  } finally {
    btn.disabled = false;
    label.textContent = "Add to Mind";
  }
}

function clearFileInput() {
  const fileInput = document.getElementById("file-upload");
  fileInput.value = "";
  document.getElementById("file-name").textContent = "";
  document.getElementById("file-clear").style.display = "none";
}

function setStatus(el, msg, type) {
  el.textContent = msg;
  el.className = `ingest-status ${type}`;
}


// ── Graph ──────────────────────────────────────────────────────────────────────
async function loadGraph() {
  try {
    const res = await fetch(`${API}/graph`);
    const data = await res.json();
    renderGraph(data);
  } catch (e) {
    console.error("Failed to load graph", e);
  }
}

function renderGraph(data) {
  const container = document.getElementById("graph-canvas");

  const nodeColorMap = {
    link: { background: "#0e4f6b", border: "#22d3ee", highlight: { background: "#155e75", border: "#67e8f9" } },
    note: { background: "#064e3b", border: "#34d399", highlight: { background: "#065f46", border: "#6ee7b7" } },
    concept: { background: "#451a03", border: "#f59e0b", highlight: { background: "#78350f", border: "#fcd34d" } },
  };

  // Scale node size by authority score (min 12, max 40)
  const maxAuth = Math.max(...data.nodes.map(n => n.auth_score || 0), 1);

  const nodes = new vis.DataSet(data.nodes.map(n => {
    const type = n.type || "concept";
    const colors = nodeColorMap[type] || nodeColorMap.concept;
    const size = 12 + (n.auth_score / maxAuth) * 28;
    return {
      id: n.id,
      label: truncate(n.title, 22),
      title: `${n.title}\nType: ${type}\nDomain: ${n.domain || "—"}\nAuth: ${(n.auth_score || 0).toFixed(3)}`,
      color: colors,
      size: size,
      font: { color: "#e2e8f0", size: 11 },
      borderWidth: 1.5,
      shadow: { enabled: true, color: "rgba(0,0,0,0.4)", size: 8 },
    };
  }));

  const edgeLabelColor = { color: "#8892a4", hover: "#e2e8f0" };
  const edges = new vis.DataSet(data.edges.map(e => ({
    from: e.source,
    to: e.target,
    label: e.label !== "mentions" ? e.label : "",
    width: Math.min(0.5 + e.weight, 3),
    color: { color: "#2e3350", hover: "#6c8ef7", opacity: 0.7 },
    font: { color: "#8892a4", size: 9, align: "middle" },
    smooth: { type: "dynamic" },
    arrows: { to: { enabled: true, scaleFactor: 0.4 } },
  })));

  const options = {
    physics: {
      enabled: true,
      solver: "forceAtlas2Based",
      forceAtlas2Based: { gravitationalConstant: -50, springLength: 120, springConstant: 0.05, damping: 0.4 },
      stabilization: { iterations: 150 },
    },
    interaction: { hover: true, tooltipDelay: 200, navigationButtons: false, keyboard: true },
    layout: { improvedLayout: true },
    background: { color: "#0f1117" },
  };

  if (network) {
    network.setData({ nodes, edges });
  } else {
    network = new vis.Network(container, { nodes, edges }, options);

    network.on("click", (params) => {
      if (params.nodes.length > 0) {
        const nodeId = params.nodes[0];
        const note = allNotes.find(n => n.id === nodeId);
        if (note) {
          openNote(nodeId);
        } else {
          // Concept node — show its title
          const nodeData = nodes.get(nodeId);
          document.getElementById("detail-title").textContent = nodeData?.title || nodeId;
          const body = document.getElementById("detail-body");
          body.classList.remove("empty");
          body.innerHTML = `<em style="color:var(--text-dim)">Concept node — open the vault to edit its stub page.</em>`;
          document.getElementById("audio-player").style.display = "none";
        }
      }
    });
  }
}

async function triggerRebuild() {
  const btn = document.getElementById("rebuild-btn");
  btn.disabled = true;
  btn.textContent = "⟳ Rebuilding...";
  try {
    await fetch(`${API}/rebuild-graph`, { method: "POST" });
    await loadGraph();
    btn.textContent = "⟳ Rebuilt ✓";
    setTimeout(() => { btn.textContent = "⟳ Rebuild"; btn.disabled = false; }, 2000);
  } catch (e) {
    btn.textContent = "⟳ Error";
    btn.disabled = false;
  }
}


// ── Events ─────────────────────────────────────────────────────────────────────
function bindEvents() {
  document.getElementById("ingest-btn").addEventListener("click", doIngest);
  document.getElementById("refresh-btn").addEventListener("click", async () => {
    await loadNotes();
    await loadGraph();
  });
  document.getElementById("fit-btn").addEventListener("click", () => {
    if (network) network.fit({ animation: true });
  });
  document.getElementById("rebuild-btn").addEventListener("click", triggerRebuild);

  // Allow pressing Enter in URL field
  document.getElementById("ingest-url").addEventListener("keydown", e => {
    if (e.key === "Enter") doIngest();
  });

  // File upload — show selected filename, clear URL/text fields
  document.getElementById("file-upload").addEventListener("change", e => {
    const file = e.target.files[0];
    if (file) {
      document.getElementById("file-name").textContent = file.name;
      document.getElementById("file-clear").style.display = "inline-flex";
      // Clear URL and text when a file is chosen
      document.getElementById("ingest-url").value = "";
      document.getElementById("ingest-text").value = "";
    } else {
      clearFileInput();
    }
  });

  document.getElementById("file-clear").addEventListener("click", clearFileInput);
}


// ── Markdown renderer (lightweight) ───────────────────────────────────────────
function renderMarkdown(text) {
  // Strip YAML frontmatter
  if (text.startsWith("---")) {
    const parts = text.split("---");
    if (parts.length >= 3) text = parts.slice(2).join("---");
  }
  // Remove trailing JSON block
  text = text.replace(/```json[\s\S]*?```/g, "");

  let html = escHtml(text);

  // Code blocks
  html = html.replace(/```[\s\S]*?```/g, m => `<pre><code>${m.replace(/```\w*\n?/g, "")}</code></pre>`);
  // Inline code
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  // Wikilinks
  html = html.replace(/\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]/g, (_, target, label) =>
    `<a class="wikilink" data-target="${escHtml(target)}" href="#">${escHtml(label || target)}</a>`
  );
  // Headings
  html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
  html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
  html = html.replace(/^# (.+)$/gm, "<h1>$1</h1>");
  // Bold
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  // Italic
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
  // Links
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  // Blockquote
  html = html.replace(/^&gt; (.+)$/gm, "<blockquote>$1</blockquote>");
  // Horizontal rule
  html = html.replace(/^---+$/gm, "<hr>");
  // Lists
  html = html.replace(/^\s*[-*]\s+(.+)$/gm, "<li>$1</li>");
  html = html.replace(/(<li>[\s\S]+?<\/li>)/g, "<ul>$1</ul>");
  // Paragraphs (double newline)
  html = html.replace(/\n\n/g, "</p><p>");
  html = `<p>${html}</p>`;
  // Clean up empty paragraphs
  html = html.replace(/<p>\s*<\/p>/g, "");

  return `<div class="md">${html}</div>`;
}


// ── Helpers ────────────────────────────────────────────────────────────────────
function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function truncate(str, max) {
  return str.length > max ? str.slice(0, max - 1) + "…" : str;
}
