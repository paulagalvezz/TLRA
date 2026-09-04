const messages = [];
const pendingImages = [];
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

const form = document.getElementById("chat-form");
const input = document.getElementById("user-input");
const sendButton = document.getElementById("send-button");
const messagesEl = document.getElementById("messages");
const contextToggle = document.getElementById("context-toggle");
const contextPane = document.getElementById("context-pane");
const contextEntries = document.getElementById("context-entries");
const composer = document.querySelector(".composer");
const attachButton = document.getElementById("attach-button");
const imageInput = document.getElementById("image-input");
const previewStrip = document.getElementById("preview-strip");

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
    if (!content && pendingImages.length === 0) return;

    const images = pendingImages.map((image) => image.dataUrl);
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

    try {
        await streamChat(messages, {
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

async function streamChat(history, { onDelta, onDone }) {
    const response = await fetch("/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: history }),
    });

    if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try {
            const data = await response.json();
            detail = data.detail || detail;
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
