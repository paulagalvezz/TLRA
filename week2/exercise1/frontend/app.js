const messages = [];
const pendingImages = [];
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

const DEFAULT_TEMPLATE =
    "Answer the question using only the context below.\n\n" +
    "Context:\n{context}\n\nQuestion: {user_input}";

let assistants = [];
let activeAssistantId = null;
let editingAssistantId = null;

const form = document.getElementById("chat-form");
const input = document.getElementById("user-input");
const sendButton = document.getElementById("send-button");
const messagesEl = document.getElementById("messages");
const chatTitle = document.getElementById("chat-title");
const contextToggle = document.getElementById("context-toggle");
const contextPane = document.getElementById("context-pane");
const contextEntries = document.getElementById("context-entries");
const composer = document.querySelector(".composer");
const attachButton = document.getElementById("attach-button");
const imageInput = document.getElementById("image-input");
const previewStrip = document.getElementById("preview-strip");

const assistantsList = document.getElementById("assistants-list");
const newAssistantButton = document.getElementById("new-assistant");
const assistantModal = document.getElementById("assistant-modal");
const assistantForm = document.getElementById("assistant-form");
const assistantFormTitle = document.getElementById("assistant-form-title");
const assistantName = document.getElementById("assistant-name");
const assistantSystem = document.getElementById("assistant-system");
const assistantTemplate = document.getElementById("assistant-template");
const assistantDocument = document.getElementById("assistant-document");
const assistantDocInfo = document.getElementById("assistant-doc-info");
const assistantFormError = document.getElementById("assistant-form-error");
const assistantCancel = document.getElementById("assistant-cancel");

contextToggle.addEventListener("click", () => {
    contextPane.classList.toggle("hidden");
});

input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
    }
});

attachButton.addEventListener("click", () => imageInput.click());

imageInput.addEventListener("change", () => {
    addFiles(imageInput.files);
    imageInput.value = "";
});

composer.addEventListener("dragover", (event) => {
    event.preventDefault();
    composer.classList.add("drag-over");
});

composer.addEventListener("dragleave", () => {
    composer.classList.remove("drag-over");
});

composer.addEventListener("drop", (event) => {
    event.preventDefault();
    composer.classList.remove("drag-over");
    addFiles(event.dataTransfer.files);
});

function addFiles(files) {
    if (activeAssistantId !== null) {
        appendError("Images are not supported in assistant chats.");
        return;
    }
    for (const file of files) {
        if (!file.type.startsWith("image/")) {
            appendError(`"${file.name}" is not an image.`);
            continue;
        }
        if (file.size > MAX_IMAGE_BYTES) {
            appendError(`"${file.name}" is larger than 5 MB.`);
            continue;
        }
        const reader = new FileReader();
        reader.onload = () => {
            pendingImages.push({ dataUrl: reader.result, name: file.name, size: file.size });
            renderPreviews();
        };
        reader.readAsDataURL(file);
    }
}

function renderPreviews() {
    previewStrip.innerHTML = "";
    previewStrip.classList.toggle("hidden", pendingImages.length === 0);
    pendingImages.forEach((image, index) => {
        const item = document.createElement("div");
        item.className = "preview-item";

        const thumb = document.createElement("img");
        thumb.src = image.dataUrl;
        thumb.alt = image.name;

        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "preview-remove";
        remove.title = "Remove";
        remove.textContent = "x";
        remove.addEventListener("click", () => {
            pendingImages.splice(index, 1);
            renderPreviews();
        });

        item.append(thumb, remove);
        previewStrip.appendChild(item);
    });
}

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const content = input.value.trim();
    const assistantMode = activeAssistantId !== null;
    if (!content && (assistantMode || pendingImages.length === 0)) return;

    const images = assistantMode ? [] : pendingImages.map((image) => image.dataUrl);
    input.value = "";
    pendingImages.length = 0;
    renderPreviews();

    const userMessage = { role: "user", content };
    if (images.length > 0) userMessage.images = images;
    messages.push(userMessage);
    appendMessage("user", content, images);

    sendButton.disabled = true;

    const { element, update } = createStreamingMessage();
    let assistantText = "";

    const url = assistantMode
        ? `/api/assistants/${activeAssistantId}/chat/stream`
        : "/chat/stream";
    const body = assistantMode ? { message: content } : { messages };

    try {
        await streamChat(url, body, {
            onDelta: (delta) => {
                assistantText += delta;
                update(assistantText);
            },
            onDone: (payload, usage) => {
                element.classList.remove("streaming");
                update(assistantText);
                messages.push({ role: "assistant", content: assistantText });
                appendContextEntry(messages.length / 2, payload, usage);
            },
        });
    } catch (error) {
        element.classList.remove("streaming");
        if (element.childNodes.length === 0) element.remove();
        appendError(error.message);
    } finally {
        sendButton.disabled = false;
        input.focus();
    }
});

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
                onDone(parsed.payload_sent, parsed.usage || {});
            } else if (parsed.type === "error") {
                throw new Error(parsed.detail || "Stream error");
            }
        }
    }

    if (!finished) {
        // Connection closed before the server sent the final event;
        // keep whatever text already arrived instead of dropping it.
        onDone(null, {});
    }
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

// --- Assistants sidebar -------------------------------------------------

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

    const plain = document.createElement("div");
    plain.className = "assistant-item" + (activeAssistantId === null ? " active" : "");
    const plainName = document.createElement("div");
    plainName.className = "assistant-name";
    plainName.textContent = "Plain chat";
    const plainMeta = document.createElement("div");
    plainMeta.className = "assistant-meta";
    plainMeta.textContent = "No context, full history";
    plain.append(plainName, plainMeta);
    plain.addEventListener("click", () => switchTo(null));
    assistantsList.appendChild(plain);

    for (const assistant of assistants) {
        const item = document.createElement("div");
        item.className = "assistant-item" + (assistant.id === activeAssistantId ? " active" : "");

        const name = document.createElement("div");
        name.className = "assistant-name";
        name.textContent = assistant.name;

        const meta = document.createElement("div");
        meta.className = "assistant-meta";
        meta.textContent = `${assistant.document_name} (${assistant.document_chars.toLocaleString()} chars)`;

        const actions = document.createElement("div");
        actions.className = "assistant-actions";

        const edit = document.createElement("button");
        edit.type = "button";
        edit.textContent = "Edit";
        edit.addEventListener("click", (event) => {
            event.stopPropagation();
            openEditModal(assistant.id);
        });

        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "Delete";
        remove.addEventListener("click", (event) => {
            event.stopPropagation();
            deleteAssistant(assistant);
        });

        actions.append(edit, remove);
        item.append(name, meta, actions);
        item.addEventListener("click", () => switchTo(assistant.id));
        assistantsList.appendChild(item);
    }
}

function switchTo(assistantId) {
    if (assistantId === activeAssistantId) return;
    activeAssistantId = assistantId;

    messages.length = 0;
    pendingImages.length = 0;
    renderPreviews();
    messagesEl.innerHTML = "";
    contextEntries.innerHTML = "";

    const assistant = assistants.find((candidate) => candidate.id === assistantId);
    chatTitle.textContent = assistant ? assistant.name : "EASY-CHATGPT";
    attachButton.classList.toggle("hidden", assistant !== undefined);
    input.placeholder = assistant
        ? `Ask about ${assistant.document_name}...`
        : "Type a message... (or drop images)";

    renderAssistantsList();
    input.focus();
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
    if (activeAssistantId === assistant.id) switchTo(null);
    await loadAssistants();
}

// --- Assistant create/edit modal ----------------------------------------

newAssistantButton.addEventListener("click", () => openNewModal());
assistantCancel.addEventListener("click", () => closeModal());
assistantModal.addEventListener("click", (event) => {
    if (event.target === assistantModal) closeModal();
});

function openNewModal() {
    editingAssistantId = null;
    assistantFormTitle.textContent = "New assistant";
    assistantName.value = "";
    assistantSystem.value = "";
    assistantTemplate.value = DEFAULT_TEMPLATE;
    assistantDocument.value = "";
    assistantDocument.required = true;
    assistantDocInfo.classList.add("hidden");
    clearFormError();
    assistantModal.classList.remove("hidden");
    assistantName.focus();
}

async function openEditModal(assistantId) {
    let assistant;
    try {
        const response = await fetch(`/api/assistants/${assistantId}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        assistant = await response.json();
    } catch (error) {
        appendError(`Could not load assistant: ${error.message}`);
        return;
    }
    editingAssistantId = assistantId;
    assistantFormTitle.textContent = "Edit assistant";
    assistantName.value = assistant.name;
    assistantSystem.value = assistant.system_prompt;
    assistantTemplate.value = assistant.prompt_template;
    assistantDocument.value = "";
    assistantDocument.required = false;
    assistantDocInfo.textContent =
        `Current document: ${assistant.document_name} ` +
        `(${assistant.document_chars.toLocaleString()} chars). ` +
        "Pick a new file to replace it, or leave empty to keep it.";
    assistantDocInfo.classList.remove("hidden");
    clearFormError();
    assistantModal.classList.remove("hidden");
    assistantName.focus();
}

function closeModal() {
    assistantModal.classList.add("hidden");
    editingAssistantId = null;
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

function looksLikeTextFile(file) {
    return file.type.startsWith("text/") || file.name.toLowerCase().endsWith(".txt");
}

assistantForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearFormError();

    const file = assistantDocument.files[0];
    if (!editingAssistantId && !file) {
        showFormError("Choose a document file.");
        return;
    }
    if (file && !looksLikeTextFile(file)) {
        showFormError(`"${file.name}" is not a plain-text file.`);
        return;
    }

    const formData = new FormData();
    formData.append("name", assistantName.value);
    formData.append("system_prompt", assistantSystem.value);
    formData.append("prompt_template", assistantTemplate.value);
    if (file) formData.append("document", file);

    const saveButton = document.getElementById("assistant-save");
    saveButton.disabled = true;
    try {
        const response = await fetch(
            editingAssistantId ? `/api/assistants/${editingAssistantId}` : "/api/assistants",
            { method: editingAssistantId ? "PUT" : "POST", body: formData },
        );
        if (!response.ok) {
            const data = await response.json().catch(() => null);
            showFormError(detailFromError(data) || `HTTP ${response.status}`);
            return;
        }
        const saved = await response.json();
        const keepActive = editingAssistantId === activeAssistantId;
        closeModal();
        await loadAssistants();
        if (!keepActive) switchTo(saved.id);
    } catch (error) {
        showFormError(error.message);
    } finally {
        saveButton.disabled = false;
    }
});

// --- Chat rendering -------------------------------------------------------

function appendMessage(role, text, images = []) {
    const el = document.createElement("div");
    el.className = `message ${role}`;

    for (const dataUrl of images) {
        const thumb = document.createElement("img");
        thumb.src = dataUrl;
        thumb.className = "attachment";
        thumb.addEventListener("click", () => window.open(dataUrl, "_blank"));
        el.appendChild(thumb);
    }

    if (text) {
        const body = document.createElement("div");
        body.textContent = text;
        el.appendChild(body);
    }

    messagesEl.appendChild(el);
    messagesEl.scrollTop = messagesEl.scrollHeight;
}

function createStreamingMessage() {
    const element = document.createElement("div");
    element.className = "message assistant streaming";
    messagesEl.appendChild(element);

    const update = (markdown) => {
        element.innerHTML = marked.parse(markdown);
        messagesEl.scrollTop = messagesEl.scrollHeight;
    };

    return { element, update };
}

function appendError(text) {
    const el = document.createElement("div");
    el.className = "message error";
    el.textContent = `Error: ${text}`;
    messagesEl.appendChild(el);
    messagesEl.scrollTop = messagesEl.scrollHeight;
}

function appendContextEntry(turn, payload, usage) {
    const entry = document.createElement("div");
    entry.className = "context-entry";

    const title = document.createElement("h3");
    title.textContent = `Turn ${turn}`;

    const usageEl = document.createElement("div");
    usageEl.className = "usage";
    usageEl.innerHTML =
        `<span>prompt: ${usage.prompt_tokens ?? "?"}</span>` +
        `<span>completion: ${usage.completion_tokens ?? "?"}</span>` +
        `<span>total: ${usage.total_tokens ?? "?"}</span>`;

    const pre = document.createElement("pre");
    pre.textContent = payload
        ? JSON.stringify(payload, null, 2)
        : "(stream interrupted before completion)";

    entry.append(title, usageEl, pre);
    contextEntries.appendChild(entry);
    contextEntries.scrollTop = contextEntries.scrollHeight;
}

loadAssistants();
