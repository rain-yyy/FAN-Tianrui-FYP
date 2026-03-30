'use client';

import { Outlet } from 'react-router-dom';

export default function RootLayoutView() {
  return (
    <main className="min-h-screen relative overflow-hidden font-sans bg-[var(--background)] text-[var(--foreground)]">
      <div className="absolute inset-0 overflow-hidden pointer-events-none z-0">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_12%_16%,rgba(14,165,233,0.08),transparent_32%),radial-gradient(circle_at_88%_12%,rgba(13,148,136,0.06),transparent_36%),radial-gradient(circle_at_50%_100%,rgba(120,113,108,0.04),transparent_42%)]" />
      </div>
      <div className="relative z-10 min-h-screen">
        <Outlet />
      </div>
    </main>
  );
}
