"use client";

import { useState } from "react";
import { useSession } from "@/components/session-context";
import { Badge, Button, Card, Empty, ErrorBanner, Loading, PageHeader, inputClass } from "@/components/ui";
import { api } from "@/lib/client";
import { when } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";
import type { Page, Task } from "@/lib/types";

export default function TasksPage() {
  const { can } = useSession();
  const [view, setView] = useState<"open" | "mine" | "all">("open");
  const query = view === "open" ? "open_only=true" : view === "mine" ? "open_only=true&mine=true" : "";
  const tasks = useApi<Page<Task>>(`/tasks?limit=100&${query}`);
  const [title, setTitle] = useState("");
  const action = useAction();

  async function create(e: React.FormEvent) {
    e.preventDefault();
    const done = await action.run(() => api("/tasks", { method: "POST", body: { title } }));
    if (done) {
      setTitle("");
      tasks.reload();
    }
  }

  async function setStatus(task: Task, status: string) {
    const done = await action.run(() => api(`/tasks/${task.id}`, { method: "PATCH", body: { status } }));
    if (done) tasks.reload();
  }

  return (
    <>
      <PageHeader title="Tasks" description="Follow-ups and action items, whether a person or an agent created them." />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex rounded-lg border border-slate-300 bg-white p-0.5 text-sm">
          {(["open", "mine", "all"] as const).map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => setView(v)}
              className={`rounded-md px-3 py-1 ${view === v ? "bg-noblen-600 text-white" : "text-slate-600"}`}
            >
              {v === "open" ? "Open" : v === "mine" ? "Mine" : "All"}
            </button>
          ))}
        </div>
        {can("task:manage") && (
          <form onSubmit={create} className="flex flex-1 gap-2">
            <input
              required
              maxLength={255}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="New task"
              aria-label="New task title"
              className={`${inputClass} max-w-md`}
            />
            <Button type="submit" variant="primary" disabled={action.busy}>Add</Button>
          </form>
        )}
      </div>
      <ErrorBanner message={tasks.error ?? action.error} />
      <Card>
        {tasks.loading && !tasks.data ? (
          <Loading />
        ) : (tasks.data?.items.length ?? 0) === 0 ? (
          <Empty title="No tasks here" />
        ) : (
          <ul className="divide-y divide-slate-100">
            {tasks.data!.items.map((task) => (
              <li key={task.id} className="flex flex-wrap items-center gap-3 py-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-slate-800">{task.title}</p>
                  <p className="text-xs text-slate-500">
                    {task.created_by_agent_id ? "Created by an agent · " : ""}
                    {task.due_at ? `due ${when(task.due_at)} · ` : ""}
                    added {when(task.created_at)}
                  </p>
                </div>
                <Badge value={task.priority} tone={task.priority === "URGENT" || task.priority === "HIGH" ? "red" : "slate"} />
                <Badge value={task.status} />
                {can("task:manage") && task.status !== "DONE" && (
                  <Button disabled={action.busy} onClick={() => setStatus(task, "DONE")}>Mark done</Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
