"use client";

import { ReactNode } from "react";

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="animate-fade-up mb-8 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-[28px] font-semibold text-slate-950 sm:text-[34px]">{title}</h1>
        {subtitle ? (
          <p className="mt-2 max-w-3xl text-[15px] leading-7 text-slate-600">
            {subtitle}
          </p>
        ) : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

const BADGE_TONES: Record<string, { chip: string; dot: string }> = {
  slate: { chip: "bg-[#f4f5f8] text-[#6a7182] ring-[#dde2eb]", dot: "bg-[#9aa1b2]" },
  green: { chip: "bg-emerald-50 text-emerald-700 ring-emerald-200", dot: "bg-[#188a72]" },
  amber: { chip: "bg-amber-50 text-amber-800 ring-amber-200", dot: "bg-amber-500" },
  rose: { chip: "bg-rose-50 text-rose-700 ring-rose-200", dot: "bg-rose-500" },
  teal: { chip: "bg-indigo-50 text-indigo-700 ring-indigo-200", dot: "bg-[#465bd8]" },
  sky: { chip: "bg-sky-50 text-sky-700 ring-sky-200", dot: "bg-sky-500" },
};

export function Badge({
  tone = "slate",
  dot = false,
  children,
}: {
  tone?: "slate" | "green" | "amber" | "rose" | "teal" | "sky";
  dot?: boolean;
  children: ReactNode;
}) {
  const t = BADGE_TONES[tone];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md px-2 py-[3px] text-[11px] font-medium ring-1 ${t.chip}`}
    >
      {dot && <span className={`h-1.5 w-1.5 rounded-full ${t.dot}`} />}
      {children}
    </span>
  );
}

const BAR_TONES: Record<string, string> = {
  teal: "bg-[#465bd8]",
  emerald: "bg-[#188a72]",
  amber: "bg-amber-500",
  rose: "bg-rose-600",
};

export function ProgressBar({
  value,
  tone = "teal",
}: {
  value: number; // 0..100
  tone?: "teal" | "emerald" | "amber" | "rose";
}) {
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-[#dde2eb]">
      <div
        className={`h-full rounded-full ${BAR_TONES[tone]} transition-all duration-500`}
        style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
      />
    </div>
  );
}

export function utilizationTone(bps: number): "emerald" | "amber" | "rose" {
  if (bps <= 3000) return "emerald";
  if (bps <= 7000) return "amber";
  return "rose";
}

export function Modal({
  title,
  open,
  onClose,
  children,
}: {
  title: string;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/55 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="panel animate-modal-in max-h-[90vh] w-full max-w-lg overflow-y-auto p-5">
        <div className="mb-5 flex items-center justify-between">
          <h2 className="text-base font-semibold text-slate-950">{title}</h2>
          <button
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-md text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
            aria-label="Close"
          >
            <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4">
              <path d="M5 5l10 10M15 5 5 15" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function EmptyState({ message, hint }: { message: string; hint?: string }) {
  return (
    <div className="panel flex flex-col items-center justify-center px-6 py-16 text-center">
      <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600">
        <svg viewBox="0 0 24 24" fill="none" className="h-6 w-6">
          <rect x="3" y="6" width="18" height="13" rx="2.5" stroke="currentColor" strokeWidth="1.7" />
          <path d="M3 10h18" stroke="currentColor" strokeWidth="1.7" />
        </svg>
      </div>
      <p className="text-sm font-semibold text-slate-700">{message}</p>
      {hint ? <p className="mt-1 text-[13px] text-slate-400">{hint}</p> : null}
    </div>
  );
}
