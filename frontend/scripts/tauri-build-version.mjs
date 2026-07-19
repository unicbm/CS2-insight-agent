import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const version = process.argv[2]?.trim();
if (!version || !/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(version)) {
  console.error("Usage: npm run desktop:build:ver -- <x.y.z>");
  process.exit(1);
}

const frontendRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

function run(command, args, env = process.env, shell = process.platform === "win32") {
  const result = spawnSync(command, args, {
    cwd: frontendRoot,
    env,
    stdio: "inherit",
    shell,
  });
  if (result.status !== 0) process.exit(result.status ?? 1);
}

run("npm", ["run", "desktop:stage-python"]);
run("npm", ["run", "desktop:stage-resources"]);

const tauri = join(frontendRoot, "node_modules", "@tauri-apps", "cli", "tauri.js");
run(
  process.execPath,
  [tauri, "build", "--config", JSON.stringify({ version })],
  {
    ...process.env,
    CS2_INSIGHT_APP_VERSION: version,
  },
  false,
);
