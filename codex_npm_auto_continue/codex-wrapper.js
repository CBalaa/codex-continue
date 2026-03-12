#!/usr/bin/env node

import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const REAL_LAUNCHER = path.join(__dirname, "codex.real.js");
const PTY_HELPER = path.join(__dirname, "codex-auto-continue-pty.py");
const WRAPPER_AUTO_FLAG = "--auto-continue";
const WRAPPER_NO_AUTO_FLAG = "--no-auto-continue";
const WRAPPER_PROMPT_FLAG = "--auto-continue-prompt";
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

function parseWrapperArgs(argv) {
  const passthrough = [];
  let autoContinue = null;
  let autoPrompt = "继续";

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];

    if (arg === WRAPPER_AUTO_FLAG) {
      autoContinue = true;
      continue;
    }

    if (arg === WRAPPER_NO_AUTO_FLAG) {
      autoContinue = false;
      continue;
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

    passthrough.push(arg);
  }

  if (autoPrompt.trim().length === 0) {
    throw new Error(`${WRAPPER_PROMPT_FLAG} must not be empty`);
  }

  return {
    autoContinue,
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

async function promptForLaunchMode() {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });

  process.stdout.write(
    [
      "",
      "Codex launch mode:",
      "  1) Normal",
      "  2) Auto-continue after each completed turn",
      "Select [1/2] (default 1): ",
    ].join("\n"),
  );

  const answer = await new Promise((resolve) => {
    rl.once("line", resolve);
  });

  rl.close();

  const trimmed = String(answer).trim();
  return trimmed === "2";
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

  if (!existsSync(PTY_HELPER)) {
    console.error(`Missing auto-continue helper: ${PTY_HELPER}`);
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

  const shouldOffer =
    parsed.autoContinue === null &&
    process.stdin.isTTY &&
    process.stdout.isTTY &&
    eligibleForAutoContinue;
  let enableAuto = parsed.autoContinue === true;

  if (shouldOffer) {
    enableAuto = await promptForLaunchMode();
  }

  if (!enableAuto) {
    await spawnAndMirror(process.execPath, [REAL_LAUNCHER, ...parsed.passthrough]);
    return;
  }

  const python = findPython();
  if (!python) {
    const dependencyHint =
      process.platform === "win32"
        ? "python, py -3, or PYTHON"
        : "python3, python, or PYTHON";
    console.error(`Auto-continue mode requires ${dependencyHint} on PATH.`);
    process.exit(1);
  }

  console.error(
    `[codex-auto-continue] enabled with prompt ${JSON.stringify(parsed.autoPrompt)}; ` +
      "press bare Esc or Ctrl+C to disable it for this session.",
  );
  if (process.env.CODEX_AUTO_CONTINUE_DEBUG === "1") {
    console.error(`[codex-auto-continue] debug log: ${DEBUG_LOG_PATH}`);
  }

  const [pythonCommand, ...pythonArgs] = python;
  await spawnAndMirror(pythonCommand, [
    ...pythonArgs,
    PTY_HELPER,
    "--node",
    process.execPath,
    "--launcher",
    REAL_LAUNCHER,
    "--prompt",
    parsed.autoPrompt,
    "--",
    ...parsed.passthrough,
  ]);
}

await main();
