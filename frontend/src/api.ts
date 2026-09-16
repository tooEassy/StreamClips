/// <reference types="vite/client" />

export type JobStatus =
  | "queued"
  | "downloading"
  | "extracting_audio"
  | "transcribing"
  | "analyzing"
  | "ready"
  | "rendering"
  | "done"
  | "error"
  | "cancelled";

export type StageBar = {
  key: string;
  label: string;
  progress: number;
  state: "pending" | "active" | "done";
  eta_sec?: number | null;
  processed_sec?: number | null;
  total_sec?: number | null;
};

export type CaptionWord = {
  word: string;
  start: number;
  end: number;
};

export type Moment = {
  id: string;
  start: number;
  end: number;
  score: number;
  hook: string;
  title: string;
  reason: string;
  on_screen_text: string;
  tiktok_caption: string;
  hashtags: string[];
  selected: boolean;
  preview_path: string | null;
  output_path: string | null;
  layout_mode?: "face_full" | "game_pip";
  cam_position?: string;
  face_cx?: number;
  caption_words?: CaptionWord[];
};

export type Job = {
  id: string;
  status: JobStatus;
  progress: number;
  stage: string;
  source_url: string | null;
  source_name: string | null;
  created_at: string;
  updated_at?: string;
  error: string | null;
  duration: number | null;
  moments: Moment[];
  settings: {
    facecam_position: string;
    propose_count: number;
    watermark: string;
  };
  stages?: Record<string, StageBar>;
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      detail = await res.text();
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export function fileUrl(jobId: string, rest: string, bust?: string) {
  const q = bust ? `?v=${encodeURIComponent(bust)}` : "";
  return `/api/jobs/${jobId}/files/${rest}${q}`;
}

export function fmtTime(seconds: number) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

export function fmtEta(seconds: number) {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h >= 1) return `~${h} ч ${String(m).padStart(2, "0")} мин`;
  if (m >= 1) return `~${m} мин`;
  return "~меньше минуты";
}
