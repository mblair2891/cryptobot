async function post(url) {
  const res = await fetch(url, { method: "POST" });
  if (!res.ok) {
    const text = await res.text();
    alert(text || res.statusText);
    return null;
  }
  return res.json();
}

document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-action]");
  if (!btn) return;
  e.preventDefault();
  const action = btn.getAttribute("data-action");
  const id = btn.getAttribute("data-id");
  if (action === "kill" && !confirm("Trip kill switch? All opens will be cancelled and AI stopped.")) return;
  if (action === "stop" && !confirm("Stop and archive this bot?")) return;
  const map = {
    pause: `/api/bots/${id}/pause`,
    resume: `/api/bots/${id}/resume`,
    stop: `/api/bots/${id}/stop`,
    kill: "/api/kill",
    unkill: "/api/unkill",
    "ai-pause": "/api/ai/pause",
    "ai-resume": "/api/ai/resume",
    "ai-step": "/api/ai/step",
  };
  const url = map[action];
  if (!url) return;
  await post(url);
  window.location.reload();
});

if (document.body && document.body.dataset.mode === "demo") {
  setInterval(() => {
    fetch("/api/tick", { method: "POST" }).catch(() => {});
  }, 2000);
  setInterval(() => {
    const tag = document.activeElement && document.activeElement.tagName;
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
    if (document.querySelector("form.js-create-bot")) return;
    window.location.reload();
  }, 10000);
}

document.addEventListener("submit", async (e) => {
  const form = e.target;
  if (!form.classList.contains("js-create-bot")) return;
  e.preventDefault();
  const data = Object.fromEntries(new FormData(form).entries());
  ["investment", "lower_price", "upper_price", "take_profit_pct", "stop_loss_pct", "grid_step_pct"].forEach((k) => {
    if (data[k] === "") delete data[k];
  });
  data.grid_levels = Number(data.grid_levels || 21);
  data.trailing_up = form.trailing_up?.checked || false;
  data.trailing_down = form.trailing_down?.checked || false;
  const res = await fetch("/api/bots", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    alert(await res.text());
    return;
  }
  const bot = await res.json();
  window.location.href = `/bots/${bot.bot_id}`;
});
