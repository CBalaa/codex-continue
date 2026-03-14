const authPanel = document.getElementById("authPanel");
const consolePanel = document.getElementById("consolePanel");
const loginForm = document.getElementById("loginForm");
const loginError = document.getElementById("loginError");
const passwordInput = document.getElementById("passwordInput");
const logoutButton = document.getElementById("logoutButton");
const listenUrl = document.getElementById("listenUrl");
const activeTabBadge = document.getElementById("activeTabBadge");
const streamState = document.getElementById("streamState");
const tabList = document.getElementById("tabList");
const attachForm = document.getElementById("attachForm");
const attachKeyInput = document.getElementById("attachKeyInput");
const attachError = document.getElementById("attachError");
const emptyConsoleState = document.getElementById("emptyConsoleState");
const instancePanels = document.getElementById("instancePanels");
const statusPill = document.getElementById("statusPill");
const modeValue = document.getElementById("modeValue");
const chatQueueValue = document.getElementById("chatQueueValue");
const autoTotalValue = document.getElementById("autoTotalValue");
const currentTaskRemainingValue = document.getElementById("currentTaskRemainingValue");
const currentTaskMessage = document.getElementById("currentTaskMessage");
const instanceMeta = document.getElementById("instanceMeta");
const latestReply = document.getElementById("latestReply");
const recentEvents = document.getElementById("recentEvents");
const chatForm = document.getElementById("chatForm");
const chatMessageInput = document.getElementById("chatMessageInput");
const chatSubmitButton = document.getElementById("chatSubmitButton");
const autoForm = document.getElementById("autoForm");
const autoTasksInput = document.getElementById("autoTasksInput");
const autoSubmitButton = document.getElementById("autoSubmitButton");
const stopAutoButton = document.getElementById("stopAutoButton");

let snapshot = null;
let activeInstanceId = null;
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

function attachedInstances() {
  return snapshot?.attached_instances || [];
}

function selectedInstance() {
  return attachedInstances().find((item) => item.instance_id === activeInstanceId) || null;
}

function selectFallbackInstance() {
  const instances = attachedInstances();
  if (!instances.length) {
    activeInstanceId = null;
    return;
  }
  if (!selectedInstance()) {
    activeInstanceId = instances[0].instance_id;
  }
}

function applySnapshot(nextSnapshot) {
  snapshot = nextSnapshot;
  listenUrl.textContent = nextSnapshot.listen_url || window.location.origin;
  selectFallbackInstance();
  renderTabs();
  renderSelectedInstance();
}

function renderTabs() {
  const instances = attachedInstances();
  if (!instances.length) {
    tabList.textContent = "暂无已连接标签页";
    tabList.className = "tab-list empty-state";
    return;
  }

  tabList.className = "tab-list";
  tabList.replaceChildren(
    ...instances.map((instance) => {
      const item = document.createElement("div");
      item.className = `tab-item ${instance.instance_id === activeInstanceId ? "active" : ""}`;

      const selectButton = document.createElement("button");
      selectButton.type = "button";
      selectButton.className = "tab-button";
      selectButton.addEventListener("click", () => {
        activeInstanceId = instance.instance_id;
        renderTabs();
        renderSelectedInstance();
      });

      const statusDot = document.createElement("span");
      statusDot.className = `tab-status ${instance.connected ? (instance.turn_in_flight ? "running" : "idle") : "offline"}`;

      const label = document.createElement("span");
      label.className = "tab-label";
      label.textContent = instance.display_name || `Codex ${instance.control_key_hint || "--"}`;

      const meta = document.createElement("span");
      meta.className = "tab-meta";
      meta.textContent = instance.connected ? modeLabel(instance.mode) : "offline";

      selectButton.append(statusDot, label, meta);

      const closeButton = document.createElement("button");
      closeButton.type = "button";
      closeButton.className = "tab-close";
      closeButton.textContent = "×";
      closeButton.title = "关闭标签页";
      closeButton.addEventListener("click", async (event) => {
        event.stopPropagation();
        try {
          await detachInstance(instance.instance_id);
        } catch (error) {
          showCommandError(error);
        }
      });

      item.append(selectButton, closeButton);
      return item;
    }),
  );
}

function renderSelectedInstance() {
  const instance = selectedInstance();
  const hasInstance = Boolean(instance);
  emptyConsoleState.hidden = hasInstance;
  instancePanels.hidden = !hasInstance;

  if (!instance) {
    activeTabBadge.hidden = true;
    activeTabBadge.textContent = "未选择标签页";
    updateCommandAvailability(null);
    return;
  }

  activeTabBadge.hidden = false;
  activeTabBadge.textContent = instance.display_name || `Codex ${instance.control_key_hint || "--"}`;

  if (!instance.connected) {
    statusPill.textContent = "离线";
    statusPill.className = "pill offline";
  } else {
    const isRunning = instance.status === "executing" || instance.turn_in_flight;
    statusPill.textContent = isRunning ? "执行中" : "空闲";
    statusPill.className = `pill ${isRunning ? "running" : "idle"}`;
  }

  modeValue.textContent = modeLabel(instance.mode);
  chatQueueValue.textContent = countLabel(instance.queued_chat_messages);
  autoTotalValue.textContent = countLabel(instance.remaining_total);
  currentTaskRemainingValue.textContent = countLabel(instance.current_task_remaining);
  instanceMeta.textContent = instance.connected
    ? `最后心跳 ${instance.last_seen || "--"}`
    : `实例已离线，最后心跳 ${instance.last_seen || "--"}`;

  if (instance.current_task_message) {
    currentTaskMessage.textContent = instance.current_task_message;
    currentTaskMessage.classList.remove("empty");
  } else {
    currentTaskMessage.textContent = "暂无 auto 任务";
    currentTaskMessage.classList.add("empty");
  }

  const assistant = instance.latest_assistant?.assistant;
  if (assistant) {
    latestReply.textContent = assistant;
    latestReply.classList.remove("empty");
  } else {
    latestReply.textContent = "暂无回复";
    latestReply.classList.add("empty");
  }

  renderEvents(instance.recent_events || []);
  updateCommandAvailability(instance);
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

function updateCommandAvailability(instance) {
  const disabled = !instance || !instance.connected;
  chatMessageInput.disabled = disabled;
  chatSubmitButton.disabled = disabled;
  autoTasksInput.disabled = disabled;
  autoSubmitButton.disabled = disabled;
  stopAutoButton.disabled = disabled;
}

function setLoggedIn() {
  authPanel.hidden = true;
  consolePanel.hidden = false;
  logoutButton.hidden = false;
  loginError.textContent = "";
}

function setLoggedOut() {
  snapshot = null;
  activeInstanceId = null;
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  authPanel.hidden = false;
  consolePanel.hidden = true;
  logoutButton.hidden = true;
  activeTabBadge.hidden = true;
  activeTabBadge.textContent = "未选择标签页";
  streamState.textContent = "未连接";
  attachError.textContent = "";
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
  const instance = selectedInstance();
  if (!instance || !instance.connected) {
    throw new Error("当前没有可用的 Codex 标签页");
  }

  const response = await request("/api/command", {
    method: "POST",
    body: JSON.stringify({
      instance_id: instance.instance_id,
      ...payload,
    }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || "请求失败");
  }
}

async function detachInstance(instanceId) {
  const response = await request("/api/detach", {
    method: "POST",
    body: JSON.stringify({ instance_id: instanceId }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || "关闭标签页失败");
  }
  if (activeInstanceId === instanceId) {
    activeInstanceId = null;
  }
  applySnapshot(data.snapshot);
}

function showCommandError(error) {
  window.alert(error instanceof Error ? error.message : "请求失败");
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginError.textContent = "";
  const password = passwordInput.value.trim();
  if (!password) {
    loginError.textContent = "请输入密码";
    return;
  }

  const response = await request("/login", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    loginError.textContent = data.error || "登录失败";
    return;
  }

  passwordInput.value = "";
  applySnapshot(data.snapshot);
  setLoggedIn();
  openStream();
});

logoutButton.addEventListener("click", async () => {
  await request("/logout", { method: "POST", body: JSON.stringify({}) });
  setLoggedOut();
});

attachForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  attachError.textContent = "";
  const controlKey = attachKeyInput.value.trim();
  if (!controlKey) {
    attachError.textContent = "请输入 control key";
    return;
  }

  const response = await request("/api/attach", {
    method: "POST",
    body: JSON.stringify({ control_key: controlKey }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    attachError.textContent = data.error || "绑定标签页失败";
    return;
  }

  attachKeyInput.value = "";
  activeInstanceId = data.instance_id || activeInstanceId;
  applySnapshot(data.snapshot);
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
