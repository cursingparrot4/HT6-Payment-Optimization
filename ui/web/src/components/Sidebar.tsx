"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ICONS: Record<string, JSX.Element> = {
  dashboard: (
    <svg viewBox="0 0 20 20" fill="none" className="h-[18px] w-[18px]">
      <rect x="2.5" y="2.5" width="6.5" height="6.5" rx="1.8" fill="currentColor" opacity="0.9" />
      <rect x="11" y="2.5" width="6.5" height="6.5" rx="1.8" fill="currentColor" opacity="0.45" />
      <rect x="2.5" y="11" width="6.5" height="6.5" rx="1.8" fill="currentColor" opacity="0.45" />
      <rect x="11" y="11" width="6.5" height="6.5" rx="1.8" fill="currentColor" opacity="0.9" />
    </svg>
  ),
  cards: (
    <svg viewBox="0 0 20 20" fill="none" className="h-[18px] w-[18px]">
      <rect x="2" y="4.5" width="16" height="11" rx="2.2" stroke="currentColor" strokeWidth="1.6" />
      <path d="M2 8h16" stroke="currentColor" strokeWidth="1.6" />
      <path d="M5 12.5h4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  ),
  payments: (
    <svg viewBox="0 0 20 20" fill="none" className="h-[18px] w-[18px]">
      <path
        d="M4.5 7.5a6 6 0 0 1 10.6-2.4M15.5 12.5a6 6 0 0 1-10.6 2.4"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <path d="M15.5 2.5v3h-3M4.5 17.5v-3h3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  tracker: (
    <svg viewBox="0 0 20 20" fill="none" className="h-[18px] w-[18px]">
      <path
        d="M2.5 10h3l2-5 3.5 10 2.5-7 1.5 2h2.5"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  ),
  optimize: (
    <svg viewBox="0 0 20 20" fill="none" className="h-[18px] w-[18px]">
      <circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="1.6" />
      <path d="M10 3v3M10 14v3M3 10h3M14 10h3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="10" cy="10" r="2.2" fill="currentColor" />
    </svg>
  ),
};

const NAV = [
  { href: "/", label: "Dashboard", icon: "dashboard" },
  { href: "/cards", label: "Cards", icon: "cards" },
  { href: "/payments", label: "Payments", icon: "payments" },
  { href: "/optimize", label: "Optimizer", icon: "optimize" },
  { href: "/tracker", label: "Payment Tracker", icon: "tracker" },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-40 border-b border-white/55 bg-[#f4f5fa]/82 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
        <Link href="/" className="flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-[15px] bg-[#465bd8] text-white shadow-[0_16px_30px_-20px_rgba(70,91,216,0.82)]">
            <svg viewBox="0 0 20 20" fill="none" className="h-5 w-5">
              <path
                d="M4 7h9.5M11 3.5 14.5 7 11 10.5M16 13H6.5M9 9.5 5.5 13 9 16.5"
                stroke="white"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
          <span className="font-display text-[19px] font-semibold text-[#202332]">
            Switch<span className="text-[#465bd8]">Pay</span>
          </span>
        </Link>

        <nav className="hidden items-center gap-1 md:flex">
          {NAV.map((item) => {
            const active =
              item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-2 rounded-[15px] px-3 py-2 text-[13px] font-semibold transition-colors ${
                  active
                    ? "bg-white text-[#202332] shadow-sm"
                    : "text-[#73798a] hover:bg-white/70 hover:text-[#202332]"
                }`}
              >
                <span className={active ? "text-[#465bd8]" : "text-[#9aa1b2]"}>{ICONS[item.icon]}</span>
                {item.label}
              </Link>
            );
          })}
        </nav>

        <nav className="flex items-center gap-1 md:hidden">
          {NAV.map((item) => {
            const active =
              item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-label={item.label}
                className={`flex h-9 w-9 items-center justify-center rounded-[15px] transition-colors ${
                  active
                    ? "bg-white text-[#465bd8] shadow-sm"
                    : "text-[#73798a] hover:bg-white/70 hover:text-[#202332]"
                }`}
              >
                {ICONS[item.icon]}
              </Link>
            );
          })}
        </nav>

        <div className="hidden items-center gap-2 rounded-[15px] border border-white/75 bg-white/60 px-3 py-2 text-xs font-semibold text-[#73798a] lg:flex">
          <span className="h-2 w-2 rounded-full bg-[#465bd8]" />
          Synthetic sandbox
        </div>
      </div>
    </header>
  );
}
