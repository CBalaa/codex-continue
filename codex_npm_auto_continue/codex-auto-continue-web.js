const authPanel = document.getElementById("authPanel");
const consolePanel = document.getElementById("consolePanel");
const loginForm = document.getElementById("loginForm");
const loginError = document.getElementById("loginError");
const passwordInput = document.getElementById("passwordInput");
const controlKeyInput = document.getElementById("controlKeyInput");
const logoutButton = document.getElementById("logoutButton");
const listenUrl = document.getElementById("listenUrl");
const bindingHint = document.getElementById("bindingHint");
const streamState = document.getElementById("streamState");
const statusPill = document.getElementById("statusPill");
const modeValue = document.getElementById("modeValue");
const chatQueueValue = document.getElementById("chatQueueValue");
const autoTotalValue = document.getElementById("autoTotalValue");
const currentTaskRemainingValue = document.getElementById("currentTaskRemainingValue");
const currentTaskMessage = document.getElementById("currentTaskMessage");
const latestReply = document.getElementById("latestReply");
const recentEvents = document.getElementById("recentEvents");
const chatForm = document.getElementById("chatForm");
const chatMessageInput = document.getElementById("chatMessageInput");
const autoForm = document.getElementById("autoForm");
const autoTasksInput = document.getElementById("autoTasksInput");
const stopAutoButton = document.getElementById("stopAutoButton");

let snapshot = null;
let eventSource = null;

function modeLabel(mode) {
  if (mode === "chat") return "chat";
  if (mode === "auto") return "auto";
  if (mode === "manual") return "manual";
  return "--";
}

function countLabel(value) {
  if (value === null || value === undefined) return "unlimited";
  return String(value);
}

function eventKindLabel(kind) {
  if (kind === "assistant") return "回复";
  if (kind === "control-response") return "回执";
  if (kind === "control-error") return "错误";
  return "状态";
}

function request(path, options = {}) {
  return fetch(path, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
}

async function loadState() {
  const response = await request("/api/state", { method: "GET" });
  if (response.status === 401) {
    setLoggedOut();
    return;
  }
  const data = await response.json();
  applySnapshot(data);
  setLoggedIn();
  openStream();
}

function applySnapshot(nextSnapshot) {
  snapshot = nextSnapshot;
  listenUrl.textContent = nextSnapshot.listen_url || window.location.origin;
  if (nextSnapshot.control_key_hint) {
    bindingHint.textContent = `Key ${nextSnapshot.control_key_hint}`;
    bindingHint.hidden = false;
  } else {
    bindingHint.textContent = "--";
    bindingHint.hidden = true;
  }
  const isRunning = nextSnapshot.status === "executing" || nextSnapshot.turn_in_flight;
  statusPill.textContent = isRunning ? "执行中" : "空闲";
  statusPill.className = `pill ${isRunning ? "running" : "idle"}`;
  modeValue.textContent = modeLabel(nextSnapshot.mode);
  chatQueueValue.textContent = countLabel(nextSnapshot.queued_chat_messages);
  autoTotalValue.textContent = countLabel(nextSnapshot.remaining_total);
  currentTaskRemainingValue.textContent = countLabel(nextSnapshot.current_task_remaining);

  if (nextSnapshot.current_task_message) {
    currentTaskMessage.textContent = nextSnapshot.current_task_message;
    currentTaskMessage.classList.remove("empty");
  } else {
    currentTaskMessage.textContent = "暂无 auto 任务";
    currentTaskMessage.classList.add("empty");
  }

  const assistant = nextSnapshot.latest_assistant?.assistant;
  if (assistant) {
    latestReply.textContent = assistant;
    latestReply.classList.remove("empty");
  } else {
    latestReply.textContent = "暂无回复";
    latestReply.classList.add("empty");
  }

  renderEvents(nextSnapshot.recent_events || []);
}

function renderEvents(events) {
  if (!events.length) {
    recentEvents.textContent = "暂无事件";
    recentEvents.className = "event-list empty-state";
    return;
  }

  recentEvents.className = "event-list";
  recentEvents.replaceChildren(
    ...events.map((entry) => {
      const item = document.createElement("article");
      item.className = "event-item";

      const meta = document.createElement("div");
      meta.className = "event-meta";

      const kind = document.createElement("span");
      kind.className = `event-kind ${entry.kind}`;
      kind.textContent = eventKindLabel(entry.kind);

      const time = document.createElement("span");
      time.textContent = entry.timestamp || "";

      meta.append(kind, time);

      const title = document.createElement("h3");
      title.className = "event-title";
      title.textContent = entry.title || "事件";

      const body = document.createElement("pre");
      body.className = "event-body";
      body.textContent = entry.kind === "assistant" && entry.assistant ? entry.assistant : entry.text || "";

      item.append(meta, title, body);
      return item;
    }),
  );
}

function setLoggedIn() {
  authPanel.hidden = true;
  consolePanel.hidden = false;
  logoutButton.hidden = false;
  loginError.textContent = "";
}

function setLoggedOut() {
  snapshot = null;
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  authPanel.hidden = false;
  consolePanel.hidden = true;
  logoutButton.hidden = true;
  bindingHint.hidden = true;
  bindingHint.textContent = "--";
  streamState.textContent = "未连接";
}

function openStream() {
  if (eventSource) {
    eventSource.close();
  }
  eventSource = new EventSource("/api/events", { withCredentials: true });
  streamState.textContent = "实时连接中";

  const handleSnapshot = (event) => {
    try {
      const payload = JSON.parse(event.data);
      if (payload.type === "snapshot" && payload.snapshot) {
        applySnapshot(payload.snapshot);
      }
    } catch (error) {
      console.error(error);
    }
  };

  eventSource.addEventListener("snapshot", handleSnapshot);
  eventSource.onmessage = handleSnapshot;
  eventSource.onerror = async () => {
    streamState.textContent = "实时连接重试中";
    try {
      await loadState();
    } catch {
      setLoggedOut();
    }
  };
}

async function submitCommand(payload) {
  const response = await request("/api/command", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || "请求失败");
  }
}

function showCommandError(error) {
  window.alert(error instanceof Error ? error.message : "请求失败");
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginError.textContent = "";
  const password = passwordInput.value.trim();
  const controlKey = controlKeyInput.value.trim();
  if (!password || !controlKey) {
    loginError.textContent = "请输入密码和 control key";
    return;
  }

  const response = await request("/login", {
    method: "POST",
    body: JSON.stringify({ password, control_key: controlKey }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    loginError.textContent = data.error || "登录失败";
    return;
  }

  passwordInput.value = "";
  controlKeyInput.value = "";
  applySnapshot(data.snapshot);
  setLoggedIn();
  openStream();
});

logoutButton.addEventListener("click", async () => {
  await request("/logout", { method: "POST", body: JSON.stringify({}) });
  setLoggedOut();
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = chatMessageInput.value.trim();
  if (!message) {
    return;
  }
  try {
    await submitCommand({
      sender: "user",
      mode: "chat",
      messages: [message],
    });
    chatMessageInput.value = "";
  } catch (error) {
    showCommandError(error);
  }
});

autoForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  let tasks;
  try {
    tasks = JSON.parse(autoTasksInput.value);
  } catch {
    window.alert("任务队列 JSON 解析失败");
    return;
  }
  try {
    await submitCommand({
      sender: "user",
      mode: "auto",
      tasks,
    });
  } catch (error) {
    showCommandError(error);
  }
});

stopAutoButton.addEventListener("click", async () => {
  try {
    await submitCommand({
      sender: "user",
      command: "stop_auto",
    });
  } catch (error) {
    showCommandError(error);
  }
});

loadState().catch(() => {
  setLoggedOut();
});
