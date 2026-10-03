"use client";

import Image from "next/image";
import { useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import Auth from "@/components/Auth";
import { t } from "@/lib/i18n";
import { useAuth } from "@/providers/AuthProvider";
import { prefetchRouteModule } from "@/router/prefetch";

export default function LoginPage() {
  const { user, isLoading } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  useEffect(() => {
    prefetchRouteModule("dashboard");
  }, []);

  useEffect(() => {
    if (isLoading || !user) return;
    const nextPath = searchParams.get("next");
    navigate(nextPath || "/app/dashboard", { replace: true });
  }, [isLoading, navigate, searchParams, user]);

  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden bg-bg p-4">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 bottom-0 h-[32rem] bg-[radial-gradient(50%_70%_at_50%_100%,rgb(243_128_32/0.2),transparent_70%)]"
      />
      <div className="relative w-full max-w-md space-y-8 text-center">
        <div className="space-y-4">
          <Image
            src="/logo.png"
            alt=""
            width={44}
            height={44}
            className="mx-auto"
            priority
          />
          <h1 className="font-medium text-4xl tracking-tight md:text-5xl">
            {t("loginTitle")}
          </h1>
          <p className="text-fg-muted">{t("loginDetail")}</p>
        </div>
        <Auth />
      </div>
    </div>
  );
}
