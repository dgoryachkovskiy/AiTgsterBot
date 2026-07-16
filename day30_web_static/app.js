const SYSTEM_PROMPT = [
  "Ты Космический AI-наставник.",
  "Помогай с учебой, идеями, короткими объяснениями и планами.",
  "Отвечай по-русски, ясно и без длинных вступлений."
].join(" ");

const state = {
  apiKey: localStorage.getItem("day30_api_key") || "",
  messages: []
};

const apiKeyInput = document.querySelector("#apiKey");
const saveKeyButton = document.querySelector("#saveKey");
const serviceState = document.querySelector("#serviceState");
const modelName = document.querySelector("#modelName");
const rateLimit = document.querySelector("#rateLimit");
const messagesEl = document.querySelector("#messages");
const promptEl = document.querySelector("#prompt");
const chatForm = document.querySelector("#chatForm");
const sendButton = document.querySelector("#sendButton");

apiKeyInput.value = state.apiKey;

function headers() {
  return {
    "Authorization": `Bearer ${state.apiKey}`,
    "Content-Type": "application/json"
  };
}

function setStatus(text, ok = false) {
  serviceState.textContent = text;
  serviceState.style.color = ok ? "var(--green)" : "var(--amber)";
}

function renderMessages() {
  messagesEl.innerHTML = "";
  if (state.messages.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "Введите ключ, задайте вопрос и получите ответ локальной модели.";
    messagesEl.appendChild(empty);
    return;
  }
  for (const message of state.messages) {
    const bubble = document.createElement("div");
    bubble.className = `bubble ${message.role}`;
    bubble.textContent = message.content;
    messagesEl.appendChild(bubble);
  }
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function pushMessage(role, content) {
  state.messages.push({ role, content });
  renderMessages();
}

function showError(content) {
  const bubble = document.createElement("div");
  bubble.className = "bubble error";
  bubble.textContent = content;
  messagesEl.appendChild(bubble);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

async function refreshHealth() {
  if (!state.apiKey) {
    setStatus("ключ не задан");
    return;
  }
  try {
    const response = await fetch("/health", { headers: headers() });
    if (!response.ok) {
      setStatus(`ошибка ${response.status}`);
      return;
    }
    const data = await response.json();
    modelName.textContent = data.model || "unknown";
    rateLimit.textContent = `${data.limits.rate_limit_per_minute} / мин`;
    setStatus(data.ollama_connected ? "онлайн" : "нет Ollama", data.ollama_connected);
  } catch (error) {
    setStatus("нет связи");
  }
}

async function sendPrompt(prompt) {
  if (!state.apiKey) {
    showError("Сначала вставьте Private key.");
    return;
  }
  const text = prompt.trim();
  if (!text) return;

  pushMessage("user", text);
  promptEl.value = "";
  sendButton.disabled = true;
  const typing = document.createElement("div");
  typing.className = "bubble assistant typing";
  typing.textContent = "Наставник думает...";
  messagesEl.appendChild(typing);
  messagesEl.scrollTop = messagesEl.scrollHeight;

  const payloadMessages = [
    { role: "system", content: SYSTEM_PROMPT },
    ...state.messages.filter((item) => item.role === "user" || item.role === "assistant")
  ];

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ messages: payloadMessages, temperature: 0.2, num_predict: 220 })
    });
    typing.remove();
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof data.detail === "object" ? JSON.stringify(data.detail) : (data.detail || "unknown error");
      showError(`Ошибка ${response.status}: ${detail}`);
      return;
    }
    pushMessage("assistant", data.answer || "Пустой ответ модели.");
  } catch (error) {
    typing.remove();
    showError(`Ошибка сети: ${error.message}`);
  } finally {
    sendButton.disabled = false;
    promptEl.focus();
    refreshHealth();
  }
}

saveKeyButton.addEventListener("click", () => {
  state.apiKey = apiKeyInput.value.trim();
  localStorage.setItem("day30_api_key", state.apiKey);
  refreshHealth();
});

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  sendPrompt(promptEl.value);
});

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    promptEl.value = button.dataset.prompt || "";
    promptEl.focus();
  });
});

renderMessages();
refreshHealth();
