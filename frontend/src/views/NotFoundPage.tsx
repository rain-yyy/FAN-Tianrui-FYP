"use client";

import { Link } from "react-router-dom";
import { secondaryActionClass } from "@/components/wiki/TaskStatePanel";
import { t } from "@/lib/i18n";

export default function NotFoundPage() {
  return (
    <div className="flex min-h-[70vh] items-center justify-center p-6">
      <div className="w-full max-w-md space-y-4 rounded-2xl border border-line bg-panel p-8 text-center">
        <p className="font-mono text-5xl text-fg tabular-nums">404</p>
        <p className="text-fg-muted">{t("notFoundDetail")}</p>
        <Link to="/app/dashboard" className={secondaryActionClass}>
          {t("backToDashboard")}
        </Link>
      </div>
    </div>
  );
}
