// Operating-environment smoke test (Noblen AI 3.0, M7): drives the real UI in a
// browser against a running backend + worker + frontend. Not part of CI.
//
// Setup (see docs/development.md → "UI smoke test"):
//   backend:  AI_DEFAULT_PROVIDER=mock AI_DEFAULT_MODEL=mock-1 uvicorn app.main:app --port 8000
//   worker:   same env, python -m app.agents.worker
//   frontend: npm run build && npm start   (port 3000)
//   run:      npm i --no-save playwright-core && node e2e/smoke.mjs ./e2e-shots
// Env: UI_BASE (default http://localhost:3000), CHROMIUM_PATH (a Chromium binary).
// Use a fresh database: the script signs up a new organization.
import { mkdirSync } from "node:fs";
import { chromium } from "playwright-core";

const BASE = process.env.UI_BASE ?? "http://localhost:3000";
const OUT = process.argv[2] ?? "e2e-shots";
mkdirSync(OUT, { recursive: true });
const problems = [];
const log = (m) => console.log("•", m);

const browser = await chromium.launch(
  process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
);
const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });
page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()}`); });
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
page.on("response", (r) => {
  if (r.status() >= 400) problems.push(`HTTP ${r.status()} ${r.request().method()} ${r.url()}`);
});
const shot = (name) => page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });

// 1. Unauthenticated → redirected to login.
await page.goto(`${BASE}/approvals`);
await page.waitForURL(/\/login\?next=/);
log("guard redirects to /login with next");

// 2. Create an organization.
await page.getByRole("button", { name: "Create an organization" }).click();
await page.getByLabel("Organization name").fill("Kano Foods Ltd");
await page.getByLabel("Your name").fill("Amina Bello");
await page.getByLabel("Email").fill("amina@kanofoods.example.com");
await page.getByLabel("Password").fill("Password123!");
await page.getByRole("button", { name: "Create organization" }).click();
await page.waitForURL(/\/approvals$/);   // honours ?next=
log("signed up and returned to /approvals");
await page.getByText("Nothing is waiting for you").waitFor();

// 3. Hire Executive AI.
await page.getByRole("link", { name: "Workforce" }).click();
await page.getByText("Ready-made agents").waitFor();
await shot("01-workforce");
await page.getByRole("button", { name: "Add to workforce" }).first().click();
await page.waitForURL(/\/workforce\/[0-9a-f-]+$/);
await page.getByText("Give it a task").waitFor();
log("Executive AI instantiated");

// 4. A task that needs approval (mock model directive → notify_member, approval-gated).
await page.getByPlaceholder(/Brief me/).fill('Remind me [[tool:notify_member|{"recipient_email":"amina@kanofoods.example.com","title":"Board pack due Friday"}]]');
await page.getByRole("button", { name: "Run", exact: true }).click();
await page.getByText(/waiting for a person/).waitFor();
await shot("02-agent-awaiting-approval");
log("agent paused for approval");

// 5. Approvals inbox: the sidebar badge and the request, then approve.
await page.getByRole("link", { name: /Approvals/ }).click();
await page.getByText("notify_member").first().waitFor();
await shot("03-approvals-inbox");
await page.getByLabel("Note for the record").first().fill("Fine to send.");
await page.getByRole("button", { name: "Approve", exact: true }).first().click();
await page.getByText("Nothing is waiting for you").waitFor();
log("approved from the inbox");

// 6. Notification arrived (agent_message) → bell count.
await page.reload();
await page.getByRole("button", { name: /Notifications \(\d+ unread\)/ }).click();
await page.getByText("Board pack due Friday").first().waitFor();
await page.keyboard.press("Escape");
log("notification visible in the bell");

// 7. Runs list → trace.
await page.goto(`${BASE}/runs`);
await page.locator("table a").first().click();
await page.getByText("Trace", { exact: true }).waitFor();
await page.getByText(/Tool · notify_member/).waitFor();
await shot("04-run-trace");
log("run trace shows the tool step");

// 8. Add an open task through the Tasks page (the workflow condition needs one).
await page.getByRole("link", { name: "Tasks" }).click();
await page.getByLabel("New task title").fill("Call the flour supplier");
await page.getByRole("button", { name: "Add" }).click();
await page.getByText("Call the flour supplier").waitFor();
log("task added from the Tasks page");

// 9. Workflow: create from the example, activate, run, decide, complete (worker).
await page.getByRole("link", { name: "Workflows" }).click();
await page.getByRole("button", { name: "New workflow" }).click();
await page.getByLabel("Name").fill("Weekly review");
await page.getByRole("button", { name: "Save draft" }).click();
await page.getByText("Weekly review").waitFor();
await page.getByRole("button", { name: "Activate" }).click();
await page.getByRole("button", { name: "Run now" }).waitFor();
await page.getByRole("button", { name: "Run now" }).click();
await page.getByRole("button", { name: "Start run" }).click();
await page.waitForURL(/\/workflow-runs\//);
log("workflow run started");
// Worker picks it up: wait until it asks for a decision.
for (let i = 0; i < 30; i++) {
  await page.reload();
  if (await page.getByText("Waiting", { exact: true }).count()) break;
  await page.waitForTimeout(700);
}
await shot("05-workflow-waiting");
await page.goto(`${BASE}/approvals`);
await page.getByText(/Create a review task for \d+ open items\?/).waitFor();
await shot("06-workflow-approval");
await page.getByRole("button", { name: "Approve", exact: true }).first().click();
await page.getByText("Nothing is waiting for you").waitFor();
log("workflow approval decided");

// 10. Tasks page shows the task the workflow created.
await page.goto(`${BASE}/tasks`);
for (let i = 0; i < 30; i++) {
  if (await page.getByText("Weekly review").count()) break;
  await page.waitForTimeout(700);
  await page.reload();
}
await page.getByText("Weekly review").waitFor();
await shot("07-tasks");
log("workflow completed: task created");

// 10. Dashboard + integrations empty state.
await page.goto(`${BASE}/dashboard`);
await page.getByText("Recent agent runs").waitFor();
await page.waitForTimeout(500);
await shot("08-dashboard");
await page.goto(`${BASE}/integrations`);
await page.getByText("No connections yet").waitFor();
log("dashboard + integrations render");

// 11. Sign out clears the session.
await page.getByRole("button", { name: "Sign out" }).click();
await page.waitForURL(/\/login/);
await page.goto(`${BASE}/dashboard`);
await page.waitForURL(/\/login/);
log("sign-out clears the session");

await browser.close();
console.log(problems.length ? `PROBLEMS:\n${problems.join("\n")}` : "No console errors or failed API calls.");
process.exitCode = problems.some((p) => p.startsWith("HTTP") || p.startsWith("pageerror")) ? 1 : 0;
