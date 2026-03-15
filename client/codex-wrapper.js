#!/usr/bin/env node

import { spawn } from "node:child_process";
import {
  accessSync,
  constants,
  existsSync,
  readFileSync,
  realpathSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);

const WRAPPER_AUTO_FLAG = "--auto-mode";
const WRAPPER_CHAT_FLAG = "--chat-mode";
const WRAPPER_NATIVE_FLAG = "--native";
const WRAPPER_WEB_CONSOLE_FLAG = "--web-console";
const WRAPPER_WEB_AGENT_FLAG = "--web-console-agent";
const REMOVED_WRAPPER_AUTO_FLAG = "--auto-continue";
const REMOVED_WRAPPER_NO_AUTO_FLAG = "--no-auto-continue";
const WRAPPER_PROMPT_FLAG = "--auto-continue-prompt";
const WRAPPER_LIMIT_FLAG = "--auto-continue-limit";
const REMOVED_WRAPPER_NTFY_TOPIC_FLAG = "--auto-continue-ntfy-topic";
const REMOVED_WRAPPER_NTFY_BASE_URL_FLAG = "--auto-continue-ntfy-base-url";
const REMOVED_WRAPPER_NOTIFY_TIMEOUT_MS_FLAG = "--auto-continue-notify-timeout-ms";
const COMMENT_WEB_BIND_KEYS = [
  "codex-remote-web-bind",
  "codex-auto-continue-web-bind",
];
const COMMENT_WEB_PORT_KEYS = [
  "codex-remote-web-port",
  "codex-auto-continue-web-port",
];
const COMMENT_WEB_PASSWORD_KEYS = [
  "codex-remote-web-password",
  "codex-auto-continue-web-password",
];
const COMMENT_REMOTE_SERVER_URL_KEYS = [
  "codex-remote-server-url",
  "codex-auto-continue-server-url",
];
const COMMENT_REMOTE_MACHINE_NAME_KEYS = [
  "codex-remote-machine-name",
  "codex-auto-continue-machine-name",
];
const COMMENT_LAUNCH_SCRIPT_KEYS = [
  "codex-remote-launch-script",
  "codex-auto-continue-launch-script",
];
const DEFAULT_WEB_BIND = "127.0.0.1";
const DEFAULT_WEB_PORT = 8765;
const DEFAULT_MACHINE_KEY_FILENAME = "codex-auto-continue-machine-key.txt";
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

function resolveRemoteSettings(parsed) {
  const configPath = resolveCodexConfigPath(parsed.passthrough);
  const configDir = path.dirname(configPath);
  if (!existsSync(configPath)) {
    return {
      configExists: false,
      configPath,
      configDir,
      launchScript: null,
      webBind: null,
      webPort: null,
      webPassword: null,
      remoteServerUrl: null,
      machineName: null,
      machineKeyFile: path.join(configDir, DEFAULT_MACHINE_KEY_FILENAME),
    };
  }

  const fileText = readFileSync(configPath, "utf8");
  const passwordSetting = findConfigSetting(fileText, COMMENT_WEB_PASSWORD_KEYS);
  const serverUrlSetting = findConfigSetting(fileText, COMMENT_REMOTE_SERVER_URL_KEYS);
  const machineNameSetting = findConfigSetting(fileText, COMMENT_REMOTE_MACHINE_NAME_KEYS);
  const launchScriptSetting = findConfigSetting(fileText, COMMENT_LAUNCH_SCRIPT_KEYS);

  const launchScript =
    launchScriptSetting === null
      ? null
      : path.resolve(
          expandHomePath(parseCommentStringSetting(configPath, launchScriptSetting)),
        );

  if (passwordSetting === null) {
    return {
      configExists: true,
      configPath,
      configDir,
      launchScript,
      webBind: null,
      webPort: null,
      webPassword: null,
      remoteServerUrl:
        serverUrlSetting === null
          ? null
          : parseCommentStringSetting(configPath, serverUrlSetting),
      machineName:
        machineNameSetting === null
          ? null
          : parseCommentStringSetting(configPath, machineNameSetting),
      machineKeyFile: path.join(configDir, DEFAULT_MACHINE_KEY_FILENAME),
    };
  }

  const bindSetting = findConfigSetting(fileText, COMMENT_WEB_BIND_KEYS);
  const portSetting = findConfigSetting(fileText, COMMENT_WEB_PORT_KEYS);

  return {
    configExists: true,
    configPath,
    configDir,
    launchScript,
    webBind:
      bindSetting === null
        ? DEFAULT_WEB_BIND
        : parseCommentStringSetting(configPath, bindSetting),
    webPort:
      portSetting === null
        ? DEFAULT_WEB_PORT
        : parseCommentIntegerSetting(configPath, portSetting),
    webPassword: parseCommentStringSetting(configPath, passwordSetting),
    remoteServerUrl:
      serverUrlSetting === null
        ? null
        : parseCommentStringSetting(configPath, serverUrlSetting),
    machineName:
      machineNameSetting === null
        ? null
        : parseCommentStringSetting(configPath, machineNameSetting),
    machineKeyFile: path.join(configDir, DEFAULT_MACHINE_KEY_FILENAME),
  };
}

function parseWrapperArgs(argv) {
  const passthrough = [];
  let launchMode = null;
  let autoPrompt = "继续";
  let autoLimit = null;

  function setLaunchMode(nextMode, flagName) {
    if (launchMode !== null && launchMode !== nextMode) {
      throw new Error(`${flagName} cannot be combined with another launch mode flag.`);
    }
    launchMode = nextMode;
  }

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];

    if (arg === WRAPPER_WEB_CONSOLE_FLAG || arg === WRAPPER_WEB_AGENT_FLAG) {
      throw new Error(
        `${arg} is no longer available through \`codex\`; run the configured launch script directly instead.`,
      );
    }

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

    if (
      arg === REMOVED_WRAPPER_NTFY_TOPIC_FLAG ||
      arg === REMOVED_WRAPPER_NTFY_BASE_URL_FLAG ||
      arg === REMOVED_WRAPPER_NOTIFY_TIMEOUT_MS_FLAG
    ) {
      throw new Error(`${arg} has been removed; remote control now uses the private web console.`);
    }

    passthrough.push(arg);
  }

  if (autoPrompt.trim().length === 0) {
    throw new Error(`${WRAPPER_PROMPT_FLAG} must not be empty`);
  }

  return {
    launchMode,
    autoLimit,
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

function formatModeName(mode) {
  if (mode === "auto") {
    return "auto mode";
  }
  if (mode === "chat") {
    return "chat mode";
  }
  return "native mode";
}

function formatWebUrl(bind, port) {
  if (bind === "0.0.0.0" || bind === "::") {
    return `http://127.0.0.1:${port}/`;
  }
  const host = bind.includes(":") && !bind.startsWith("[") ? `[${bind}]` : bind;
  return `http://${host}:${port}/`;
}

function isExecutableFile(candidate) {
  try {
    accessSync(candidate, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function resolveNativeCodexCommand() {
  const wrapperRealPath = realpathSync(__filename);
  const pathEntries = (process.env.PATH || "")
    .split(path.delimiter)
    .map((value) => value.trim())
    .filter((value) => value.length > 0);

  for (const entry of pathEntries) {
    const candidate = path.join(entry, "codex");
    if (!isExecutableFile(candidate)) {
      continue;
    }

    let candidateRealPath;
    try {
      candidateRealPath = realpathSync(candidate);
    } catch {
      continue;
    }

    if (candidateRealPath === wrapperRealPath) {
      continue;
    }

    return candidate;
  }

  throw new Error(
    "could not find native codex on PATH after this wrapper; ensure the native Codex binary is still installed later in PATH.",
  );
}

function launchScriptProblem(remoteSettings) {
  if (remoteSettings.configExists === false) {
    return `${remoteSettings.configPath} was not found`;
  }
  if (remoteSettings.launchScript === null) {
    return `no launch script is configured in ${remoteSettings.configPath}`;
  }
  if (!existsSync(remoteSettings.launchScript)) {
    return `launch script ${remoteSettings.launchScript} was not found`;
  }
  return null;
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
  let parsed;
  try {
    parsed = parseWrapperArgs(process.argv.slice(2));
  } catch (error) {
    console.error(error.message);
    process.exit(1);
  }

  let nativeCodex;
  try {
    nativeCodex = resolveNativeCodexCommand();
  } catch (error) {
    console.error(`[codex-auto-continue] ${error.message}`);
    process.exit(1);
  }

  const eligibleForAutoContinue = shouldOfferAutoContinue(parsed.passthrough);
  if (!eligibleForAutoContinue) {
    await spawnAndMirror(nativeCodex, parsed.passthrough);
    return;
  }

  const launchMode = parsed.launchMode ?? "chat";
  if (launchMode === "native") {
    await spawnAndMirror(nativeCodex, parsed.passthrough);
    return;
  }

  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    console.error(
      `[codex-auto-continue] ${formatModeName(launchMode)} requires an interactive TTY; starting native Codex.`,
    );
    await spawnAndMirror(nativeCodex, parsed.passthrough);
    return;
  }

  let remoteSettings;
  try {
    remoteSettings = resolveRemoteSettings(parsed);
  } catch (error) {
    console.error(
      `[codex-auto-continue] ${error.message} Starting native Codex instead.`,
    );
    await spawnAndMirror(nativeCodex, parsed.passthrough);
    return;
  }

  if (remoteSettings.webPassword === null) {
    const detail =
      remoteSettings.configExists === false
        ? `${remoteSettings.configPath} was not found`
        : `no web password is configured in ${remoteSettings.configPath}`;
    console.error(
      `[codex-auto-continue] ${formatModeName(launchMode)} requested, but ${detail}; starting native Codex. Add ` +
        `"# codex-remote-web-password = \\\"change-me\\\"" to enable the private web console.`,
    );
    await spawnAndMirror(nativeCodex, parsed.passthrough);
    return;
  }

  const launchScriptIssue = launchScriptProblem(remoteSettings);
  if (launchScriptIssue !== null) {
    console.error(
      `[codex-auto-continue] ${formatModeName(launchMode)} requested, but ${launchScriptIssue}; starting native Codex. Add ` +
        `"# codex-remote-launch-script = \\\"/absolute/path/to/client/codex-auto-continue-launch\\\"" to enable the web console bridge.`,
    );
    await spawnAndMirror(nativeCodex, parsed.passthrough);
    return;
  }

  if (launchMode === "auto") {
    console.error(
      `[codex-auto-continue] enabled with prompt ${JSON.stringify(parsed.autoPrompt)} ` +
        `and limit ${
          parsed.autoLimit === null ? "unlimited sends" : `${parsed.autoLimit} sends`
        }; press bare Esc or Ctrl+C to switch to manual mode.`,
    );
  } else {
    console.error(
      "[codex-auto-continue] chat mode enabled; open the shared private web console to send messages; " +
        "press bare Esc or Ctrl+C to switch to manual mode.",
    );
  }

  if (process.env.CODEX_AUTO_CONTINUE_DEBUG === "1") {
    console.error(`[codex-auto-continue] debug log: ${DEBUG_LOG_PATH}`);
  }

  console.error(
    `[codex-auto-continue] private web console on ${JSON.stringify(
      formatWebUrl(remoteSettings.webBind, remoteSettings.webPort),
    )} from ${remoteSettings.configPath}.`,
  );
  console.error(
    "[codex-auto-continue] this session will appear as a tab automatically after the web console connects.",
  );

  await spawnAndMirror(remoteSettings.launchScript, [
    "codex",
    "--mode",
    launchMode,
    "--prompt",
    parsed.autoPrompt,
    ...(parsed.autoLimit === null ? [] : ["--limit", String(parsed.autoLimit)]),
    "--web-bind",
    remoteSettings.webBind,
    "--web-port",
    String(remoteSettings.webPort),
    "--web-password",
    remoteSettings.webPassword,
    "--",
    ...parsed.passthrough,
  ]);
}

await main();
