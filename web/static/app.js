const state = {
  jobId: null,
  pollHandle: null,
  selectedFile: null,
  extensions: [],
};

const views = {
  login: document.getElementById("login-view"),
  app: document.getElementById("app-view"),
};

const dropZone = document.getElementById("drop-zone");
const dropZoneText = document.getElementById("drop-zone-text");
const fileInput = document.getElementById("file-input");
const modelSelect = document.getElementById("model-select");
const languageSelect = document.getElementById("language-select");
const transcribeBtn = document.getElementById("transcribe-btn");
const cancelBtn = document.getElementById("cancel-btn");
const statusArea = document.getElementById("status-area");
const statusText = document.getElementById("status-text");
const progressBar = document.getElementById("progress-bar");
const etaText = document.getElementById("eta-text");
const resultArea = document.getElementById("result-area");
const downloadLink = document.getElementById("download-link");
const errorText = document.getElementById("error-text");

function showView(name) {
  views.login.classList.toggle("hidden", name !== "login");
  views.app.classList.toggle("hidden", name !== "app");
}

function showError(message) {
  errorText.textContent = message;
  errorText.classList.remove("hidden");
}

function hideError() {
  errorText.classList.add("hidden");
}

function hideResult() {
  resultArea.classList.add("hidden");
}

function showResult(downloadUrl) {
  downloadLink.href = downloadUrl;
  resultArea.classList.remove("hidden");
}

function formatEta(seconds) {
  if (seconds == null) return "Calculando tiempo restante…";
  const total = Math.max(0, Math.round(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `Tiempo restante: ${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function safeErrorDetail(responseText) {
  try {
    return JSON.parse(responseText).detail || "Error inesperado.";
  } catch {
    return "Error inesperado.";
  }
}

// ── Configuración (modelos / idiomas) ─────────────────────────────────────────

async function loadConfig() {
  const res = await fetch("/api/config");
  if (!res.ok) throw new Error("not authenticated");
  const cfg = await res.json();
  state.extensions = cfg.extensions;

  modelSelect.innerHTML = "";
  for (const model of cfg.models) {
    const opt = document.createElement("option");
    opt.value = model;
    opt.textContent = model;
    if (model === cfg.default_model) opt.selected = true;
    modelSelect.appendChild(opt);
  }

  languageSelect.innerHTML = "";
  for (const name of Object.keys(cfg.languages)) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    if (name === cfg.default_language) opt.selected = true;
    languageSelect.appendChild(opt);
  }
}

async function tryRestoreSession() {
  try {
    await loadConfig();
    showView("app");
  } catch {
    showView("login");
  }
}

// ── Login / logout ────────────────────────────────────────────────────────────

document.getElementById("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const loginError = document.getElementById("login-error");
  loginError.classList.add("hidden");

  const passwordInput = document.getElementById("password-input");
  const body = new FormData();
  body.append("password", passwordInput.value);

  const res = await fetch("/api/login", { method: "POST", body });
  if (res.ok) {
    passwordInput.value = "";
    await loadConfig();
    showView("app");
  } else {
    loginError.textContent = res.status === 429
      ? "Demasiados intentos. Inténtalo de nuevo en unos minutos."
      : "Contraseña incorrecta.";
    loginError.classList.remove("hidden");
  }
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  resetAppState();
  showView("login");
});

// ── Selección de archivo ──────────────────────────────────────────────────────

dropZone.addEventListener("click", () => fileInput.click());

dropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropZone.classList.add("dragover");
});

dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));

dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropZone.classList.remove("dragover");
  if (event.dataTransfer.files.length) selectFile(event.dataTransfer.files[0]);
});

fileInput.addEventListener("change", () => {
  if (fileInput.files.length) selectFile(fileInput.files[0]);
});

function selectFile(file) {
  const ext = "." + file.name.split(".").pop().toLowerCase();
  if (state.extensions.length && !state.extensions.includes(ext)) {
    showError(`Extensión '${ext}' no soportada. Usa: ${state.extensions.join(", ")}`);
    return;
  }
  state.selectedFile = file;
  dropZoneText.textContent = file.name;
  transcribeBtn.disabled = false;
  hideError();
  hideResult();
}

// ── Transcripción ──────────────────────────────────────────────────────────────

transcribeBtn.addEventListener("click", startTranscription);
cancelBtn.addEventListener("click", cancelTranscription);

function startTranscription() {
  if (!state.selectedFile) return;
  hideError();
  hideResult();
  setProcessingUI(true);
  statusText.textContent = "Subiendo vídeo…";
  progressBar.removeAttribute("value");

  const formData = new FormData();
  formData.append("file", state.selectedFile);
  formData.append("model", modelSelect.value);
  formData.append("language", languageSelect.value);

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/jobs");
  xhr.upload.addEventListener("progress", (event) => {
    if (event.lengthComputable) {
      progressBar.value = (event.loaded / event.total) * 100;
    }
  });
  xhr.addEventListener("load", () => {
    if (xhr.status === 201) {
      const job = JSON.parse(xhr.responseText);
      state.jobId = job.id;
      pollStatus();
    } else {
      setProcessingUI(false);
      showError(safeErrorDetail(xhr.responseText));
    }
  });
  xhr.addEventListener("error", () => {
    setProcessingUI(false);
    showError("Error de red al subir el vídeo.");
  });
  xhr.send(formData);
}

function pollStatus() {
  state.pollHandle = setInterval(async () => {
    const res = await fetch(`/api/jobs/${state.jobId}`);
    if (!res.ok) {
      clearInterval(state.pollHandle);
      setProcessingUI(false);
      showError("No se pudo consultar el estado del trabajo.");
      return;
    }

    const job = await res.json();
    renderJobState(job);

    if (job.status === "done" || job.status === "error" || job.status === "cancelled") {
      clearInterval(state.pollHandle);
      setProcessingUI(false);
      if (job.status === "done") {
        showResult(job.download_url);
      } else if (job.status === "error") {
        showError(job.error_message || "Error durante la transcripción.");
      } else {
        statusText.textContent = "Transcripción cancelada";
      }
    }
  }, 1500);
}

function renderJobState(job) {
  statusText.textContent = job.status_text;
  if (job.percent != null) {
    progressBar.value = job.percent;
  } else {
    progressBar.removeAttribute("value");
  }
  etaText.textContent = formatEta(job.remaining_seconds);
}

async function cancelTranscription() {
  if (!state.jobId) return;
  cancelBtn.disabled = true;
  cancelBtn.textContent = "Cancelando…";
  await fetch(`/api/jobs/${state.jobId}/cancel`, { method: "POST" });
}

function setProcessingUI(isProcessing) {
  transcribeBtn.classList.toggle("hidden", isProcessing);
  cancelBtn.classList.toggle("hidden", !isProcessing);
  cancelBtn.disabled = false;
  cancelBtn.textContent = "Cancelar";
  statusArea.classList.toggle("hidden", !isProcessing);
  modelSelect.disabled = isProcessing;
  languageSelect.disabled = isProcessing;
  dropZone.classList.toggle("disabled", isProcessing);
  if (!isProcessing) {
    progressBar.value = 0;
  }
}

function resetAppState() {
  if (state.pollHandle) clearInterval(state.pollHandle);
  state.jobId = null;
  state.selectedFile = null;
  fileInput.value = "";
  dropZoneText.textContent = "Arrastra un vídeo aquí o haz clic para seleccionarlo";
  transcribeBtn.disabled = true;
  hideError();
  hideResult();
  setProcessingUI(false);
}

tryRestoreSession();
