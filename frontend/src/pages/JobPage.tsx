import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Job, api, fileUrl, fmtTime } from "../api";
import { StageBars, statusLabel } from "../components/StageBars";

const ACTIVE = ["queued", "downloading", "extracting_audio", "transcribing", "analyzing", "rendering"];

export default function JobPage() {
  const { id } = useParams();
  const [job, setJob] = useState<Job | null>(null);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    if (!id) return;
    const data = await api<Job>(`/api/jobs/${id}`);
    setJob(data);
    setPicked((prev) => {
      if (Object.keys(prev).length) return prev;
      const next: Record<string, boolean> = {};
      data.moments.slice(0, 5).forEach((m) => {
        next[m.id] = true;
      });
      return next;
    });
  }

  useEffect(() => {
    let alive = true;
    const tick = () =>
      refresh().catch((e) => {
        if (alive) setError(String(e));
      });
    tick();
    const t = setInterval(tick, 1500);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [id]);

  const selectedIds = useMemo(
    () => Object.entries(picked).filter(([, v]) => v).map(([k]) => k),
    [picked],
  );

  async function render() {
    if (!id) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/api/jobs/${id}/render`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ moment_ids: selectedIds }),
      });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (!id) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/api/jobs/${id}/cancel`, { method: "POST" });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function retry() {
    if (!id) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/api/jobs/${id}/retry`, { method: "POST" });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!job) {
    return <div className="panel">Загружаю…</div>;
  }

  const working = ACTIVE.includes(job.status);

  return (
    <div>
      <div className="toolbar">
        <div>
          <Link to="/" className="hint">← все стримы</Link>
          <h1 className="serif" style={{ margin: "6px 0 0" }}>
            {job.source_name || "Стрим"}
          </h1>
          <div className="meta">
            {job.duration ? `${fmtTime(job.duration)} · ` : ""}
            камера {job.settings.facecam_position}
          </div>
        </div>
        <div className="actions" style={{ marginTop: 0 }}>
          {working ? (
            <button className="btn secondary" disabled={busy} onClick={cancel}>
              {busy ? "Останавливаю…" : "Остановить"}
            </button>
          ) : null}
          {["cancelled", "error"].includes(job.status) ? (
            <button className="btn" disabled={busy} onClick={retry}>
              {busy ? "Запускаю…" : "Запустить снова"}
            </button>
          ) : null}
          {["ready", "done"].includes(job.status) ? (
            <button className="btn" disabled={busy || selectedIds.length === 0} onClick={render}>
              {busy ? "Отправляю…" : `Смонтировать выбранные (${selectedIds.length})`}
            </button>
          ) : null}
        </div>
      </div>

      {error ? <div className="error">{error}</div> : null}
      {job.error ? <div className="error">{job.error}</div> : null}

      {working || job.status === "cancelled" || job.status === "rendering" ? (
        <div className="panel" style={{ marginBottom: 18 }}>
          <div className="status">{statusLabel(job.status)}</div>
          <strong>{job.stage}</strong>
          <StageBars job={job} />
        </div>
      ) : null}

      <div className="grid">
        {job.moments.map((m) => (
          <article key={m.id} className={`card ${picked[m.id] ? "picked" : ""}`}>
            {m.output_path ? (
              <video key={m.output_path + (job.updated_at || "")} src={fileUrl(job.id, m.output_path, job.updated_at)} controls />
            ) : m.preview_path ? (
              <video src={fileUrl(job.id, m.preview_path, job.updated_at)} muted loop playsInline controls />
            ) : (
              <video src={`${fileUrl(job.id, "source.mp4")}#t=${m.start},${m.end}`} controls />
            )}
            <div className="body">
              <label className="check">
                <input
                  type="checkbox"
                  checked={!!picked[m.id]}
                  onChange={(e) => setPicked((p) => ({ ...p, [m.id]: e.target.checked }))}
                />
                <span className="score">{Math.round(m.score)}</span>
                {fmtTime(m.start)}–{fmtTime(m.end)}
              </label>
              <strong>{m.hook || m.title}</strong>
              <div className="meta">
                {m.layout_mode === "game_pip"
                  ? `игра + камера (${m.cam_position === "left" ? "слева" : "справа"})`
                  : "камера на весь кадр → 9:16"}
              </div>
              <div className="reason">{m.reason}</div>
              {m.tiktok_caption ? <div className="meta">{m.tiktok_caption}</div> : null}
              {m.output_path ? (
                <a className="hint" href={fileUrl(job.id, m.output_path)} download>
                  скачать mp4
                </a>
              ) : null}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
