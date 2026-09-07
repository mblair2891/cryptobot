async function post(url) {
  const res = await fetch(url, { method: "POST" });
  if (!res.ok) {
    const text = await res.text();
    alert(text || res.statusText);
    return null;
  }
  return res.json();
}

function switchMode(target, extra) {
  return fetch("/api/mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target, ...(extra || {}) }),
  });
}

document.addEventListener("click", async (e) => {
  const modeBtn = e.target.closest("[data-mode-target]");
  if (modeBtn) {
    e.preventDefault();
    const target = modeBtn.getAttribute("data-mode-target");
    const current = document.body.dataset.mode;
    const vercel = document.body.dataset.vercel === "1";
    const keys = document.body.dataset.keys === "present";
    if (target === current) return;
    if (target === "live" && vercel) {
      alert("Vercel hosts the demo UI only. Run Docker or a local process for live Coinbase trading.");
      return;
    }
    const modal = document.getElementById("mode-modal");
    const title = document.getElementById("mode-modal-title");
    const body = document.getElementById("mode-modal-body");
    const confirmLabel = document.getElementById("mode-confirm-label");
    const confirmInput = document.getElementById("mode-confirm-input");
    const cancelLabel = document.getElementById("mode-cancel-label");
    const cancelBox = document.getElementById("mode-cancel-orders");
    confirmLabel.hidden = true;
    cancelLabel.hidden = true;
    confirmInput.value = "";
    if (target === "live") {
      if (!keys) {
        alert("CDP key name + private key must be set in server env. The browser never receives the private key.");
        return;
      }
      title.textContent = "Enable LIVE trading";
      body.textContent = "Real Coinbase Advanced Trade orders. Type I UNDERSTAND THE RISK. Keys stay on the server.";
      confirmLabel.hidden = false;
    } else if (current === "live") {
      title.textContent = "Leave LIVE";
      body.textContent = "Cancel open live orders? Default is YES.";
      cancelLabel.hidden = false;
      cancelBox.checked = true;
    } else {
      const res = await switchMode(target);
      if (!res.ok) {
        alert(await res.text());
        return;
      }
      window.location.reload();
      return;
    }
    modal.showModal();
    modal.dataset.target = target;
    return;
  }
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
  if (form.id === "mode-form") {
    const modal = document.getElementById("mode-modal");
    const submitter = e.submitter;
    if (submitter && submitter.value === "cancel") return;
    e.preventDefault();
    const target = modal.dataset.target;
    const confirmation = document.getElementById("mode-confirm-input").value;
    const cancelLive = document.getElementById("mode-cancel-orders").checked;
    const res = await switchMode(target, { confirmation, cancel_live_orders: cancelLive });
    if (!res.ok) {
      alert(await res.text());
      return;
    }
    modal.close();
    window.location.reload();
    return;
  }
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
