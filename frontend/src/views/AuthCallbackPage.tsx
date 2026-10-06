"use client";

import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { t } from "@/lib/i18n";
import { supabase } from "@/lib/supabase";

export default function AuthCallbackPage() {
  const navigate = useNavigate();

  useEffect(() => {
    // Supabase automatically detects the hash (#access_token=...) on page load.
    // We just need to wait for the SIGNED_IN event to fire, then redirect.
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((event) => {
      if (event === "SIGNED_IN") {
        navigate("/app/dashboard", { replace: true });
      }
    });

    // Also handle the case where the session is already established
    // (e.g., user refreshes this page after sign-in was processed)
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (session) {
        navigate("/app/dashboard", { replace: true });
      }
    });

    return () => {
      subscription.unsubscribe();
    };
  }, [navigate]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg">
      <div className="space-y-3 text-center">
        <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-line-strong border-t-accent" />
        <p className="text-fg-muted text-sm">{t("signingIn")}</p>
      </div>
    </div>
  );
}
