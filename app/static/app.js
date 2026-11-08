// The home page. The first message is typed in the hero (or picked from an
// example); from then on the page shows the chat and the live application.

const $ = (id) => document.getElementById(id);
const messages = $("messages");
const input = $("input");
const STATUS_TEXT = { collecting: "Collecting", confirming: "Checking with you", done: "Complete" };

let sessionId = null; // created by the server on the first message
let busy = false;

function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text; // textContent, never innerHTML: no injected HTML
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

function setBusy(on) {
  busy = on;
  $("send").disabled = on;
  input.disabled = on;
}

// Switch from the hero to the chat, then send the first message.
function start(text) {
  text = text.trim();
  if (!text) return;
  document.body.classList.add("chatting");
  $("workspace").hidden = false;
  window.scrollTo({ top: 0 });
  send(text);
}

async function send(text) {
  text = text.trim();
  if (busy || !text) return;
  setBusy(true);
  addMessage("user", text);
  input.value = "";
  const typing = addMessage("agent typing", "Thinking…");

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: text }),
    });
    const data = await res.json().catch(() => ({}));
    typing.remove();
    if (!res.ok) {
      addMessage("error", data.error || "That message didn't go through. Send it again.");
      return;
    }
    sessionId = data.session_id;
    addMessage("agent", data.reply);
    renderForm(data.form);
  } catch {
    typing.remove();
    addMessage("error", "Couldn't reach the server. Check your connection and send it again.");
  } finally {
    setBusy(false);
    if (!input.disabled) input.focus();
  }
}

function listItems(list, items, render) {
  list.replaceChildren(...items.map((item) => {
    const li = document.createElement("li");
    render(li, item);
    return li;
  }));
}

function renderForm(form) {
  $("progress-bar").style.width = `${form.progress}%`;
  document.querySelector(".progress").setAttribute("aria-valuenow", form.progress);
  $("progress-text").textContent = `${form.progress}% complete`;
  $("status").textContent = STATUS_TEXT[form.status];
  $("status").dataset.status = form.status;

  const rows = form.fields.flatMap((f) => {
    const dt = document.createElement("dt");
    dt.textContent = f.label;
    const dd = document.createElement("dd");
    dd.className = f.status;
    dd.textContent = f.status === "checking" ? `${f.value}, checking` : f.value || "";
    return [dt, dd];
  });
  $("fields").replaceChildren(...rows);

  listItems($("coverage"), form.coverage, (li, rec) => {
    const strong = document.createElement("strong");
    strong.textContent = rec.policy;
    li.append(strong, document.createTextNode(` ${rec.why}`));
  });
  listItems($("flags"), form.flags, (li, flag) => { li.textContent = flag; });
  $("flags-box").hidden = form.flags.length === 0;

  if (form.status === "done") {
    $("download").href = `/api/sessions/${sessionId}/application.json`;
    $("download").hidden = false;
    setBusy(true); // conversation finished
    input.placeholder = "Application complete";
  }
}

$("start").addEventListener("submit", (e) => {
  e.preventDefault();
  start($("start-input").value);
});

$("examples").addEventListener("click", (e) => {
  if (e.target.dataset.message) start(e.target.dataset.message);
});

$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  send(input.value);
});

// Enter sends; Shift+Enter makes a new line.
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send(input.value);
  }
});

$("restart").addEventListener("click", () => location.reload());

renderForm(JSON.parse($("initial-form").textContent));
