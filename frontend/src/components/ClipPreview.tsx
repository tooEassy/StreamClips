import { useEffect, useRef } from "react";

type Props = {
  src: string;
  start?: number;
  end?: number;
  mode: "source" | "tiktok";
};

export function ClipPreview({ src, start = 0, end, mode }: Props) {
  const ref = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const seekIn = () => {
      if (mode !== "source") return;
      if (el.currentTime < start - 0.05 || (end != null && el.currentTime >= end)) {
        el.currentTime = start;
      }
    };

    const onTime = () => {
      if (mode !== "source" || end == null) return;
      if (el.currentTime >= end - 0.05) {
        el.pause();
        el.currentTime = start;
      }
    };

    el.addEventListener("loadedmetadata", seekIn);
    el.addEventListener("play", seekIn);
    el.addEventListener("timeupdate", onTime);
    return () => {
      el.removeEventListener("loadedmetadata", seekIn);
      el.removeEventListener("play", seekIn);
      el.removeEventListener("timeupdate", onTime);
    };
  }, [src, start, end, mode]);

  if (mode === "tiktok") {
    return (
      <div className="preview-stage tiktok">
        <div className="phone-frame" aria-label="Предпросмотр 9:16 как в TikTok">
          <video
            key={src}
            ref={ref}
            src={src}
            controls
            playsInline
            preload="metadata"
          />
        </div>
        <div className="preview-caption">полный кадр 9:16 · без обрезки</div>
      </div>
    );
  }

  return (
    <div className="preview-stage source">
      <video
        key={src}
        ref={ref}
        src={src}
        controls
        playsInline
        preload="metadata"
      />
      <div className="preview-caption">исходник 16:9 со звуком · нажми play</div>
    </div>
  );
}
