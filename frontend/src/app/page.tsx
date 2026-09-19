import Link from "next/link";

const services = [
  "AI Business Automation",
  "AI Sales & Lead Generation",
  "AI Customer Service",
  "AI Content & Media Automation",
  "AI Executive Assistant",
  "Custom AI Agents",
  "AI Knowledge & Document Intelligence",
  "AI Training & Implementation",
];

export default function HomePage() {
  return (
    <main className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4">
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-lg bg-noblen-600" aria-hidden />
            <span className="text-lg font-semibold text-noblen-900">
              Noblen AI
            </span>
          </div>
          <nav className="flex items-center gap-3">
            <Link
              href="/login"
              className="rounded-lg px-4 py-2 text-sm font-medium text-noblen-700 hover:bg-noblen-50"
            >
              Sign in
            </Link>
            <Link
              href="/dashboard"
              className="rounded-lg bg-noblen-600 px-4 py-2 text-sm font-medium text-white hover:bg-noblen-700"
            >
              Dashboard
            </Link>
          </nav>
        </div>
      </header>

      <section className="mx-auto max-w-6xl px-4 py-16 sm:py-24">
        <p className="mb-3 text-sm font-semibold uppercase tracking-wide text-noblen-600">
          Noblen AI Solutions
        </p>
        <h1 className="max-w-3xl text-4xl font-bold leading-tight text-slate-900 sm:text-5xl">
          One platform to deploy AI agents and automate your business.
        </h1>
        <p className="mt-5 max-w-2xl text-lg text-slate-600">
          A modular, multi-tenant AI automation platform built for Nigerian and
          African businesses, professionals, NGOs, institutions and enterprises —
          and international clients.
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <Link
            href="/login"
            className="rounded-lg bg-noblen-600 px-6 py-3 font-medium text-white hover:bg-noblen-700"
          >
            Get started
          </Link>
          <a
            href="https://www.noblenai.com"
            className="rounded-lg border border-slate-300 px-6 py-3 font-medium text-slate-700 hover:bg-slate-100"
          >
            Learn more
          </a>
        </div>

        <div className="mt-16 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {services.map((service) => (
            <div
              key={service}
              className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
            >
              <div className="mb-3 h-9 w-9 rounded-lg bg-noblen-100" aria-hidden />
              <p className="font-medium text-slate-800">{service}</p>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
