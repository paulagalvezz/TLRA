// EASY-RAG frontend — create/list assistants, upload documents, grounded streaming
// chat with clickable sources (provenance) + token usage.
// SSE + rendering helpers adapted from exercise1.

const DEFAULT_TEMPLATE =
    "Answer the question using only the context below.\n\n" +
    "Context:\n{context}\n\nQuestion: {user_input}";

const DEFAULT_SYSTEM =
    "Use only the information in the context below to answer the question.\n" +
    "If the answer is not in the context, say that you do not know.";

let assistants = [];
let activeAssistantId = null;
let activeRecord = null;
let turn = 0;

const assistantsList = document.getElementById("assistants-list");
const newAssistantButton = document.getElementById("new-assistant");

const chatTitle = document.getElementById("chat-title");
const uploadButton = document.getElementById("upload-document");
const documentInput = document.getElementById("document-input");
const contextToggle = document.getElementById("context-toggle");
const contextPane = document.getElementById("context-pane");
const contextEntries = document.getElementById("context-entries");

const docStatus = document.getElementById("doc-status");
const docList = document.getElementById("doc-list");

const messagesEl = document.getElementById("messages");
const emptyState = document.getElementById("empty-state");
const form = document.getElementById("chat-form");
const input = document.getElementById("user-input");
const sendButton = document.getElementById("send-button");

const assistantModal = document.getElementById("assistant-modal");
const assistantForm = document.getElementById("assistant-form");
const assistantName = document.getElementById("assistant-name");
const assistantSystem = document.getElementById("assistant-system");
const assistantTemplate = document.getElementById("assistant-template");
const assistantFormError = document.getElementById("assistant-form-error");
const assistantCancel = document.getElementById("assistant-cancel");
const assistantSave = document.getElementById("assistant-save");

// --- Assistants list --------------------------------------------------------

async function loadAssistants() {
    try {
        const response = await fetch("/api/assistants");
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        assistants = data.assistants || [];
    } catch (error) {
        appendError(`Could not load assistants: ${error.message}`);
        assistants = [];
    }
    renderAssistantsList();
}

function renderAssistantsList() {
    assistantsList.innerHTML = "";
    for (const assistant of assistants) {
        const item = document.createElement("div");
        item.className = "assistant-item" + (assistant.id === activeAssistantId ? " active" : "");

        const name = document.createElement("div");
        name.className = "assistant-name";
        name.textContent = assistant.name;

        const meta = document.createElement("div");
        meta.className = "assistant-meta";
        meta.textContent =
            `${assistant.document_count} doc${assistant.document_count === 1 ? "" : "s"}` +
            ` · ${assistant.chunk_count} chunk${assistant.chunk_count === 1 ? "" : "s"}`;

        const actions = document.createElement("div");
        actions.className = "assistant-actions";
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "Delete";
        remove.addEventListener("click", (event) => {
            event.stopPropagation();
            deleteAssistant(assistant);
        });
        actions.appendChild(remove);

        item.append(name, meta, actions);
        item.addEventListener("click", () => switchTo(assistant.id));
        assistantsList.appendChild(item);
    }
}

async function switchTo(assistantId) {
    activeAssistantId = assistantId;
    turn = 0;
    messagesEl.innerHTML = "";
    contextEntries.innerHTML = "";

    if (assistantId === null) {
        activeRecord = null;
        chatTitle.textContent = "EASY-RAG";
        uploadButton.disabled = true;
        input.disabled = true;
        sendButton.disabled = true;
        input.placeholder = "Select an assistant to start chatting...";
        renderDocBar(null);
        renderAssistantsList();
        return;
    }

    try {
        const response = await fetch(`/api/assistants/${assistantId}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        activeRecord = await response.json();
    } catch (error) {
        appendError(`Could not load assistant: ${error.message}`);
        activeRecord = null;
    }

    const summary = assistants.find((a) => a.id === assistantId);
    chatTitle.textContent = activeRecord ? activeRecord.name : (summary ? summary.name : "EASY-RAG");
    uploadButton.disabled = false;
    input.disabled = false;
    sendButton.disabled = false;
    input.placeholder = activeRecord
        ? `Ask about ${activeRecord.name}'s documents...`
        : "Type a message...";
    renderDocBar(activeRecord);
    renderAssistantsList();
    input.focus();
}

function renderDocBar(record) {
    docList.innerHTML = "";
    if (!record) {
        docStatus.textContent = "No assistant selected";
        return;
    }
    const docs = record.documents || [];
    const chunks = docs.reduce((sum, d) => sum + (d.chunks || 0), 0);
    docStatus.textContent =
        `${docs.length} doc${docs.length === 1 ? "" : "s"} · ${chunks} chunk${chunks === 1 ? "" : "s"} in collection`;
    if (docs.length === 0) {
        const hint = document.createElement("span");
        hint.className = "upload-hint";
        hint.textContent = "No documents yet — click “Upload document”.";
        docList.appendChild(hint);
        return;
    }
    for (const doc of docs) {
        // Chip links straight to the ORIGINAL document under /static.
        const chip = document.createElement("a");
        chip.className = "doc-chip";
        chip.href = doc.doc_url || "#";
        chip.target = "_blank";
        chip.rel = "noopener";
        chip.title = `${doc.title || doc.name} — open original`;
        chip.textContent = doc.title || doc.name;
        const n = document.createElement("span");
        n.className = "doc-chunks";
        n.textContent = ` · ${doc.chunks}`;
        chip.appendChild(n);
        docList.appendChild(chip);
    }
}

async function deleteAssistant(assistant) {
    if (!window.confirm(`Delete assistant "${assistant.name}"?`)) return;
    try {
        const response = await fetch(`/api/assistants/${assistant.id}`, { method: "DELETE" });
        if (!response.ok) {
            const data = await response.json().catch(() => null);
            throw new Error(detailFromError(data) || `HTTP ${response.status}`);
        }
    } catch (error) {
        appendError(`Could not delete assistant: ${error.message}`);
        return;
    }
    if (activeAssistantId === assistant.id) await switchTo(null);
    await loadAssistants();
}

// --- Create-assistant modal -------------------------------------------------

newAssistantButton.addEventListener("click", openNewModal);
assistantCancel.addEventListener("click", closeModal);
assistantModal.addEventListener("click", (event) => {
    if (event.target === assistantModal) closeModal();
});

function openNewModal() {
    assistantName.value = "";
    assistantSystem.value = DEFAULT_SYSTEM;
    assistantTemplate.value = DEFAULT_TEMPLATE;
    clearFormError();
    assistantModal.classList.remove("hidden");
    assistantName.focus();
}

function closeModal() {
    assistantModal.classList.add("hidden");
    clearFormError();
}

function showFormError(text) {
    assistantFormError.textContent = text;
    assistantFormError.classList.remove("hidden");
}

function clearFormError() {
    assistantFormError.textContent = "";
    assistantFormError.classList.add("hidden");
}

assistantForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearFormError();

    const formData = new FormData();
    formData.append("name", assistantName.value);
    formData.append("system_prompt", assistantSystem.value);
    formData.append("prompt_template", assistantTemplate.value);

    assistantSave.disabled = true;
    try {
        const response = await fetch("/api/assistants", { method: "POST", body: formData });
        if (!response.ok) {
            const data = await response.json().catch(() => null);
            showFormError(detailFromError(data) || `HTTP ${response.status}`);
            return;
        }
        const saved = await response.json();
        closeModal();
        await loadAssistants();
        await switchTo(saved.id);
    } catch (error) {
        showFormError(error.message);
    } finally {
        assistantSave.disabled = false;
    }
});

// --- Upload document --------------------------------------------------------

uploadButton.addEventListener("click", () => {
    if (activeAssistantId === null) return;
    documentInput.value = "";
    documentInput.click();
});

documentInput.addEventListener("change", async () => {
    const file = documentInput.files[0];
    if (!file || activeAssistantId === null) return;

    const formData = new FormData();
    formData.append("document", file);

    uploadButton.disabled = true;
    const previous = uploadButton.textContent;
    uploadButton.textContent = "Uploading...";
    try {
        const response = await fetch(`/api/assistants/${activeAssistantId}/documents`, {
            method: "POST",
            body: formData,
        });
        const data = await response.json().catch(() => null);
        if (!response.ok) {
            throw new Error(detailFromError(data) || `HTTP ${response.status}`);
        }
        const d = data.document;
        appendSystemNote(
            `Ingested "${d.name}" → ${d.chunks} chunk(s) via ${d.chunking_strategy}; ` +
            `collection now has ${data.collection_total}.`
        );
        await loadAssistants();
        await refreshActiveRecord();
    } catch (error) {
        appendError(`Upload failed: ${error.message}`);
    } finally {
        uploadButton.textContent = previous;
        uploadButton.disabled = activeAssistantId === null;
    }
});

async function refreshActiveRecord() {
    if (activeAssistantId === null) return;
    try {
        const response = await fetch(`/api/assistants/${activeAssistantId}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        activeRecord = await response.json();
        renderDocBar(activeRecord);
    } catch (error) {
        appendError(`Could not refresh assistant: ${error.message}`);
    }
}

// --- Chat -------------------------------------------------------------------

contextToggle.addEventListener("click", () => contextPane.classList.toggle("hidden"));

input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
    }
});

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (activeAssistantId === null) return;
    const content = input.value.trim();
    if (!content) return;

    input.value = "";
    clearEmptyState();
    appendMessage("user", content);
    sendButton.disabled = true;

    const { element, update } = createStreamingMessage();
    let assistantText = "";
    turn += 1;

    try {
        await streamChat(
            `/api/assistants/${activeAssistantId}/chat/stream`,
            { message: content },
            {
                onDelta: (delta) => {
                    assistantText += delta;
                    update(assistantText);
                },
                onDone: (payload, usage, sources) => {
                    element.classList.remove("streaming");
                    update(assistantText);
                    addSources(element, sources);
                    appendContextEntry(turn, payload, usage, sources);
                },
            }
        );
    } catch (error) {
        element.classList.remove("streaming");
        if (element.childNodes.length === 0) element.remove();
        appendError(error.message);
    } finally {
        sendButton.disabled = false;
        input.focus();
    }
});

// --- SSE + rendering helpers ------------------------------------------------

async function streamChat(url, body, { onDelta, onDone }) {
    const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });

    if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try {
            const data = await response.json();
            detail = detailFromError(data) || detail;
        } catch (parseError) {
            // body was not JSON; keep the generic status text
        }
        throw new Error(detail);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finished = false;

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        let separator;
        while ((separator = buffer.indexOf("\n\n")) !== -1) {
            const rawEvent = buffer.slice(0, separator);
            buffer = buffer.slice(separator + 2);

            const parsed = parseSSEEvent(rawEvent);
            if (!parsed) continue;

            if (parsed.type === "delta") {
                onDelta(parsed.content);
            } else if (parsed.type === "done") {
                finished = true;
                onDone(parsed.payload_sent, parsed.usage || {}, parsed.sources || []);
            } else if (parsed.type === "error") {
                throw new Error(parsed.detail || "Stream error");
            }
        }
    }

    if (!finished) onDone(null, {}, []);
}

function parseSSEEvent(rawEvent) {
    const dataLines = rawEvent
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim());
    if (dataLines.length === 0) return null;
    const data = dataLines.join("\n");
    if (data === "[DONE]") return null;
    try {
        return JSON.parse(data);
    } catch (error) {
        return null;
    }
}

function detailFromError(data) {
    if (!data || !data.detail) return null;
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) {
        return data.detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
    }
    return null;
}

function clearEmptyState() {
    if (emptyState && emptyState.parentNode) emptyState.remove();
}

function appendMessage(role, text) {
    clearEmptyState();
    const el = document.createElement("div");
    el.className = `message ${role}`;
    const body = document.createElement("div");
    body.textContent = text;
    el.appendChild(body);
    messagesEl.appendChild(el);
    messagesEl.scrollTop = messagesEl.scrollHeight;
}

function appendSystemNote(text) {
    clearEmptyState();
    const el = document.createElement("div");
    el.className = "message assistant";
    el.style.opacity = "0.75";
    const body = document.createElement("div");
    body.textContent = text;
    el.appendChild(body);
    messagesEl.appendChild(el);
    messagesEl.scrollTop = messagesEl.scrollHeight;
}

function createStreamingMessage() {
    clearEmptyState();
    const element = document.createElement("div");
    element.className = "message assistant streaming";
    messagesEl.appendChild(element);
    const update = (markdown) => {
        element.innerHTML = marked.parse(markdown);
        messagesEl.scrollTop = messagesEl.scrollHeight;
    };
    return { element, update };
}

// Sources footer under an assistant message: clickable links to the ORIGINAL
// document under /static, with chunk number + similarity.
function addSources(messageElement, sources) {
    const box = document.createElement("div");
    box.className = "sources" + (sources && sources.length ? "" : " empty");

    const title = document.createElement("div");
    title.className = "sources-title";
    title.textContent = sources && sources.length ? "Sources" : "No sources";
    box.appendChild(title);

    if (!sources || sources.length === 0) {
        const none = document.createElement("div");
        none.className = "source-none";
        none.textContent = "Nothing passed the similarity threshold — no document was used.";
        box.appendChild(none);
        messageElement.appendChild(box);
        return;
    }

    sources.forEach((s, i) => {
        const item = document.createElement("div");
        item.className = "source-item";

        const idx = document.createElement("span");
        idx.className = "src-idx";
        idx.textContent = `[${i + 1}]`;

        const link = document.createElement("a");
        link.href = s.doc_url || "#";
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = s.title || s.source || "document";

        const chunkNo = document.createElement("span");
        chunkNo.className = "chunk-no";
        chunkNo.textContent = `chunk ${s.chunk_number ?? "?"}`;

        const sim = document.createElement("span");
        sim.className = "sim";
        sim.textContent = typeof s.similarity === "number" ? `sim ${s.similarity.toFixed(3)}` : "";

        item.append(idx, link, chunkNo, sim);
        if (s.md_url) {
            const md = document.createElement("a");
            md.className = "md-link";
            md.href = s.md_url;
            md.target = "_blank";
            md.rel = "noopener";
            md.textContent = "md";
            md.title = "Open the markdown distillation";
            item.appendChild(md);
        }
        box.appendChild(item);
    });

    messageElement.appendChild(box);
    messagesEl.scrollTop = messagesEl.scrollHeight;
}

function appendError(text) {
    clearEmptyState();
    const el = document.createElement("div");
    el.className = "message error";
    el.textContent = `Error: ${text}`;
    messagesEl.appendChild(el);
    messagesEl.scrollTop = messagesEl.scrollHeight;
}

function appendContextEntry(turnNumber, payload, usage, sources) {
    const entry = document.createElement("div");
    entry.className = "context-entry";

    const title = document.createElement("h3");
    title.textContent = `Turn ${turnNumber}`;

    const usageEl = document.createElement("div");
    usageEl.className = "usage";
    usageEl.innerHTML =
        `<span>prompt: ${usage.prompt_tokens ?? "?"}</span>` +
        `<span>completion: ${usage.completion_tokens ?? "?"}</span>` +
        `<span>total: ${usage.total_tokens ?? "?"}</span>`;

    entry.append(title, usageEl);

    if (sources && sources.length) {
        const srcTitle = document.createElement("div");
        srcTitle.className = "ctx-sources-title";
        srcTitle.textContent = `Sources (${sources.length}):`;
        entry.appendChild(srcTitle);
        const ul = document.createElement("div");
        ul.className = "ctx-sources";
        sources.forEach((s, i) => {
            const line = document.createElement("div");
            const a = document.createElement("a");
            a.href = s.doc_url || "#";
            a.target = "_blank";
            a.rel = "noopener";
            a.textContent = `[${i + 1}] ${s.title || s.source}`;
            line.append(a, document.createTextNode(` · chunk ${s.chunk_number ?? "?"} · sim ${typeof s.similarity === "number" ? s.similarity.toFixed(3) : "?"}`));
            ul.appendChild(line);
        });
        entry.appendChild(ul);
    }

    const pre = document.createElement("pre");
    pre.textContent = payload
        ? JSON.stringify(payload, null, 2)
        : "(stream interrupted before completion)";
    entry.appendChild(pre);

    contextEntries.appendChild(entry);
    contextEntries.scrollTop = contextEntries.scrollHeight;
}

loadAssistants();
