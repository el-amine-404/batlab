"use strict";

// The input sheet. The TV cannot say which input is showing, so the page shows
// the one last selected here, and calls it that.

let inputList = [];

async function loadInputs() {
  inputList = await api(`/api/tv/${current.mac}/inputs`).catch(() => []);
  renderInputLabel();
}

function renderInputLabel() {
  const chosen = state.power === "on" && !state.app && inputList.find((i) => i.id === state.last_input);
  $("#input-label").textContent = chosen ? chosen.name : "Input";
}

function openInputs() {
  buzz();
  $("#inputs-list").replaceChildren(...inputList.map((input) => {
    const last = input.id === state.last_input && !state.app;
    const detail = [input.detail, last && "last selected"].filter(Boolean).join(" · ");
    return el("button", { type: "button", class: "btn input-choice", onclick: () => chooseInput({ id: input.id }, input.name) },
      icon(input.id === "tv" ? "tv" : "source"),
      el("span", {}, input.name, ...(detail ? [el("small", {}, detail)] : [])),
    );
  }));
  $("#inputs").showModal();
}

async function chooseInput(body, name) {
  buzz();
  $("#inputs").close();
  const btn = $("#input-open");
  btn.classList.add("working");
  toast(name ? `Switching to ${name}…` : "Next HDMI", false, 12000);
  try {
    await api(`/api/tv/${current.mac}/input`, body);
    toast(name ? `Sent: ${name}` : "Next HDMI");
    refreshSoon(300);
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.classList.remove("working");
  }
}

function bindInputs() {
  $("#input-open").addEventListener("click", openInputs);
  $("#inputs-close").addEventListener("click", () => $("#inputs").close());
  $("#input-next").addEventListener("click", () => chooseInput({ next: true }));
  $("#input-list-tv").addEventListener("click", () => $("#inputs").close());
  $("#inputs").addEventListener("click", (e) => { if (e.target === $("#inputs")) $("#inputs").close(); });
}
