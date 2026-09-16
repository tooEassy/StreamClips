import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Job, api } from "../api";
import { StageBars, statusLabel } from "../components/StageBars";

export default function Home() {
  const nav = useNavigate();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [facecam, setFacecam] = useState("auto");
  const [watermark, setWatermark] = useState("");
  const [count, setCount] = useState(12);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    const data = await api<{ jobs: Job[] }>("/api/jobs");
    setJobs(data.jobs);
  }

  useEffect(() => {
    api<{ watermark?: string }>("/api/health")
      .then((h) => {
        if (h.watermark) setWatermark((current) => current || h.watermark || "");
      })
      .catch(() => undefined);
    refresh().catch((e) => setError(String(e)));
    const t = setInterval(() => refresh().catch(() => undefined), 2000);
    return () => clearInterval(t);
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = new FormData();
      if (url.trim()) body.append("source_url", url.trim());
      if (file) body.append("file", file);
      body.append("facecam_position", facecam);
      body.append("propose_count", String(count));
      if (watermark.trim()) body.append("watermark", watermark.trim());
      const job = await api<Job>("/api/jobs", { method: "POST", body });
      nav(`/jobs/${job.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <form className="panel" onSubmit={onSubmit}>
        <h1 className="serif" style={{ marginTop: 0 }}>Новый стрим</h1>
        <p className="hint">
          Лучше ссылка на Twitch VOD. Файл — запасной путь, если запись ещё не на
          Twitch или VOD закрыт.
        </p>
        {error ? <div className="error">{error}</div> : null}
        <div className="row">
          <div>
            <label>Twitch VOD</label>
            <input
              type="text"
              placeholder="https://www.twitch.tv/videos/..."
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </div>
          <div>
            <label>Камера</label>
            <select value={facecam} onChange={(e) => setFacecam(e.target.value)}>
              <option value="auto">Авто: весь экран или угол</option>
              <option value="top_right">Игра, камера сверху справа</option>
              <option value="top_left">Игра, камера сверху слева</option>
              <option value="bottom_right">Игра, камера снизу справа</option>
              <option value="bottom_left">Игра, камера снизу слева</option>
            </select>
          </div>
          <div>
            <label>Сколько моментов показать</label>
            <input
              type="number"
              min={4}
              max={24}
              value={count}
              onChange={(e) => setCount(Number(e.target.value))}
            />
          </div>
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <div>
            <label>Водяной знак</label>
            <input
              type="text"
              placeholder="twitch.tv/ник"
              value={watermark}
              onChange={(e) => setWatermark(e.target.value)}
            />
          </div>
        </div>
        <label className="drop">
          Или локальный файл
          <input
            type="file"
            accept="video/*,.mkv,.mp4,.mov"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
          {file ? <div className="meta">{file.name}</div> : null}
        </label>
        <div className="actions">
          <button className="btn" disabled={busy || (!url.trim() && !file)}>
            {busy ? "Создаю…" : "Разобрать стрим"}
          </button>
          <span className="hint">Транскрипция large-v3: ~3 часа на M1 Pro обычно 40–90 минут, зато точнее.</span>
        </div>
      </form>

      <div className="jobs">
        {jobs.map((job) => (
          <Link className="job-row" key={job.id} to={`/jobs/${job.id}`}>
            <div>
              <strong>{job.source_name || job.source_url || job.id}</strong>
              <div className="meta">{job.stage}</div>
            </div>
            <div className="status">{statusLabel(job.status)}</div>
            <div className="meta">{job.moments.length ? `${job.moments.length} моментов` : ""}</div>
            {["queued", "downloading", "extracting_audio", "transcribing", "analyzing", "rendering"].includes(job.status) ? (
              <div className="job-stages">
                <StageBars job={job} compact />
              </div>
            ) : null}
          </Link>
        ))}
      </div>
    </>
  );
}
