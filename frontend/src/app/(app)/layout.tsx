import { AppShell } from "@/components/app-shell";
import { SessionProvider } from "@/components/session-context";

export default function OperatingEnvironmentLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <AppShell>{children}</AppShell>
    </SessionProvider>
  );
}
