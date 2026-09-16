import { useEffect, useState } from "react";
import { Job, JobStatus, StageBar, fmtEta, fmtTime } from "../api";

const STAGE_DEFS: { key: string; label: string }[] = [
  { key: "download", label: "Скачивание видео" },
  { key: "audio", label: "Звуковая дорожка" },
  { key: "transcribe", label: "Транскрипция" },
  { key: "analyze", label: "Отбор моментов" },
  { key: "render", label: "Монтаж" },
];

const STATUS_STAGE: Record<string, string> = {
  queued: "download",
  downloading: "download",
  extracting_audio: "audio",
  transcribing: "transcribe",
  analyzing: "analyze",
  ready: "analyze",
  rendering: "render",
  done: "render",
  error: "download",
  cancelled: "download",
};

function resolveStages(job: Job): StageBar[] {
  const activeKey = STATUS_STAGE[job.status] || "download";
  const order = STAGE_DEFS.map((item) => item.key);
  const activeIdx = order.indexOf(activeKey);
  const finished =
    job.status === "done" || job.status === "ready"
      ? job.status === "done"
        ? order.length
        : order.indexOf("render")
      : -1;

  return STAGE_DEFS.map((def, idx) => {
    const fromJob = job.stages?.[def.key];
    if (fromJob) return fromJob;
    let state: StageBar["state"] = "pending";
    let progress = 0;
    if (finished >= 0 && idx < finished) {
      state = "done";
      progress = 1;
    } else if (idx < activeIdx) {
      state = "done";
      progress = 1;
    } else if (idx === activeIdx && !["queued", "cancelled", "error"].includes(job.status)) {
      state = "active";
      progress = Math.max(0, Math.min(1, job.progress || 0));
    }
    if (job.status === "done" && def.key === "render") {
      state = "done";
      progress = 1;
    }
    return { key: def.key, label: def.label, progress, state };
  });
}

function pctLabel(progress: number) {
  const p = Math.max(0, Math.min(100, (progress || 0) * 100));
  if (progress > 0 && p < 10) return `${p.toFixed(1)}%`;
  return `${Math.round(p)}%`;
}

const STATUS_LABEL: Partial<Record<JobStatus, string>> = {
  queued: "в очереди",
  downloading: "скачивание",
  extracting_audio: "звук",
  transcribing: "транскрипция",
  analyzing: "отбор",
  ready: "к ревью",
  rendering: "монтаж",
  done: "готово",
  error: "ошибка",
  cancelled: "остановлено",
};

export function statusLabel(status: JobStatus) {
  return STATUS_LABEL[status] || status;
}

type Sample = { t: number; p: number };

function loadSamples(jobId: string, key: string): Sample[] {
  try {
    return JSON.parse(sessionStorage.getItem(`eta:${jobId}:${key}`) || "[]");
  } catch {
    return [];
  }
}

function saveSamples(jobId: string, key: string, samples: Sample[]) {
  sessionStorage.setItem(`eta:${jobId}:${key}`, JSON.stringify(samples));
}

function bootstrapEta(job: Job, stage: StageBar): number | null {
  if (stage.key !== "transcribe" || stage.state !== "active") return null;
  if ((stage.progress || 0) < 0.01) return null;
  if (job.stages?.download?.state !== "done" || job.stages?.audio?.state !== "done") return null;
  if (job.source_url) return null;
  const started = Date.parse(job.created_at);
  if (!Number.isFinite(started)) return null;
  const elapsed = (Date.now() - started) / 1000;
  if (elapsed < 90) return null;
  const rate = stage.progress / elapsed;
  if (rate <= 0) return null;
  return (1 - stage.progress) / rate;
}

function useEta(job: Job, stage: StageBar): number | null {
  const [eta, setEta] = useState<number | null>(stage.eta_sec ?? bootstrapEta(job, stage));

  useEffect(() => {
    if (stage.state !== "active") {
      setEta(null);
      return;
    }
    if (stage.eta_sec != null && stage.eta_sec > 0) {
      setEta(stage.eta_sec);
      return;
    }
    const now = Date.now();
    let samples = loadSamples(job.id, stage.key).filter((item) => now - item.t < 50 * 60_000);
    const last = samples[samples.length - 1];
    if (!last || Math.abs(last.p - stage.progress) >= 0.0005) {
      samples.push({ t: now, p: stage.progress });
      samples = samples.slice(-12);
      saveSamples(job.id, stage.key, samples);
    }
    if (samples.length >= 2) {
      const a = samples[0];
      const b = samples[samples.length - 1];
      const dt = (b.t - a.t) / 1000;
      const dp = b.p - a.p;
      if (dt >= 25 && dp >= 0.003) {
        setEta((1 - b.p) / (dp / dt));
        return;
      }
    }
    setEta(bootstrapEta(job, stage));
  }, [job.id, job.created_at, job.source_url, stage.key, stage.progress, stage.state, stage.eta_sec]);

  return eta;
}

function StageRow({ job, stage }: { job: Job; stage: StageBar }) {
  const eta = useEta(job, stage);
  const value = pctLabel(stage.progress);
  const animate =
    stage.state === "active" && !["cancelled", "error", "done", "ready"].includes(job.status);
  const processed =
    stage.processed_sec != null && stage.total_sec
      ? `${fmtTime(stage.processed_sec)} / ${fmtTime(stage.total_sec)}`
      : null;
  return (
    <div className={`stage-row ${stage.state}`}>
      <div className="stage-head">
        <span>
          {stage.label}
          {processed ? <span className="stage-sub"> · {processed}</span> : null}
        </span>
        <strong>
          {value}
          {eta != null && stage.state === "active" ? (
            <span className="stage-eta"> · {fmtEta(eta)}</span>
          ) : null}
        </strong>
      </div>
      <div className="stage-track">
        <i className={animate ? "active" : ""} style={{ width: `${Math.min(100, Math.max(0, (stage.progress || 0) * 100))}%` }} />
      </div>
    </div>
  );
}

export function StageBars({ job, compact = false }: { job: Job; compact?: boolean }) {
  const stages = resolveStages(job);
  return (
    <div className={`stage-list ${compact ? "compact" : ""}`}>
      {stages.map((stage) => (
        <StageRow key={stage.key} job={job} stage={stage} />
      ))}
    </div>
  );
}
