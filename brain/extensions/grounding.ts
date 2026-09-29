import * as fs from "node:fs";
import * as path from "node:path";

export const OBSERVATION_TTL_MS = 5_000;
export const WORKER_HEARTBEAT_TTL_MS = 20_000;
export const runtimeMemory = (runDir: string) => path.join(runDir, "memory", "SCRATCH.md");

export function readWorkerStatus(runDir: string, now = Date.now()) {
  try {
    const stat = fs.statSync(path.join(runDir, "worker.alive"));
    const age = now - stat.mtimeMs;
    if (!stat.isFile() || !Number.isFinite(age) || age < -1_000) throw new Error("invalid worker heartbeat");
    return {
      online: age < WORKER_HEARTBEAT_TTL_MS,
      heartbeat_at: new Date(stat.mtimeMs).toISOString(),
      max_age_ms: WORKER_HEARTBEAT_TTL_MS,
    };
  } catch {
    return { online: false, heartbeat_at: null, max_age_ms: WORKER_HEARTBEAT_TTL_MS };
  }
}

export function readCapabilitySnapshot(runDir: string, now = Date.now()) {
  const checkedAt = new Date(now).toISOString();
  const observation = readObservation(runDir, now);
  const status = observation.available ? "available"
    : observation.reason === "stale" ? "stale"
      : observation.reason === "no observation producer connected" ? "missing" : "invalid";
  return {
    schema_version: 1,
    checked_at: checkedAt,
    identity: "MIST",
    body_design: "four legs, three joint axes per leg and wheel feet; engineering in progress",
    motion: { execution: "simulation", hardware_connected: false, stairs_validated: false },
    face: "expression events can animate a connected browser; phone display has no acknowledgement",
    perception: {
      available: observation.available,
      status,
      captured_at: observation.available ? new Date(observation.captured_at).toISOString() : null,
      age_ms: observation.age_ms ?? null,
      max_age_ms: OBSERVATION_TTL_MS,
      requires_fresh_observe: true,
      source_verified: false,
    },
    executive: {
      ...readWorkerStatus(runDir, now),
      actions: ["research text", "draft text"],
      autonomous_notifications: false,
    },
    memory: { persistent: true, scope: "this run directory", recall_tool: "recall_memory" },
    unavailable: ["physical actuation", "arbitrary shell execution", "sending external messages", "verified camera tracking"],
  };
}

export function readObservation(runDir: string, now = Date.now()) {
  const file = path.join(runDir, "observation.json");
  try {
    if (fs.statSync(file).size > 32_768) throw new Error("observation exceeds 32 KiB");
    const input = JSON.parse(fs.readFileSync(file, "utf8"));
    const captured = typeof input.captured_at === "string" ? Date.parse(input.captured_at) : NaN;
    const age = now - captured;
    if (!Number.isFinite(age) || age < -1_000) throw new Error("invalid observation timestamp");
    if (age > OBSERVATION_TTL_MS) {
      return { available: false, reason: "stale", age_ms: age, max_age_ms: OBSERVATION_TTL_MS };
    }
    const source = typeof input.source === "string" ? input.source.slice(0, 120) : "unspecified";
    const objects = Array.isArray(input.objects) ? input.objects.slice(0, 24).flatMap((o: any) => {
      if (!o || typeof o.label !== "string") return [];
      return [{
        label: o.label.slice(0, 160),
        bearing_deg: Number.isFinite(o.bearing_deg) ? o.bearing_deg : null,
        distance_m: Number.isFinite(o.distance_m) && o.distance_m >= 0 ? o.distance_m : null,
        confidence: Number.isFinite(o.confidence) && o.confidence >= 0 && o.confidence <= 1 ? o.confidence : null,
      }];
    }) : [];
    return {
      available: true, captured_at: input.captured_at, age_ms: Math.max(0, age),
      max_age_ms: OBSERVATION_TTL_MS, source,
      provenance: "local producer report; source identity is not independently verified",
      untrusted: true,
      summary: typeof input.summary === "string" ? input.summary.slice(0, 2_000) : "",
      objects,
      hazards: Array.isArray(input.hazards)
        ? input.hazards.filter((h: unknown) => typeof h === "string").slice(0, 16).map((h: string) => h.slice(0, 160)) : [],
    };
  } catch (error) {
    return {
      available: false,
      reason: (error as NodeJS.ErrnoException).code === "ENOENT" ? "no observation producer connected" : "invalid observation",
    };
  }
}

export function recallMemory(brainDir: string, runDir: string, query: string, demoMemory = false) {
  const files = [{ file: runtimeMemory(runDir), source: "runtime notes" }];
  if (demoMemory) {
    files.push({ file: path.join(brainDir, "memory", "MEMORY.md"), source: "demo memory (unverified fixture)" });
    const people = path.join(brainDir, "memory", "PEOPLE");
    if (fs.existsSync(people)) {
      for (const name of fs.readdirSync(people).filter((f) => f.endsWith(".md")).slice(0, 40)) {
        files.push({ file: path.join(people, name), source: `demo person ${name} (unverified fixture)` });
      }
    }
  }
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  const matches: { source: string; text: string }[] = [];
  for (const { file, source } of files) {
    try {
      const raw = fs.readFileSync(file, "utf8");
      for (const line of raw.slice(-64_000).split(/\r?\n/).reverse()) {
        if (line.trim() && terms.every((term) => line.toLowerCase().includes(term))) {
          matches.push({ source, text: line.slice(0, 1_000) });
          if (matches.length === 12) break;
        }
      }
    } catch { /* No notes have been saved in this run yet. */ }
    if (matches.length === 12) break;
  }
  return { untrusted: true, matches, scope: "this run's notes; demo fixtures only when explicitly enabled" };
}
