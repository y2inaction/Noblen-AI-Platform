import Link from "next/link";

const nav = [
  { group: "Overview", items: ["Dashboard"] },
  { group: "AI Agents", items: ["All Agents", "Create Agent", "Agent Runs"] },
  { group: "Conversations", items: ["Conversations"] },
  { group: "Sales", items: ["Leads", "Contacts", "Activities"] },
  { group: "Content", items: ["Calendar", "Drafts", "Published"] },
  { group: "Automation", items: ["Workflows", "Runs"] },
  { group: "Knowledge", items: ["Knowledge Bases", "Documents"] },
  { group: "Operations", items: ["Tasks", "Analytics", "Integrations"] },
  { group: "Account", items: ["Team", "Billing", "Settings"] },
];

const stats = [
  { label: "Active agents", value: "—" },
  { label: "Conversations (7d)", value: "—" },
  { label: "New leads (7d)", value: "—" },
  { label: "Workflow runs (7d)", value: "—" },
];

export default function DashboardPage() {
  return (
    <div className="flex min-h-screen">
      {/* Sidebar */}
      <aside className="hidden w-64 shrink-0 border-r border-slate-200 bg-white lg:block">
        <div className="flex items-center gap-2 border-b border-slate-200 px-5 py-4">
          <div className="h-8 w-8 rounded-lg bg-noblen-600" aria-hidden />
          <span className="font-semibold text-noblen-900">Noblen AI</span>
        </div>
        <nav className="space-y-5 px-3 py-4">
          {nav.map((section) => (
            <div key={section.group}>
              <p className="px-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                {section.group}
              </p>
              <ul className="mt-1 space-y-0.5">
                {section.items.map((item) => (
                  <li key={item}>
                    <span className="block cursor-default rounded-lg px-2 py-1.5 text-sm text-slate-700 hover:bg-noblen-50">
                      {item}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
      </aside>

      {/* Main */}
      <div className="flex-1">
        <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-4">
          <h1 className="text-lg font-semibold text-slate-900">Dashboard</h1>
          <Link href="/" className="text-sm text-noblen-700 hover:underline">
            Sign out
          </Link>
        </header>

        <main className="p-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {stats.map((stat) => (
              <div
                key={stat.label}
                className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
              >
                <p className="text-sm text-slate-500">{stat.label}</p>
                <p className="mt-2 text-2xl font-bold text-slate-900">{stat.value}</p>
              </div>
            ))}
          </div>

          <div className="mt-6 rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center">
            <p className="font-medium text-slate-700">Foundation ready</p>
            <p className="mt-1 text-sm text-slate-500">
              This is the Phase 1 dashboard shell. Live data (agents,
              conversations, leads, workflows) is wired in later phases as the
              backend modules land.
            </p>
          </div>
        </main>
      </div>
    </div>
  );
}
