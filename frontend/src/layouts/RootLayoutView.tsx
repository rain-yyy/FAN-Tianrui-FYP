'use client';

import { Outlet } from 'react-router-dom';

export default function RootLayoutView() {
  return (
    <main className="min-h-screen relative overflow-hidden font-sans bg-[var(--background)] text-[var(--foreground)]">
      <div className="absolute inset-0 overflow-hidden pointer-events-none z-0 dark-bg-overlay">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_14%_18%,rgba(217,70,239,0.2),transparent_28%),radial-gradient(circle_at_85%_14%,rgba(99,102,241,0.24),transparent_34%),radial-gradient(circle_at_72%_82%,rgba(6,182,212,0.18),transparent_35%)]" />
        <div className="absolute inset-0 bg-[linear-gradient(130deg,rgba(255,255,255,0.02)_0%,rgba(255,255,255,0)_25%,rgba(255,255,255,0.04)_53%,rgba(255,255,255,0)_78%,rgba(255,255,255,0.02)_100%)]" />
      </div>
      <div className="relative z-10 min-h-screen">
        <Outlet />
      </div>
    </main>
  );
}
