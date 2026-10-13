// Railway settings for both services of this project, as Infrastructure as Code.
//
// Railway does NOT read this file when it deploys. A change here takes effect
// only when someone runs `railway config apply` from the repo root; preview it
// first with `railway config plan`. docs/runbooks/deploy.md has the procedure.
//
// This describes the whole project: a service left out of `resources` is
// deleted by an apply. Service names must match the names on Railway exactly,
// or an apply creates a second service instead of updating the first.
import { defineRailway, github, project, service } from "railway/iac";

export default defineRailway(() => {
  // Grafana Alloy, built from ops/alloy/Dockerfile. Watch patterns match from
  // the repo root even though the service's root directory is ops/alloy.
  const monitoring = service("EQ2 Lexicon Grafana Monitoring", {
    source: github("VortexUK/EQ2Lexicon", { branch: "main", checkSuites: false, rootDirectory: "ops/alloy/" }),
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "Dockerfile", watchPatterns: ["/ops/alloy/**"] },
    deploy: { restartPolicyType: "ALWAYS" },
    replicas: { "sfo": 1 },
    networking: { privateNetworkEndpoint: "eq2censusbot" },
  });
  // The web app and the bot. `checkSuites` is "wait for CI": a push to main
  // deploys only after its checks pass.
  const app = service("EQ2 Lexicon", {
    source: github("VortexUK/EQ2Lexicon", { branch: "main", checkSuites: true }),
    build: {
      buildEnvironment: "V3",
      // Must be stated: with no builder stored, Railway builds with Railpack
      // and nixpacks.toml (Python, uv, the frontend build) is ignored.
      builder: "NIXPACKS",
      // Only redeploy when something that ships in the image changes.
      // gitignore-style, matched from the repo root; a negation needs a
      // preceding include (`**`).
      watchPatterns: ["**", "!/ops/**", "!/docs/**", "!/.github/**", "!/.claude/**", "!/.railway/**", "!/*.md"],
    },
    // Exec the app from the project's venv. Schema migrations run inside the
    // app lifespan (backend/pg_migrate.py): a deploy IS a migration run.
    //
    // The venv (`/app/.venv`, where `uv sync` installed the dependencies) is
    // tried first, then a legacy `/opt/venv`, then PATH as a last resort. The
    // `python` on PATH is the bare Nix-store one with no site-packages, so
    // resolving it first crashes on the first import. Every candidate is
    // printed so a Nixpacks layout change shows in the first log lines.
    start: "set -x; echo '=== python path discovery ==='; (which python python3 2>&1 || true); (ls -la /app/.venv/bin/ 2>&1 | head -20 || true); (ls -la /opt/venv/bin/ 2>&1 | head -20 || true); echo \"PATH=$PATH\"; echo '=== end discovery ==='; PYTHON_BIN=''; for c in /app/.venv/bin/python /opt/venv/bin/python; do if [ -x \"$c\" ]; then PYTHON_BIN=\"$c\"; break; fi; done; if [ -z \"$PYTHON_BIN\" ]; then PYTHON_BIN=$(command -v python || command -v python3 || echo ''); fi; if [ -z \"$PYTHON_BIN\" ]; then echo 'FATAL: no python found in project venv or on PATH' >&2; exit 78; fi; echo \"Resolved Python: $PYTHON_BIN\"; exec \"$PYTHON_BIN\" main.py",
    // Do not route traffic, or call the deploy live, until the app serves.
    // Without this Railway switches to the new container the moment it
    // starts, while imports and the lifespan (pool open, pending migrations)
    // are still running, and every request is a 502.
    healthcheck: "/api/health",
    healthcheckTimeout: 300,
    // Restart policy: on failure, 10 retries. That is Railway's default, and
    // it must NOT be written here: Railway stores a default as "unset", so a
    // line stating it makes every `railway config plan` report a change that
    // an apply never clears (seen on the first apply, 2026-10-13).
    replicas: { "europe-west4-drams3a": 1 },
    domains: ["eq2lexicon.com", "parses.eq2lexicon.com", "varsoon.eq2lexicon.com", "wuoshi.eq2lexicon.com"],
    networking: { privateNetworkEndpoint: "eq2lexicon" },
  });

  return project("EQ2Lexicon", {
    // Variables stay owned by Railway. This repo is public: no value belongs
    // here, and `railway config pull --include-variables` must never be run.
    variables: { managed: false },
    resources: [monitoring, app],
  });
});
