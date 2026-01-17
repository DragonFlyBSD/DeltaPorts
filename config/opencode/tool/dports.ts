import { tool } from "@opencode-ai/plugin";

const DEFAULT_WORKER_PATH = "/build/synth/DeltaPorts/scripts/agentic-worker";

function requireEnv(name: string) {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Missing ${name} for dports tools`);
  }
  return value;
}

function getWorkerPath() {
  return process.env.DP_WORKER_PATH ?? DEFAULT_WORKER_PATH;
}

function sshArgs() {
  const host = requireEnv("DP_SSH_HOST");
  const port = process.env.DP_SSH_PORT ?? "22";
  const key = requireEnv("DP_SSH_KEY");
  return [
    "-i",
    key,
    "-p",
    port,
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "UserKnownHostsFile=/dev/null",
    host,
  ];
}

function envArgs() {
  const envs = [
    "DP_WORKSPACE_BASE",
    "DP_WORKSPACE_CONFIG",
    "DP_FPORTS_DIR",
    "DP_DELTAPORTS_DIR",
    "DP_DPORTS_DIR",
  ];
  const args: string[] = [];
  for (const key of envs) {
    const value = process.env[key];
    if (!value) {
      continue;
    }
    args.push(`${key}=${value}`);
  }
  return args;
}

async function runWorker(args: string[]) {
  const cmd = ["ssh", ...sshArgs(), "--", "env", ...envArgs(), getWorkerPath(), ...args];
  const proc = Bun.spawn(cmd, { stdout: "pipe", stderr: "pipe" });
  const stdout = await new Response(proc.stdout).text();
  const stderr = await new Response(proc.stderr).text();
  const exitCode = await proc.exited;

  if (exitCode !== 0 && !stdout.trim()) {
    throw new Error(stderr.trim() || `Worker failed with exit code ${exitCode}`);
  }

  let payload;
  try {
    payload = JSON.parse(stdout);
  } catch (err) {
    throw new Error(`Failed to parse worker output: ${stdout}\n${stderr}`);
  }

  if (!payload.ok) {
    throw new Error(payload.error || "Worker returned error");
  }

  return JSON.stringify(payload.result);
}

export const dports_workspace_verify = tool({
  description: "Verify workspace config and pinned FPORTS ref",
  args: {},
  async execute() {
    return await runWorker(["workspace-verify"]);
  },
});

export const dports_checkout_branch = tool({
  description: "Checkout or create per-origin fix branch",
  args: {
    origin: tool.schema.string().describe("Port origin (category/port)"),
  },
  async execute(args) {
    return await runWorker(["checkout-branch", "--origin", args.origin]);
  },
});

export const dports_commit = tool({
  description: "Commit current DeltaPorts changes",
  args: {
    message: tool.schema.string().describe("Commit message"),
  },
  async execute(args) {
    return await runWorker(["commit", "--message", args.message]);
  },
});

export const dports_materialize_closure = tool({
  description: "Materialize port + MASTERDIR closure into staged DPorts",
  args: {
    origin: tool.schema.string().describe("Port origin (category/port)"),
  },
  async execute(args) {
    return await runWorker(["materialize-closure", "--origin", args.origin]);
  },
});

export const dports_extract = tool({
  description: "Run make extract and return WRKSRC",
  args: {
    origin: tool.schema.string().describe("Port origin (category/port)"),
  },
  async execute(args) {
    return await runWorker(["extract", "--origin", args.origin]);
  },
});

export const dports_get_file = tool({
  description: "Read a file from the workspace (base64)",
  args: {
    path: tool.schema.string().describe("Absolute path inside workspace"),
  },
  async execute(args) {
    return await runWorker(["get-file", "--path", args.path]);
  },
});

export const dports_put_file = tool({
  description: "Write a file to the workspace (base64)",
  args: {
    path: tool.schema.string().describe("Absolute path inside workspace"),
    content: tool.schema.string().describe("Base64-encoded content"),
    expectedSha256: tool.schema.string().optional().describe("Optional sha256 for optimistic lock"),
  },
  async execute(args) {
    const workerArgs = ["put-file", "--path", args.path, "--content", args.content];
    if (args.expectedSha256) {
      workerArgs.push("--expected-sha256", args.expectedSha256);
    }
    return await runWorker(workerArgs);
  },
});

export const dports_dupe = tool({
  description: "Run dupe on a WRKSRC file",
  args: {
    path: tool.schema.string().describe("Absolute file path inside workspace"),
  },
  async execute(args) {
    return await runWorker(["dupe", "--path", args.path]);
  },
});

export const dports_genpatch = tool({
  description: "Run genpatch on a WRKSRC file",
  args: {
    path: tool.schema.string().describe("Absolute file path inside workspace"),
  },
  async execute(args) {
    return await runWorker(["genpatch", "--path", args.path]);
  },
});

export const dports_install_patches = tool({
  description: "Install patch-* files into DeltaPorts dragonfly/",
  args: {
    origin: tool.schema.string().describe("Port origin (category/port)"),
    patches: tool.schema.array(tool.schema.string()).optional().describe("Optional patch filenames"),
  },
  async execute(args) {
    const workerArgs = ["install-patches", "--origin", args.origin];
    if (args.patches) {
      for (const patch of args.patches) {
        workerArgs.push("--patch", patch);
      }
    }
    return await runWorker(workerArgs);
  },
});

export const dports_emit_diff = tool({
  description: "Emit diffs/*.diff for a port skeleton file",
  args: {
    origin: tool.schema.string().describe("Port origin (category/port)"),
    relpath: tool.schema.string().describe("Relative path under port dir (e.g. Makefile)"),
  },
  async execute(args) {
    return await runWorker(["emit-diff", "--origin", args.origin, "--relpath", args.relpath]);
  },
});

export const dports_dsynth_build = tool({
  description: "Run dsynth just-build using workspace profile",
  args: {
    origin: tool.schema.string().describe("Port origin (category/port)"),
    profile: tool.schema.string().optional().describe("Optional dsynth profile"),
  },
  async execute(args) {
    const workerArgs = ["dsynth-build", "--origin", args.origin];
    if (args.profile) {
      workerArgs.push("--profile", args.profile);
    }
    return await runWorker(workerArgs);
  },
});
