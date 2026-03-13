#!/usr/bin/env node

import { spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const REAL_LAUNCHER = path.join(__dirname, "codex.real.js");
const PTY_HELPER = path.join(__dirname, "codex-auto-continue-pty.py");
const WRAPPER_AUTO_FLAG = "--auto-mode";
const WRAPPER_CHAT_FLAG = "--chat-mode";
const WRAPPER_NATIVE_FLAG = "--native";
const REMOVED_WRAPPER_AUTO_FLAG = "--auto-continue";
const REMOVED_WRAPPER_NO_AUTO_FLAG = "--no-auto-continue";
const WRAPPER_PROMPT_FLAG = "--auto-continue-prompt";
const WRAPPER_LIMIT_FLAG = "--auto-continue-limit";
const WRAPPER_NTFY_TOPIC_FLAG = "--auto-continue-ntfy-topic";
const REMOVED_WRAPPER_NTFY_CONTROL_TOPIC_FLAG = "--auto-continue-ntfy-control-topic";
const WRAPPER_NTFY_BASE_URL_FLAG = "--auto-continue-ntfy-base-url";
const WRAPPER_NOTIFY_TIMEOUT_MS_FLAG = "--auto-continue-notify-timeout-ms";
const REMOVED_NTFY_TOPIC_ENV = "CODEX_AUTO_CONTINUE_NTFY_TOPIC";
const REMOVED_NTFY_CONTROL_TOPIC_ENV = "CODEX_AUTO_CONTINUE_NTFY_CONTROL_TOPIC";
const DEFAULT_NTFY_BASE_URL = "https://ntfy.sh";
const DEFAULT_NOTIFY_TIMEOUT_MS = 3000;
const COMMENT_TOPIC_KEYS = [
  "codex-remote-ntfy-topic",
  "codex-remote-topic",
  "codex-auto-continue-ntfy-topic",
];
const COMMENT_BASE_URL_KEYS = [
  "codex-remote-ntfy-base-url",
  "codex-remote-base-url",
  "codex-auto-continue-ntfy-base-url",
];
const COMMENT_TIMEOUT_KEYS = [
  "codex-remote-notify-timeout-ms",
  "codex-remote-timeout-ms",
  "codex-auto-continue-notify-timeout-ms",
];
const DEBUG_LOG_PATH =
  process.env.CODEX_AUTO_CONTINUE_DEBUG_LOG ||
  path.join(os.tmpdir(), "codex-auto-continue-debug.log");
const NON_INTERACTIVE_SUBCOMMANDS = new Set([
  "exec",
  "review",
  "mcp",
  "mcp-server",
  "proto",
  "app-server",
  "completion",
  "sandbox",
  "debug",
  "execpolicy",
  "apply",
  "cloud",
  "cloud-tasks",
  "features",
  "login",
  "logout",
]);

function isHelpOrVersionArg(arg) {
  return arg === "--help" || arg === "-h" || arg === "--version" || arg === "-V";
}

function parsePositiveInteger(flagName, value) {
  if (!/^[1-9]\d*$/.test(value)) {
    throw new Error(`${flagName} must be a positive integer`);
  }

  return Number.parseInt(value, 10);
}

function normalizeOptionalString(value) {
  if (typeof value !== "string") {
    return null;
  }

  const trimmed = value.trim();
  return trimmed.length === 0 ? null : trimmed;
}

function validateNtfyBaseUrl(flagName, value) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error(`${flagName} must be a valid http(s) URL`);
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error(`${flagName} must be a valid http(s) URL`);
  }

  return value;
}

function parsePositiveIntegerEnv(varName) {
  const value = normalizeOptionalString(process.env[varName]);
  if (value === null) {
    return null;
  }

  return parsePositiveInteger(varName, value);
}

function parseQuotedString(value) {
  const trimmed = value.trim();
  if (trimmed.length === 0) {
    return "";
  }

  if (trimmed.startsWith('"') && trimmed.endsWith('"')) {
    try {
      return JSON.parse(trimmed);
    } catch {
      return trimmed.slice(1, -1);
    }
  }

  if (trimmed.startsWith("'") && trimmed.endsWith("'")) {
    return trimmed.slice(1, -1);
  }

  return trimmed;
}

function expandHomePath(value) {
  if (value === "~") {
    return os.homedir();
  }
  if (value.startsWith("~/") || value.startsWith("~\\")) {
    return path.join(os.homedir(), value.slice(2));
  }
  return value;
}

function parseConfigAssignment(raw) {
  const equalsIndex = raw.indexOf("=");
  if (equalsIndex < 0) {
    return null;
  }

  return {
    key: raw.slice(0, equalsIndex).trim(),
    value: raw.slice(equalsIndex + 1),
  };
}

function resolveCodexConfigPath(passthrough) {
  let configFileOverride = null;

  for (let index = 0; index < passthrough.length; index += 1) {
    const arg = passthrough[index];
    let assignment = null;

    if (arg === "-c" || arg === "--config") {
      assignment = passthrough[index + 1];
      if (assignment === undefined) {
        break;
      }
      index += 1;
    } else if (arg.startsWith("--config=")) {
      assignment = arg.slice("--config=".length);
    }

    if (assignment === null) {
      continue;
    }

    const parsed = parseConfigAssignment(assignment);
    if (parsed === null || parsed.key !== "config_file") {
      continue;
    }

    const value = normalizeOptionalString(parseQuotedString(parsed.value));
    if (value !== null) {
      configFileOverride = value;
    }
  }

  if (configFileOverride !== null) {
    return path.resolve(expandHomePath(configFileOverride));
  }

  const codexHome = normalizeOptionalString(process.env.CODEX_HOME);
  if (codexHome !== null) {
    return path.resolve(expandHomePath(codexHome), "config.toml");
  }

  return path.join(os.homedir(), ".codex", "config.toml");
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function findConfigSetting(fileText, keys) {
  for (const key of keys) {
    const match = new RegExp(
      `^\\s*(?:#\\s*)?${escapeRegExp(key)}\\s*=\\s*(.+?)\\s*$`,
      "m",
    ).exec(fileText);
    if (match) {
      return {
        key,
        rawValue: match[1].trim(),
      };
    }
  }

  return null;
}

function parseCommentStringSetting(configPath, setting) {
  const value = normalizeOptionalString(parseQuotedString(setting.rawValue));
  if (value === null) {
    throw new Error(
      `Invalid ${setting.key} in ${configPath}; expected a non-empty string.`,
    );
  }
  return value;
}

function parseCommentIntegerSetting(configPath, setting) {
  try {
    return parsePositiveInteger(setting.key, parseQuotedString(setting.rawValue));
  } catch {
    throw new Error(
      `Invalid ${setting.key} in ${configPath}; expected a positive integer.`,
    );
  }
}

function resolveNtfySettings(parsed) {
  if (normalizeOptionalString(process.env[REMOVED_NTFY_CONTROL_TOPIC_ENV]) !== null) {
    throw new Error(
      `${REMOVED_NTFY_CONTROL_TOPIC_ENV} has been removed; configure the topic in config.toml comments instead.`,
    );
  }
  if (normalizeOptionalString(process.env[REMOVED_NTFY_TOPIC_ENV]) !== null) {
    throw new Error(
      `${REMOVED_NTFY_TOPIC_ENV} has been removed; configure the topic in config.toml comments instead.`,
    );
  }

  const configPath = resolveCodexConfigPath(parsed.passthrough);
  if (!existsSync(configPath)) {
    return {
      configExists: false,
      configPath,
      ntfyBaseUrl: null,
      ntfyTopic: null,
      notifyTimeoutMs: null,
    };
  }

  const fileText = readFileSync(configPath, "utf8");
  const topicSetting = findConfigSetting(fileText, COMMENT_TOPIC_KEYS);
  const ntfyTopic =
    topicSetting === null ? null : parseCommentStringSetting(configPath, topicSetting);
  if (ntfyTopic === null) {
    return {
      configExists: true,
      configPath,
      ntfyBaseUrl: null,
      ntfyTopic: null,
      notifyTimeoutMs: null,
    };
  }

  const baseUrlSetting = findConfigSetting(fileText, COMMENT_BASE_URL_KEYS);
  const timeoutSetting = findConfigSetting(fileText, COMMENT_TIMEOUT_KEYS);
  const ntfyBaseUrl = validateNtfyBaseUrl(
    parsed.autoContinueNtfyBaseUrl === null
      ? "CODEX_AUTO_CONTINUE_NTFY_BASE_URL"
      : WRAPPER_NTFY_BASE_URL_FLAG,
    parsed.autoContinueNtfyBaseUrl ??
      normalizeOptionalString(process.env.CODEX_AUTO_CONTINUE_NTFY_BASE_URL) ??
      (baseUrlSetting === null
        ? null
        : parseCommentStringSetting(configPath, baseUrlSetting)) ??
      DEFAULT_NTFY_BASE_URL,
  );
  const notifyTimeoutMs =
    parsed.autoContinueNotifyTimeoutMs ??
    parsePositiveIntegerEnv("CODEX_AUTO_CONTINUE_NOTIFY_TIMEOUT_MS") ??
    (timeoutSetting === null
      ? null
      : parseCommentIntegerSetting(configPath, timeoutSetting)) ??
    DEFAULT_NOTIFY_TIMEOUT_MS;

  return {
    configExists: true,
    configPath,
    ntfyBaseUrl,
    ntfyTopic,
    notifyTimeoutMs,
  };
}

function parseWrapperArgs(argv) {
  const passthrough = [];
  let launchMode = null;
  let autoPrompt = "继续";
  let autoLimit = null;
  let autoContinueNtfyBaseUrl = null;
  let autoContinueNotifyTimeoutMs = null;

  function setLaunchMode(nextMode, flagName) {
    if (launchMode !== null && launchMode !== nextMode) {
      throw new Error(
        `${flagName} cannot be combined with another launch mode flag.`,
      );
    }
    launchMode = nextMode;
  }

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];

    if (arg === WRAPPER_AUTO_FLAG) {
      setLaunchMode("auto", WRAPPER_AUTO_FLAG);
      continue;
    }

    if (arg === WRAPPER_CHAT_FLAG) {
      setLaunchMode("chat", WRAPPER_CHAT_FLAG);
      continue;
    }

    if (arg === WRAPPER_NATIVE_FLAG) {
      setLaunchMode("native", WRAPPER_NATIVE_FLAG);
      continue;
    }

    if (arg === REMOVED_WRAPPER_AUTO_FLAG) {
      throw new Error(`${REMOVED_WRAPPER_AUTO_FLAG} has been removed; use ${WRAPPER_AUTO_FLAG}.`);
    }

    if (arg === REMOVED_WRAPPER_NO_AUTO_FLAG) {
      throw new Error(
        `${REMOVED_WRAPPER_NO_AUTO_FLAG} has been removed; use ${WRAPPER_NATIVE_FLAG}.`,
      );
    }

    if (arg === WRAPPER_PROMPT_FLAG) {
      const value = argv[index + 1];
      if (value === undefined) {
        throw new Error(`${WRAPPER_PROMPT_FLAG} requires a value`);
      }
      autoPrompt = value;
      index += 1;
      continue;
    }

    if (arg === WRAPPER_LIMIT_FLAG) {
      const value = argv[index + 1];
      if (value === undefined) {
        throw new Error(`${WRAPPER_LIMIT_FLAG} requires a value`);
      }

      autoLimit = parsePositiveInteger(WRAPPER_LIMIT_FLAG, value);
      index += 1;
      continue;
    }

    if (arg === WRAPPER_NTFY_TOPIC_FLAG) {
      throw new Error(
        `${WRAPPER_NTFY_TOPIC_FLAG} has been removed; configure the topic in config.toml comments instead.`,
      );
    }

    if (arg === REMOVED_WRAPPER_NTFY_CONTROL_TOPIC_FLAG) {
      throw new Error(
        `${REMOVED_WRAPPER_NTFY_CONTROL_TOPIC_FLAG} has been removed; configure the topic in config.toml comments instead.`,
      );
    }

    if (arg === WRAPPER_NTFY_BASE_URL_FLAG) {
      const value = argv[index + 1];
      if (value === undefined) {
        throw new Error(`${WRAPPER_NTFY_BASE_URL_FLAG} requires a value`);
      }
      autoContinueNtfyBaseUrl = validateNtfyBaseUrl(
        WRAPPER_NTFY_BASE_URL_FLAG,
        value,
      );
      index += 1;
      continue;
    }

    if (arg === WRAPPER_NOTIFY_TIMEOUT_MS_FLAG) {
      const value = argv[index + 1];
      if (value === undefined) {
        throw new Error(`${WRAPPER_NOTIFY_TIMEOUT_MS_FLAG} requires a value`);
      }
      autoContinueNotifyTimeoutMs = parsePositiveInteger(
        WRAPPER_NOTIFY_TIMEOUT_MS_FLAG,
        value,
      );
      index += 1;
      continue;
    }

    passthrough.push(arg);
  }

  if (autoPrompt.trim().length === 0) {
    throw new Error(`${WRAPPER_PROMPT_FLAG} must not be empty`);
  }

  return {
    launchMode,
    autoLimit,
    autoContinueNtfyBaseUrl,
    autoContinueNotifyTimeoutMs,
    autoPrompt,
    passthrough,
  };
}

function firstNonOptionArg(argv) {
  for (const arg of argv) {
    if (!arg.startsWith("-")) {
      return arg;
    }
  }

  return null;
}

function shouldOfferAutoContinue(argv) {
  if (argv.some(isHelpOrVersionArg)) {
    return false;
  }

  const firstArg = firstNonOptionArg(argv);
  if (firstArg === null) {
    return true;
  }

  return !NON_INTERACTIVE_SUBCOMMANDS.has(firstArg);
}

function findPython() {
  const candidates = [];
  if (process.env.PYTHON) {
    candidates.push([process.env.PYTHON]);
  }

  if (process.platform === "win32") {
    candidates.push(["python"], ["py", "-3"], ["py"]);
  } else {
    candidates.push(["python3"], ["python"]);
  }

  for (const candidate of candidates) {
    const [command, ...prefixArgs] = candidate;
    const result = spawnSync(command, [...prefixArgs, "--version"], {
      stdio: "ignore",
    });
    if (!result.error && result.status === 0) {
      return candidate;
    }
  }

  return null;
}

function formatModeName(mode) {
  if (mode === "auto") {
    return "auto mode";
  }
  if (mode === "chat") {
    return "chat mode";
  }
  return "native mode";
}

async function spawnAndMirror(command, args, options = {}) {
  const child = spawn(command, args, {
    stdio: "inherit",
    env: options.env ?? process.env,
  });

  child.on("error", (error) => {
    console.error(error);
    process.exit(1);
  });

  const result = await new Promise((resolve) => {
    child.on("exit", (code, signal) => {
      resolve({ code, signal });
    });
  });

  if (result.signal) {
    process.kill(process.pid, result.signal);
    return;
  }

  process.exit(result.code ?? 1);
}

async function main() {
  if (!existsSync(REAL_LAUNCHER)) {
    console.error(`Missing backup launcher: ${REAL_LAUNCHER}`);
    process.exit(1);
  }

  let parsed;
  try {
    parsed = parseWrapperArgs(process.argv.slice(2));
  } catch (error) {
    console.error(error.message);
    process.exit(1);
  }

  const eligibleForAutoContinue = shouldOfferAutoContinue(parsed.passthrough);
  if (!eligibleForAutoContinue) {
    await spawnAndMirror(process.execPath, [REAL_LAUNCHER, ...parsed.passthrough]);
    return;
  }

  const launchMode = parsed.launchMode ?? "chat";

  if (launchMode === "native") {
    await spawnAndMirror(process.execPath, [REAL_LAUNCHER, ...parsed.passthrough]);
    return;
  }

  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    console.error(
      `[codex-auto-continue] ${formatModeName(launchMode)} requires an interactive TTY; starting native Codex.`,
    );
    await spawnAndMirror(process.execPath, [REAL_LAUNCHER, ...parsed.passthrough]);
    return;
  }

  if (!existsSync(PTY_HELPER)) {
    console.error(
      `[codex-auto-continue] missing helper ${PTY_HELPER}; starting native Codex.`,
    );
    await spawnAndMirror(process.execPath, [REAL_LAUNCHER, ...parsed.passthrough]);
    return;
  }

  let ntfySettings;
  try {
    ntfySettings = resolveNtfySettings(parsed);
  } catch (error) {
    console.error(
      `[codex-auto-continue] ${error.message} Starting native Codex instead.`,
    );
    await spawnAndMirror(process.execPath, [REAL_LAUNCHER, ...parsed.passthrough]);
    return;
  }

  if (ntfySettings.ntfyTopic === null) {
    const detail =
      ntfySettings.configExists === false
        ? `${ntfySettings.configPath} was not found`
        : `no remote topic is configured in ${ntfySettings.configPath}`;
    console.error(
      `[codex-auto-continue] ${formatModeName(launchMode)} requested, but ${detail}; starting native Codex. Add ` +
        `"# codex-remote-topic = \\"your-topic\\"" to enable remote mode.`,
    );
    await spawnAndMirror(process.execPath, [REAL_LAUNCHER, ...parsed.passthrough]);
    return;
  }

  const python = findPython();
  if (!python) {
    console.error(
      `[codex-auto-continue] ${formatModeName(launchMode)} requires ${
        process.platform === "win32"
          ? "python, py -3, or PYTHON"
          : "python3, python, or PYTHON"
      } on PATH; starting native Codex.`,
    );
    await spawnAndMirror(process.execPath, [REAL_LAUNCHER, ...parsed.passthrough]);
    return;
  }

  if (launchMode === "auto") {
    console.error(
      `[codex-auto-continue] enabled with prompt ${JSON.stringify(parsed.autoPrompt)} ` +
        `and limit ${
          parsed.autoLimit === null ? "unlimited sends" : `${parsed.autoLimit} sends`
        }; ` +
        "press bare Esc or Ctrl+C to switch to manual mode.",
    );
  } else {
    console.error(
      "[codex-auto-continue] chat mode enabled; waiting for remote ntfy messages; " +
        "press bare Esc or Ctrl+C to switch to manual mode.",
    );
  }
  if (process.env.CODEX_AUTO_CONTINUE_DEBUG === "1") {
    console.error(`[codex-auto-continue] debug log: ${DEBUG_LOG_PATH}`);
  }

  if (ntfySettings.ntfyTopic !== null) {
    console.error(
      `[codex-auto-continue] ntfy single-topic JSON mode on ${JSON.stringify(
        ntfySettings.ntfyTopic,
      )} from ${ntfySettings.configPath} via ${ntfySettings.ntfyBaseUrl} ` +
        `(timeout ${ntfySettings.notifyTimeoutMs}ms).`,
    );
  }

  const [pythonCommand, ...pythonArgs] = python;
  await spawnAndMirror(pythonCommand, [
    ...pythonArgs,
    PTY_HELPER,
    "--node",
    process.execPath,
    "--launcher",
    REAL_LAUNCHER,
    "--mode",
    launchMode,
    "--prompt",
    parsed.autoPrompt,
    ...(parsed.autoLimit === null
      ? []
      : ["--limit", String(parsed.autoLimit)]),
    ...(ntfySettings.ntfyTopic === null
      ? []
      : [
          "--ntfy-topic",
          ntfySettings.ntfyTopic,
          "--ntfy-base-url",
          ntfySettings.ntfyBaseUrl,
          "--notify-timeout-ms",
          String(ntfySettings.notifyTimeoutMs),
        ]),
    "--",
    ...parsed.passthrough,
  ]);
}

await main();
