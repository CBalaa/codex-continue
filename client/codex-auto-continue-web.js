const authPanel = document.getElementById("authPanel");
const consolePanel = document.getElementById("consolePanel");
const loginForm = document.getElementById("loginForm");
const loginError = document.getElementById("loginError");
const passwordInput = document.getElementById("passwordInput");
const logoutButton = document.getElementById("logoutButton");
const listenUrl = document.getElementById("listenUrl");
const machineBadge = document.getElementById("machineBadge");
const activeTabBadge = document.getElementById("activeTabBadge");
const streamState = document.getElementById("streamState");
const machinePanel = document.getElementById("machinePanel");
const machinePanelHint = document.getElementById("machinePanelHint");
const machineStateText = document.getElementById("machineStateText");
const machineConnectForm = document.getElementById("machineConnectForm");
const machineKeyInput = document.getElementById("machineKeyInput");
const machineConnectButton = document.getElementById("machineConnectButton");
const machineDisconnectButton = document.getElementById("machineDisconnectButton");
const machineNameValue = document.getElementById("machineNameValue");
const machineStatusValue = document.getElementById("machineStatusValue");
const machineKeyHintValue = document.getElementById("machineKeyHintValue");
const machineLastSeenValue = document.getElementById("machineLastSeenValue");
const machineError = document.getElementById("machineError");
const tabPanelHint = document.getElementById("tabPanelHint");
const tabList = document.getElementById("tabList");
const createForm = document.getElementById("createForm");
const createTabButton = document.getElementById("createTabButton");
const createError = document.getElementById("createError");
const emptyConsoleState = document.getElementById("emptyConsoleState");
const emptyConsoleText = document.getElementById("emptyConsoleText");
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
let pendingSelectNewest = false;

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

function lifecycleLabel(instance) {
  const state = instance?.lifecycle_state;
  if (state === "starting") return "启动中";
  if (state === "stopping") return "停止中";
  if (state === "failed") return "失败";
  if (state === "exited") return "已退出";
  if (instance?.connected) return modeLabel(instance.mode);
  return "离线";
}

function instanceVisualState(instance) {
  if (!instance) return "offline";
  if (instance.lifecycle_state === "starting") return "starting";
  if (instance.lifecycle_state === "stopping") return "stopping";
  if (instance.lifecycle_state === "failed") return "failed";
  if (instance.lifecycle_state === "exited") return "offline";
  if (!instance.connected) return "offline";
  return instance.status === "executing" || instance.turn_in_flight ? "running" : "idle";
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

function isRemoteServerSnapshot(value = snapshot) {
  return Boolean(
    value &&
      typeof value === "object" &&
      Object.prototype.hasOwnProperty.call(value, "attached_machine"),
  );
}

function attachedMachine() {
  if (!isRemoteServerSnapshot()) {
    return null;
  }
  return snapshot.attached_machine || null;
}

function machineReady() {
  if (!snapshot) {
    return false;
  }
  if (!isRemoteServerSnapshot()) {
    return true;
  }
  const machine = attachedMachine();
  return Boolean(machine && machine.connected);
}

function instances(value = snapshot) {
  return value?.instances || value?.attached_instances || [];
}

function selectedInstance() {
  return instances().find((item) => item.instance_id === activeInstanceId) || null;
}

function selectFallbackInstance() {
  const currentInstances = instances();
  if (!currentInstances.length) {
    activeInstanceId = null;
    return;
  }
  if (!selectedInstance()) {
    activeInstanceId = currentInstances[0].instance_id;
  }
}

function applySnapshot(nextSnapshot) {
  const previousInstanceIds = new Set(instances().map((item) => item.instance_id));
  snapshot = nextSnapshot;
  listenUrl.textContent = nextSnapshot.listen_url || window.location.origin;

  if (pendingSelectNewest) {
    const nextInstances = instances(nextSnapshot);
    const freshInstance = nextInstances.find((item) => !previousInstanceIds.has(item.instance_id));
    if (freshInstance) {
      activeInstanceId = freshInstance.instance_id;
      pendingSelectNewest = false;
    } else if (nextInstances.length > previousInstanceIds.size) {
      activeInstanceId = nextInstances[nextInstances.length - 1].instance_id;
      pendingSelectNewest = false;
    }
  }

  selectFallbackInstance();
  renderMachinePanel();
  renderCreateAvailability();
  renderTabs();
  renderSelectedInstance();
}

function renderMachinePanel() {
  if (!snapshot) {
    return;
  }

  if (!isRemoteServerSnapshot()) {
    machinePanel.hidden = true;
    machineBadge.hidden = true;
    tabPanelHint.textContent = "点击标签页切换实例；新建标签页会直接启动一个新的后台 Codex 会话。";
    emptyConsoleText.textContent = "先在上面新建一个标签页，或等待已运行的实例自动出现在这里。";
    return;
  }

  machinePanel.hidden = false;
  const machine = attachedMachine();
  if (!machine) {
    machinePanelHint.textContent = "输入本地 agent 启动时打印出来的机器 key。";
    machineStateText.textContent = "未连接";
    machineNameValue.textContent = "未连接";
    machineStatusValue.textContent = "--";
    machineKeyHintValue.textContent = "--";
    machineLastSeenValue.textContent = "--";
    machineBadge.hidden = true;
    machineDisconnectButton.hidden = true;
    machineDisconnectButton.disabled = true;
    machineKeyInput.disabled = false;
    machineConnectButton.disabled = false;
    tabPanelHint.textContent = "先连接机器，然后新建标签页。";
    emptyConsoleText.textContent = "先连接机器并新建一个标签页，或等待已运行的实例同步到这里。";
    return;
  }

  const online = machine.connected === true;
  machinePanelHint.textContent = online
    ? "机器已连接，可以继续创建和控制标签页。"
    : "机器已离线，等待 agent 重新连回，或断开后连接其它机器。";
  machineStateText.textContent = online ? "已连接" : "已离线";
  machineNameValue.textContent = machine.display_name || "Machine";
  machineStatusValue.textContent = online ? "在线" : "离线";
  machineKeyHintValue.textContent = machine.machine_key_hint || "--";
  machineLastSeenValue.textContent = machine.last_seen || "--";
  machineBadge.hidden = false;
  machineBadge.textContent = `${machine.display_name || "Machine"} · ${online ? "在线" : "离线"}`;
  machineDisconnectButton.hidden = false;
  machineDisconnectButton.disabled = false;
  machineKeyInput.disabled = true;
  machineConnectButton.disabled = true;
  tabPanelHint.textContent = online
    ? "点击标签页切换实例；新建标签页会直接启动一个新的后台 Codex 会话。"
    : "当前机器离线，暂时无法新建标签页。";
  emptyConsoleText.textContent = online
    ? "先在上面新建一个标签页，或等待已运行的实例同步到这里。"
    : "当前机器离线，等待 agent 恢复连接后才能继续。";
}

function renderCreateAvailability() {
  let enabled = false;
  let message = "";

  if (!snapshot) {
    enabled = false;
  } else if (isRemoteServerSnapshot()) {
    const machine = attachedMachine();
    if (!machine) {
      message = "先连接一台在线机器，才能新建 Codex 实例";
    } else if (!machine.connected) {
      message = "当前机器离线，无法新建 Codex 实例";
    } else if (snapshot.can_create_instances === false) {
      message = "当前机器未准备好 launch script，无法新建 Codex 实例";
    } else {
      enabled = true;
    }
  } else if (snapshot.can_create_instances === false) {
    message = "当前 manager 没有可用的 launch script，无法新建 Codex 实例";
  } else {
    enabled = true;
  }

  createTabButton.disabled = !enabled;
  if (message) {
    createError.textContent = message;
  } else if (
    createError.textContent.startsWith("当前") ||
    createError.textContent.startsWith("先连接")
  ) {
    createError.textContent = "";
  }
}

function renderTabs() {
  const currentInstances = instances();
  if (!currentInstances.length) {
    tabList.textContent = "暂无已连接标签页";
    tabList.className = "tab-list empty-state";
    return;
  }

  tabList.className = "tab-list";
  tabList.replaceChildren(
    ...currentInstances.map((instance) => {
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
      statusDot.className = `tab-status ${instanceVisualState(instance)}`;

      const label = document.createElement("span");
      label.className = "tab-label";
      label.textContent = instance.display_name || "Codex";

      const meta = document.createElement("span");
      meta.className = "tab-meta";
      meta.textContent = lifecycleLabel(instance);

      selectButton.append(statusDot, label, meta);

      const closeButton = document.createElement("button");
      closeButton.type = "button";
      closeButton.className = "tab-close";
      closeButton.textContent = "×";
      closeButton.title = instance.spawned_by_server ? "关闭实例" : "从网页中移除";
      closeButton.addEventListener("click", async (event) => {
        event.stopPropagation();
        try {
          await terminateInstance(instance.instance_id);
        } catch (error) {
          showCommandError(error);
        }
      });

      item.append(selectButton, closeButton);
      return item;
    }),
  );
}

function renderStatus(instance) {
  const state = instanceVisualState(instance);
  if (state === "running") {
    statusPill.textContent = "执行中";
    statusPill.className = "pill running";
    return;
  }
  if (state === "idle") {
    statusPill.textContent = "空闲";
    statusPill.className = "pill idle";
    return;
  }
  if (state === "starting") {
    statusPill.textContent = "启动中";
    statusPill.className = "pill starting";
    return;
  }
  if (state === "stopping") {
    statusPill.textContent = "停止中";
    statusPill.className = "pill stopping";
    return;
  }
  if (state === "failed") {
    statusPill.textContent = "失败";
    statusPill.className = "pill offline";
    return;
  }
  statusPill.textContent = "离线";
  statusPill.className = "pill offline";
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
  activeTabBadge.textContent = instance.display_name || "Codex";
  renderStatus(instance);

  modeValue.textContent = modeLabel(instance.mode);
  chatQueueValue.textContent = countLabel(instance.queued_chat_messages);
  autoTotalValue.textContent = countLabel(instance.remaining_total);
  currentTaskRemainingValue.textContent = countLabel(instance.current_task_remaining);

  const metaParts = [];
  if (instance.pid) {
    metaParts.push(`PID ${instance.pid}`);
  }
  metaParts.push(
    instance.connected
      ? `最后心跳 ${instance.last_seen || "--"}`
      : `${lifecycleLabel(instance)} · ${instance.last_seen || "--"}`,
  );
  instanceMeta.textContent = metaParts.join(" · ");

  if (instance.current_task_message) {
    currentTaskMessage.textContent = instance.current_task_message;
    currentTaskMessage.classList.remove("empty");
  } else if (instance.lifecycle_state === "starting") {
    currentTaskMessage.textContent = "Codex 正在启动，等待首轮状态上报";
    currentTaskMessage.classList.add("empty");
  } else {
    currentTaskMessage.textContent = "暂无 auto 任务";
    currentTaskMessage.classList.add("empty");
  }

  const assistant = instance.latest_assistant?.assistant;
  if (assistant) {
    latestReply.textContent = assistant;
    latestReply.classList.remove("empty");
  } else if (instance.launch_error) {
    latestReply.textContent = instance.launch_error;
    latestReply.classList.remove("empty");
  } else {
    latestReply.textContent = "暂无回复";
    latestReply.classList.add("empty");
  }

  renderEvents(instance);
  updateCommandAvailability(instance);
}

function renderEvents(instance) {
  const events = [...(instance.recent_events || [])];
  if (instance.launch_error) {
    events.unshift({
      kind: "control-error",
      timestamp: instance.last_seen || "",
      title: "实例错误",
      text: instance.launch_error,
    });
  }

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
      body.textContent =
        entry.kind === "assistant" && entry.assistant ? entry.assistant : entry.text || "";

      item.append(meta, title, body);
      return item;
    }),
  );
}

function updateCommandAvailability(instance) {
  const disabled = !instance || !instance.connected || !machineReady();
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
  pendingSelectNewest = false;
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  authPanel.hidden = false;
  consolePanel.hidden = true;
  logoutButton.hidden = true;
  machineBadge.hidden = true;
  activeTabBadge.hidden = true;
  activeTabBadge.textContent = "未选择标签页";
  streamState.textContent = "未连接";
  createError.textContent = "";
  machineError.textContent = "";
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
  if (data.snapshot) {
    applySnapshot(data.snapshot);
  }
}

async function terminateInstance(instanceId) {
  const response = await request("/api/terminate", {
    method: "POST",
    body: JSON.stringify({ instance_id: instanceId }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || "关闭实例失败");
  }
  if (data.snapshot) {
    applySnapshot(data.snapshot);
    return;
  }
  if (activeInstanceId === instanceId) {
    activeInstanceId = null;
  }
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

machineConnectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!isRemoteServerSnapshot()) {
    return;
  }

  machineError.textContent = "";
  const machineKey = machineKeyInput.value.trim();
  if (!machineKey) {
    machineError.textContent = "请输入机器 key";
    return;
  }

  const response = await request("/api/connect-machine", {
    method: "POST",
    body: JSON.stringify({ machine_key: machineKey }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    machineError.textContent = data.error || "连接机器失败";
    return;
  }

  machineError.textContent = "";
  machineKeyInput.value = "";
  applySnapshot(data.snapshot);
});

machineDisconnectButton.addEventListener("click", async () => {
  const response = await request("/api/disconnect-machine", {
    method: "POST",
    body: JSON.stringify({}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    machineError.textContent = data.error || "断开机器失败";
    return;
  }
  machineError.textContent = "";
  activeInstanceId = null;
  applySnapshot(data.snapshot);
});

createForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  createError.textContent = "";

  const response = await request("/api/instances", {
    method: "POST",
    body: JSON.stringify({}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    createError.textContent = data.error || "新建标签页失败";
    return;
  }

  if (data.instance_id) {
    activeInstanceId = data.instance_id;
  } else {
    pendingSelectNewest = true;
  }

  if (data.snapshot) {
    applySnapshot(data.snapshot);
  }
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
