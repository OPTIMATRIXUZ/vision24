"use client";

import { Maximize, MoreVertical, Pause, Play, Volume2, VolumeX } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useT } from "@/lib/locale";
import { cn } from "@/lib/utils";

const SKIP_S = 15;
const SPEEDS = [0.5, 1, 1.5, 2];

function formatTime(seconds: number) {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function ControlButton({
  label,
  onClick,
  children,
  className,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className={cn(
        "relative size-10 shrink-0 rounded-full text-white transition-colors hover:bg-white/10 sm:size-12",
        className,
      )}
    >
      <span className="absolute top-1/2 left-1/2 size-6 -translate-x-1/2 -translate-y-1/2">
        {children}
      </span>
    </button>
  );
}

export function VideoPlayer({
  src,
  title,
  poster,
  className,
}: {
  src: string;
  title?: string;
  poster?: string;
  className?: string;
}) {
  const t = useT();
  const containerRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const [speed, setSpeed] = useState(1);

  const [prevSrc, setPrevSrc] = useState(src);
  if (prevSrc !== src) {
    setPrevSrc(src);
    setPlaying(false);
    setCurrent(0);
    setDuration(0);
  }

  useEffect(() => {
    if (!menuOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [menuOpen]);

  const togglePlay = useCallback(() => {
    const el = videoRef.current;
    if (!el) return;
    if (el.paused) void el.play();
    else el.pause();
  }, []);

  const skip = useCallback((delta: number) => {
    const el = videoRef.current;
    if (!el) return;
    el.currentTime = Math.min(
      Math.max(el.currentTime + delta, 0),
      el.duration || Number.MAX_SAFE_INTEGER,
    );
  }, []);

  const seekToFraction = useCallback((fraction: number) => {
    const el = videoRef.current;
    if (!el || !Number.isFinite(el.duration)) return;
    el.currentTime = Math.min(Math.max(fraction, 0), 1) * el.duration;
  }, []);

  const onTrackPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    const rect = trackRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return;
    seekToFraction((event.clientX - rect.left) / rect.width);
  };

  const onTrackKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "ArrowRight") skip(5);
    else if (event.key === "ArrowLeft") skip(-5);
    else if (event.key === " " || event.key === "Enter") togglePlay();
    else return;
    event.preventDefault();
  };

  const toggleFullscreen = () => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void containerRef.current?.requestFullscreen();
  };

  const progress = duration > 0 ? (current / duration) * 100 : 0;

  return (
    <div
      ref={containerRef}
      className={cn(
        "relative flex flex-col justify-end overflow-hidden rounded-[6px] bg-black",
        className,
      )}
    >
      <video
        ref={videoRef}

        src={poster || src.includes("#") ? src : `${src}#t=0.1`}
        poster={poster}
        preload="metadata"
        playsInline
        onClick={togglePlay}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onTimeUpdate={(e) => setCurrent(e.currentTarget.currentTime)}
        onDurationChange={(e) => setDuration(e.currentTarget.duration)}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
        onVolumeChange={(e) => setMuted(e.currentTarget.muted)}
        className="absolute inset-0 size-full cursor-pointer object-contain"
      />

      <div className="relative flex flex-col gap-4 bg-gradient-to-b from-[rgba(8,9,13,0)] to-[rgba(8,9,13,0.9)] p-4 backdrop-blur-[5px]">
        <div className="flex items-end justify-between gap-4">
          {title && (
            <p className="truncate text-2xl leading-none font-medium text-white">{title}</p>
          )}
          <p className="ml-auto shrink-0 text-base leading-none font-medium text-white tabular-nums">
            {formatTime(current)} / {formatTime(duration)}
          </p>
        </div>

        <div
          ref={trackRef}
          role="slider"
          tabIndex={0}
          aria-label={t("player.seek")}
          aria-valuemin={0}
          aria-valuemax={Math.round(duration)}
          aria-valuenow={Math.round(current)}
          aria-valuetext={`${formatTime(current)} of ${formatTime(duration)}`}
          onPointerDown={onTrackPointerDown}
          onKeyDown={onTrackKeyDown}
          className="h-0.5 w-full cursor-pointer overflow-hidden rounded-[20px] bg-[#d9d9d9]/40 outline-offset-4"
        >
          <div className="h-full rounded-[20px] bg-white" style={{ width: `${progress}%` }} />
        </div>

        <div className="flex items-center justify-between gap-1">
          <div className="flex items-center gap-1 sm:gap-2">
            <ControlButton
              label={playing ? t("player.pause") : t("player.play")}
              onClick={togglePlay}
            >
              {playing ? (
                <Pause className="size-6" fill="currentColor" strokeWidth={0} />
              ) : (
                <Play className="size-6" fill="currentColor" strokeWidth={0} />
              )}
            </ControlButton>
            <ControlButton label={`Back ${SKIP_S} seconds`} onClick={() => skip(-SKIP_S)}>
              <span className="absolute inset-[-12.5%_-4.17%_-4.17%_-4.17%]">
                <img src="/icons/skip-back-15.svg" alt="" className="block size-full max-w-none" />
              </span>
            </ControlButton>
            <ControlButton label={`Forward ${SKIP_S} seconds`} onClick={() => skip(SKIP_S)}>
              <span className="absolute inset-[-12.5%_-4.17%_-4.17%_-4.17%]">
                <img
                  src="/icons/skip-forward-15.svg"
                  alt=""
                  className="block size-full max-w-none"
                />
              </span>
            </ControlButton>
          </div>

          <div className="flex items-center gap-1 sm:gap-2">
            <ControlButton
              label={muted ? t("player.unmute") : t("player.mute")}
              onClick={() => {
                const el = videoRef.current;
                if (el) el.muted = !el.muted;
              }}
            >
              {muted ? <VolumeX className="size-6" /> : <Volume2 className="size-6" />}
            </ControlButton>
            <ControlButton label={t("player.fullscreen")} onClick={toggleFullscreen}>
              <Maximize className="size-6" />
            </ControlButton>
            <div ref={menuRef} className="relative">
              <ControlButton label={t("player.more")} onClick={() => setMenuOpen((open) => !open)}>
                <MoreVertical className="size-5" />
              </ControlButton>
              {menuOpen && (
                <div className="absolute right-0 bottom-14 z-10 w-40 overflow-hidden rounded-lg bg-[rgba(8,9,13,0.92)] p-1 text-sm text-white shadow-lg backdrop-blur-sm">
                  <p className="px-2 py-1 text-xs text-white/50">{t("player.speed")}</p>
                  {SPEEDS.map((value) => (
                    <button
                      key={value}
                      type="button"
                      onClick={() => {
                        const el = videoRef.current;
                        if (el) el.playbackRate = value;
                        setSpeed(value);
                        setMenuOpen(false);
                      }}
                      className={cn(
                        "flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left hover:bg-white/10",
                        speed === value && "text-brand",
                      )}
                    >
                      {value}×{speed === value && <span aria-hidden>•</span>}
                    </button>
                  ))}
                  <a
                    href={src}
                    target="_blank"
                    rel="noreferrer"
                    onClick={() => setMenuOpen(false)}
                    className="mt-1 block border-t border-white/10 px-2 py-1.5 hover:bg-white/10"
                  >
                    {t("player.openOriginal")}
                  </a>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
