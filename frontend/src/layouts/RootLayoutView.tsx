"use client";

import { Outlet } from "react-router-dom";

export default function RootLayoutView() {
  return (
    <div className="min-h-dvh bg-paper font-sans text-ink">
      <Outlet />
    </div>
  );
}
