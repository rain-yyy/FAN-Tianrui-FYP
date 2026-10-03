"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

export default function LegacyDashboardRedirect() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/app/dashboard");
  }, [router]);

  return null;
}
