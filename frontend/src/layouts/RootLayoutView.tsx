"use client";

import { Outlet } from "react-router-dom";

export default function RootLayoutView() {
  return (
    <div className="min-h-dvh bg-bg font-sans text-fg">
      <Outlet />
    </div>
  );
}
