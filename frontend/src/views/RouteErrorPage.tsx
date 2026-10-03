"use client";

import { isRouteErrorResponse, Link, useRouteError } from "react-router-dom";
import { secondaryActionClass } from "@/components/wiki/TaskStatePanel";
import { t } from "@/lib/i18n";

export default function RouteErrorPage() {
  const error = useRouteError();
  const detail = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : t("unknownError");

  return (
    <div className="flex min-h-[70vh] items-center justify-center bg-bg p-6">
      <div className="w-full max-w-xl space-y-4 rounded-2xl border border-danger/40 bg-panel p-8">
        <h1 className="font-medium text-xl">{t("routeErrorTitle")}</h1>
        <p className="break-all font-mono text-fg-muted text-sm">{detail}</p>
        <Link to="/app/dashboard" className={secondaryActionClass}>
          {t("backToDashboard")}
        </Link>
      </div>
    </div>
  );
}
