const SAMPLE = `services:
  frontend:
    build: ./frontend
    image: node:20
    ports:
      - "3000:3000"
    volumes:
      - ./frontend/src:/app/src

  api:
    build: ./api
    image: node:20
    ports:
      - "4000:4000"
    command: node server.js
    depends_on:
      - db
      - cache
    environment:
      JWT_SECRET: dev-secret-123

  db:
    image: postgres:15
    volumes:
      - pgdata:/var/lib/postgresql/data

  cache:
    image: redis:7

  search:
    image: mongo:6

volumes:
  pgdata:
`;

const form = document.getElementById("form");
const composeField = document.getElementById("compose");
const submitButton = document.getElementById("submit");
const errorBox = document.getElementById("error");
const resultBox = document.getElementById("result");

const ICONS = { error: "!", warning: "▲", info: "i" };

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

function noteMarkup(note) {
  const service = note.service
    ? `<span class="svc">&nbsp;${escapeHtml(note.service)}</span>`
    : "";
  return `<div class="note ${escapeHtml(note.severity)}">
    <span class="icon">${ICONS[note.severity] || "i"}</span>
    <div>
      <div class="title">${escapeHtml(note.title)}${service}</div>
      <div class="detail">${escapeHtml(note.detail)}</div>
    </div>
  </div>`;
}

function fileMarkup(name, body, elementId, shareId) {
  const download = shareId
    ? `<a class="copy" href="/m/${encodeURIComponent(shareId)}/download/${name}">download</a>`
    : "";
  return `<div>
    <div class="file-head">
      <span class="file-name">${name}</span>
      <span>${download}
        <button class="copy" data-target="${elementId}" type="button">copy</button></span>
    </div>
    <pre id="${elementId}">${escapeHtml(body)}</pre>
  </div>`;
}

function render(data) {
  const notes = data.notes.length
    ? data.notes.map(noteMarkup).join("")
    : '<p style="color:var(--muted); margin:0">Nothing needs attention. This stack maps cleanly onto Zerops.</p>';
  const badge = data.valid
    ? '<span class="badge ok">schema valid</span>'
    : '<span class="badge bad">schema errors</span>';
  const share = data.share_id
    ? `<p class="share">Share link: <a href="/m/${encodeURIComponent(data.share_id)}">/m/${escapeHtml(data.share_id)}</a></p>`
    : "";

  resultBox.innerHTML = `
    <h2>Architecture</h2>
    ${data.diagram}
    <h2>Review ${badge}</h2>
    <div class="panel">${notes}</div>
    <h2>Generated files</h2>
    <div class="grid">
      ${fileMarkup("zerops-project-import.yml", data.import_yaml, "import-yaml", data.share_id)}
      ${fileMarkup("zerops.yaml", data.zerops_yaml, "zerops-yaml", data.share_id)}
    </div>
    <h2>Deploy it</h2>
    <ol class="steps">
      <li>Save the first file as <code>zerops-project-import.yml</code>, then run
          <code>zcli project project-import zerops-project-import.yml</code></li>
      <li>Commit the second file to your repository root as <code>zerops.yaml</code></li>
      <li>Work through the warnings above, especially bind mounts and any missing
          <code>start</code> command</li>
      <li>Connect the repository in the Zerops GUI, or run <code>zcli push</code></li>
    </ol>
    ${share}`;
  resultBox.hidden = false;
}

async function translate(event) {
  event.preventDefault();
  errorBox.hidden = true;
  submitButton.disabled = true;
  submitButton.textContent = "Translating…";
  try {
    const response = await fetch("/api/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        compose: composeField.value,
        project_name: document.getElementById("project_name").value || "migrated",
        share: document.getElementById("share").checked,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "The API rejected that compose file.");
    }
    render(data);
    resultBox.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    resultBox.hidden = true;
    errorBox.textContent = err.message;
    errorBox.hidden = false;
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Translate";
  }
}

form.addEventListener("submit", translate);

document.getElementById("sample").addEventListener("click", function () {
  composeField.value = SAMPLE;
  composeField.focus();
});

document.addEventListener("click", function (event) {
  const button = event.target.closest("button.copy");
  if (!button) return;
  const code = document.getElementById(button.dataset.target);
  navigator.clipboard.writeText(code.innerText).then(function () {
    const original = button.textContent;
    button.textContent = "copied";
    setTimeout(function () { button.textContent = original; }, 1200);
  });
});

const shared = window.location.pathname.match(/^\/m\/([A-Za-z0-9]+)$/);
if (shared) {
  fetch(`/api/shared/${shared[1]}`)
    .then(function (response) {
      if (!response.ok) throw new Error("That share link does not exist or has expired.");
      return response.json();
    })
    .then(function (data) {
      composeField.value = data.compose;
      document.getElementById("project_name").value = data.project_name;
      render(data);
    })
    .catch(function (err) {
      errorBox.textContent = err.message;
      errorBox.hidden = false;
    });
}
