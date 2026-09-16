import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CaptionWord, Job, api, fileUrl, fmtTime } from "../api";
import { ClipPreview } from "../components/ClipPreview";
import { StageBars, statusLabel } from "../components/StageBars";

const ACTIVE = ["queued", "downloading", "extracting_audio", "transcribing", "analyzing", "rendering"];

export default function JobPage() {
  const { id } = useParams();
  const [job, setJob] = useState<Job | null>(null);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, CaptionWord[]>>({});
  const [openCaptions, setOpenCaptions] = useState<Record<string, boolean>>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  const [showHidden, setShowHidden] = useState(false);

  async function refresh() {
    if (!id) return;
    const data = await api<Job>(`/api/jobs/${id}`);
    setJob(data);
    setPicked((prev) => {
      if (Object.keys(prev).length) return prev;
      const chosen = data.moments.filter((m) => m.selected || m.output_path);
      const source = chosen.length ? chosen : data.moments.slice(0, 5);
      const next: Record<string, boolean> = {};
      source.forEach((m) => {
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

  async function toggleCaptions(momentId: string) {
    const next = !openCaptions[momentId];
    setOpenCaptions((p) => ({ ...p, [momentId]: next }));
    if (!next || drafts[momentId] || !id) return;
    try {
      const data = await api<{ words: CaptionWord[] }>(`/api/jobs/${id}/moments/${momentId}/captions`);
      setDrafts((p) => ({ ...p, [momentId]: data.words }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  function updateWord(momentId: string, index: number, word: string) {
    setDrafts((p) => {
      const list = [...(p[momentId] || [])];
      list[index] = { ...list[index], word };
      return { ...p, [momentId]: list };
    });
  }

  function removeWord(momentId: string, index: number) {
    setDrafts((p) => ({
      ...p,
      [momentId]: (p[momentId] || []).filter((_, i) => i !== index),
    }));
  }

  async function remountCaptions(momentId: string) {
    if (!id) return;
    setSavingId(momentId);
    setError(null);
    try {
      await api(`/api/jobs/${id}/moments/${momentId}/captions`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ words: drafts[momentId] || [] }),
      });
      await api(`/api/jobs/${id}/moments/${momentId}/rerender`, { method: "POST" });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSavingId(null);
    }
  }

  if (!job) {
    return <div className="panel">Загружаю…</div>;
  }

  const working = ACTIVE.includes(job.status);
  const focused =
    job.status === "rendering" ||
    job.status === "done" ||
    job.moments.some((m) => m.output_path);
  const visibleMoments = job.moments.filter((m) => {
    if (showHidden || !focused) return true;
    if (m.selected || m.output_path) return true;
    if (job.status === "rendering" && picked[m.id]) return true;
    return false;
  });
  const hiddenCount = job.moments.length - visibleMoments.length;

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
          {hiddenCount > 0 ? (
            <button className="btn secondary" onClick={() => setShowHidden((v) => !v)}>
              {showHidden ? "Скрыть остальные" : `Показать остальные (${hiddenCount})`}
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
        {visibleMoments.map((m) => (
          <article key={m.id} className={`card ${picked[m.id] ? "picked" : ""} ${m.output_path ? "card-clip" : "card-source"}`}>
            {m.output_path ? (
              <ClipPreview
                src={fileUrl(job.id, m.output_path, job.updated_at)}
                mode="tiktok"
              />
            ) : (
              <ClipPreview
                src={`${fileUrl(job.id, "source.mp4")}#t=${m.start}`}
                start={m.start}
                end={m.end}
                mode="source"
              />
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
                {["top_left", "top_right", "bottom_left", "bottom_right"].includes(job.settings.facecam_position)
                  ? "игра на весь кадр + вебка сверху"
                  : m.layout_mode === "game_pip"
                    ? `игра + камера (${m.cam_position === "left" ? "слева" : "справа"})`
                    : "камера на весь кадр → 9:16"}
              </div>
              <div className="reason">{m.reason}</div>
              {m.tiktok_caption ? (
                <div className="tiktok-copy">
                  <div className="meta">описание для TikTok</div>
                  <pre>{m.tiktok_caption}</pre>
                  <button
                    type="button"
                    className="btn secondary"
                    onClick={() => navigator.clipboard.writeText(m.tiktok_caption)}
                  >
                    копировать
                  </button>
                </div>
              ) : null}
              {m.output_path ? (
                <a className="hint" href={fileUrl(job.id, m.output_path)} download>
                  скачать mp4
                </a>
              ) : null}
              {m.output_path ? (
                <div className="caption-edit">
                  <button
                    type="button"
                    className="btn secondary"
                    onClick={() => toggleCaptions(m.id)}
                  >
                    {openCaptions[m.id] ? "Скрыть субтитры" : "Править субтитры"}
                  </button>
                  {openCaptions[m.id] ? (
                    <>
                      <p className="meta">
                        Поправь слова, если Whisper ошибся, и перемонтируй только этот клип.
                      </p>
                      <div className="caption-words">
                        {(drafts[m.id] || []).map((word, index) => (
                          <span key={`${word.start}-${index}`} className="caption-chip">
                            <input
                              type="text"
                              value={word.word}
                              onChange={(e) => updateWord(m.id, index, e.target.value)}
                            />
                            <button
                              type="button"
                              className="chip-del"
                              onClick={() => removeWord(m.id, index)}
                              aria-label="удалить слово"
                            >
                              ×
                            </button>
                          </span>
                        ))}
                      </div>
                      <button
                        type="button"
                        className="btn"
                        disabled={savingId === m.id || working}
                        onClick={() => remountCaptions(m.id)}
                      >
                        {savingId === m.id ? "Монтирую…" : "Сохранить и перемонтировать"}
                      </button>
                    </>
                  ) : null}
                </div>
              ) : null}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
