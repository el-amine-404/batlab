"use strict";

// The input sheet.

// ------------------------------------------------------------------- inputs

async function openInputs() {
  buzz();
  const dlg = $("#inputs");
  if (!dlg.open) dlg.showModal();
  const inputs = await api(`/api/tv/${current.mac}/inputs`).catch(() => []);
  $("#inputs-list").replaceChildren(...inputs.map((input) =>
    el("button", { type: "button", class: "btn input-choice", onclick: () => chooseInput({ id: input.id }, input.name) },
      icon(input.id === "tv" ? "tv" : "source"),
      el("span", {}, input.name, ...(input.detail ? [el("small", {}, input.detail)] : [])),
    )));
}

async function chooseInput(body, name) {
  buzz();
  $("#inputs").close();
  toast(name ? `Switching to ${name}…` : "Next HDMI");
  try {
    await api(`/api/tv/${current.mac}/input`, body);
    if (name) toast(`Sent: ${name}`);
    refreshSoon(1500);
  } catch (err) {
    toast(err.message, true);
  }
}

function bindInputs() {
  $("#input-open").addEventListener("click", openInputs);
  $("#inputs-close").addEventListener("click", () => $("#inputs").close());
  $("#input-next").addEventListener("click", () => chooseInput({ next: true }));
  $("#input-list-tv").addEventListener("click", () => $("#inputs").close());
  $("#inputs").addEventListener("click", (e) => { if (e.target === $("#inputs")) $("#inputs").close(); });
}
