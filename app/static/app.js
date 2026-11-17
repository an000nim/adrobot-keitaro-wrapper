const $ = (selector) => document.querySelector(selector);
const state = { campaign: null, drafts: [], offers: new Map(), stats: null };
const number = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
let busy = false;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function message(text, success = false) {
  $("#message").hidden = !text;
  $("#message").textContent = text;
  $("#message").className = success ? "success" : "";
}

async function api(path, method = "GET", body) {
  const response = await fetch("/api" + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const result = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(result.detail)
      ? result.detail.map((error) => error.msg).join("\n")
      : result.detail;
    throw new Error(detail || "Не удалось выполнить запрос");
  }
  return result;
}

async function run(task) {
  if (busy) return;
  busy = true;
  document.querySelectorAll("button").forEach((button) => { button.disabled = true; });
  message("");
  try {
    await task();
  } catch (error) {
    message(error.message);
  } finally {
    busy = false;
    document.querySelectorAll("button").forEach((button) => { button.disabled = false; });
  }
}

function fillSelect(selector, rows, preferred) {
  const select = $(selector);
  select.replaceChildren();
  for (const row of rows) {
    const option = element("option", row.name);
    option.value = row.id;
    option.selected = row.name === preferred;
    select.append(option);
  }
}

function actionButton(label, handler, className) {
  const button = element("button", label, className);
  button.type = "button";
  button.addEventListener("click", () => run(handler));
  return button;
}

async function loadStats() {
  state.stats = null;
  $("#stats-status").textContent = "Загружаем статистику…";
  try {
    state.stats = await api("/campaigns/" + state.campaign.id + "/stats");
    $("#stats-status").textContent = "Статистика за " + state.stats.date + " · " + state.stats.timezone + " · тренд кликов за 7 дней";
  } catch (error) {
    $("#stats-status").textContent = "Статистика недоступна. " + error.message;
  }
}

async function openCampaign(id) {
  state.campaign = await api("/campaigns/" + id);
  $("#campaign-section").hidden = false;
  $("#empty").hidden = true;
  $("#campaign-name").textContent = state.campaign.name;
  $("#campaign-number").textContent = "Кампания #" + id;
  $("#admin-link").href = state.campaign.admin_url;
  $("#campaign-link").textContent = state.campaign.url;
  $("#campaign-link").href = state.campaign.url;
  $("#campaign-id").value = id;
  await loadStreams();
}

async function loadStreams() {
  state.stats = null;
  state.drafts = await api("/campaigns/" + state.campaign.id + "/streams");
  renderStreams();
  await loadStats();
  renderStreams();
}

async function mutate(draft, action, offerId, share) {
  const updated = await api(
    "/campaigns/" + state.campaign.id + "/streams/" + draft.stream_id + "/draft",
    "PATCH", { revision: draft.revision, action, offer_id: offerId, share },
  );
  state.drafts = state.drafts.map((row) => row.stream_id === updated.stream_id ? updated : row);
  renderStreams();
}

async function commitOrReset(draft, action) {
  const updated = await api(
    "/campaigns/" + state.campaign.id + "/streams/" + draft.stream_id + "/" + action,
    "POST", { revision: draft.revision },
  );
  state.drafts = state.drafts.map((row) => row.stream_id === updated.stream_id ? updated : row);
  renderStreams();
  message(action === "push" ? "Поток сохранён в Keitaro." : "Поток загружен из Keitaro.", true);
}

function statsCell(streamId, offerId) {
  const cell = element("td", undefined, "numbers");
  if (!state.stats) {
    cell.textContent = "Нет данных";
    return cell;
  }
  const row = state.stats.rows.find((row) => Number(row.stream_id) === streamId && Number(row.offer_id) === offerId) || {};
  for (const [key, label] of [["clicks", "Клики"], ["conversions", "Конверсии"], ["revenue", "Доход"], ["cost", "Расход"], ["profit", "Прибыль"], ["roi", "ROI %"]]) {
    const line = element("div");
    const value = row[key] === null ? "—" : number.format(row[key] ?? 0);
    line.append(element("span", label + " "), document.createTextNode(value));
    cell.append(line);
  }
  return cell;
}

function trendCell(streamId, offerId) {
  const cell = element("td");
  if (!state.stats) return cell;
  const values = state.stats.trends.filter((row) => Number(row.stream_id) === streamId && Number(row.offer_id) === offerId);
  if (!values.length) {
    cell.append(element("span", "Нет кликов", "hint"));
    return cell;
  }
  const maximum = Math.max(...values.map((row) => Number(row.clicks)), 1);
  const bars = element("div", undefined, "bars");
  values.sort((a, b) => String(a.day).localeCompare(String(b.day)));
  for (const row of values) {
    const bar = element("span", undefined, "bar");
    bar.style.height = Math.max(2, Number(row.clicks) / maximum * 28) + "px";
    bar.title = row.day + ": " + row.clicks + " кликов";
    bars.append(bar);
  }
  cell.append(bars);
  return cell;
}

function offerRow(draft, offer) {
  const row = element("tr", undefined, offer.removed ? "removed" : "");
  const title = element("td", undefined, "offer-name");
  title.append(element("small", "#" + offer.offer_id), element("div", state.offers.get(offer.offer_id) || "Оффер #" + offer.offer_id));
  const preview = element("a", "Предпросмотр ↗");
  preview.href = "/api/offers/" + offer.offer_id + "/preview";
  preview.target = "_blank";
  preview.rel = "noopener noreferrer";
  title.append(preview);
  const share = element("td", undefined, "share");
  if (offer.removed) {
    share.textContent = "0% · удалён";
  } else {
    const input = element("input");
    input.type = "number";
    input.min = "0";
    input.max = "100";
    input.step = "1";
    input.value = offer.share;
    input.setAttribute("aria-label", "Доля оффера " + offer.offer_id);
    share.append(input, document.createTextNode("%"), element("br"));
    share.append(actionButton(offer.pinned ? "Открепить" : "Закрепить", async () => {
      if (offer.pinned) return mutate(draft, "unpin", offer.offer_id);
      if (!input.checkValidity() || input.value === "") throw new Error("Укажите целую долю от 0 до 100");
      await mutate(draft, "pin", offer.offer_id, Number(input.value));
    }));
  }
  const actions = element("td");
  actions.append(actionButton(offer.removed ? "Вернуть" : "Удалить", () => mutate(draft, offer.removed ? "restore" : "remove", offer.offer_id)));
  row.append(title, share, statsCell(draft.stream_id, offer.offer_id), trendCell(draft.stream_id, offer.offer_id), actions);
  return row;
}

function renderStreams() {
  const container = $("#streams");
  container.replaceChildren();
  for (const draft of state.drafts) {
    const stream = draft.stream;
    const card = element("article", undefined, "stream" + (draft.dirty ? " dirty" : ""));
    const title = element("div", undefined, "stream-title");
    const heading = element("div");
    heading.append(element("h3", stream.name), element("p", "#" + draft.stream_id + " · позиция " + stream.position, "hint"));
    if (state.stats) {
      const clicks = state.stats.rows
        .filter((row) => Number(row.stream_id) === draft.stream_id)
        .reduce((sum, row) => sum + Number(row.clicks || 0), 0);
      heading.append(element("p", "Клики сегодня · " + number.format(clicks), "hint"));
    }
    title.append(heading, element("span", draft.dirty ? "Есть изменения" : "Сохранено", "tag"));
    card.append(title);
    const body = element("div", undefined, "stream-body");
    if (draft.conflict) body.append(element("p", "Поток изменился в Keitaro. Перед сохранением обновите данные.", "notice"));
    if (stream.schema !== "landings") {
      const countries = (stream.filters || []).filter((filter) => filter.name === "country").map((filter) => filter.payload).flat();
      body.append(element("div", stream.action_payload || (stream.action_options || {}).url || "Прямой редирект"));
      body.append(element("p", countries.length ? "Страны: " + countries.join(", ") : "Без фильтра по стране", "hint"));
      card.append(body);
      container.append(card);
      continue;
    }
    const total = draft.offers.filter((row) => !row.removed).reduce((sum, row) => sum + row.share, 0);
    body.append(element("span", "Активные офферы · сумма долей " + total + "%", "hint"));
    const tools = element("div", undefined, "stream-tools");
    if (draft.dirty) tools.append(actionButton("Сохранить в Keitaro", () => commitOrReset(draft, "push"), "primary"));
    tools.append(actionButton("Отменить / обновить", () => commitOrReset(draft, "reset")));
    body.append(tools);
    card.append(body);
    const table = element("table");
    const head = element("thead");
    const headings = element("tr");
    for (const name of ["Оффер", "Доля", "Сегодня", "Тренд", "Действия"]) headings.append(element("th", name));
    head.append(headings);
    const rows = element("tbody");
    for (const offer of draft.offers) {
      if (!offer.removed || $("#show-removed").checked) rows.append(offerRow(draft, offer));
    }
    table.append(head, rows);
    const wrap = element("div", undefined, "table-wrap");
    wrap.append(table);
    card.append(wrap);
    const form = element("form", undefined, "add-offer");
    const input = element("input");
    input.setAttribute("list", "offers-list");
    input.setAttribute("aria-label", "ID добавляемого оффера");
    input.placeholder = "Найти оффер или ввести ID";
    input.required = true;
    form.append(input, element("button", "Добавить оффер"));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(() => {
        const id = Number(input.value);
        if (!Number.isInteger(id) || id <= 0) throw new Error("Выберите ID оффера из списка");
        return mutate(draft, "add", id);
      });
    });
    card.append(form);
    container.append(card);
  }
}

$("#open-form").addEventListener("submit", (event) => {
  event.preventDefault();
  run(() => {
    const id = Number($("#campaign-id").value);
    if (!Number.isInteger(id) || id <= 0) throw new Error("Укажите ID кампании");
    return openCampaign(id);
  });
});
$("#fetch").addEventListener("click", () => run(loadStreams));
$("#show-removed").addEventListener("change", renderStreams);
$("#create-form").addEventListener("submit", (event) => {
  event.preventDefault();
  run(async () => {
    const payload = {
      name: $("#name").value.trim(),
      geo: $("#geo").value.split(/[,;\s]+/).filter(Boolean),
      offer_id: Number($("#create-offer").value),
      domain_id: Number($("#domain").value), group_id: Number($("#group").value),
      traffic_source_id: Number($("#source").value),
    };
    const stored = JSON.parse(sessionStorage.getItem("creation") || "null");
    const requestId = stored && JSON.stringify(stored.payload) === JSON.stringify(payload)
      ? stored.requestId : crypto.randomUUID();
    sessionStorage.setItem("creation", JSON.stringify({ requestId, payload }));
    const result = await api("/campaigns", "POST", { ...payload, request_id: requestId });
    sessionStorage.removeItem("creation");
    await openCampaign(result.id);
    message("Кампания создана. Два потока настроены и активны.", true);
  });
});

run(async () => {
  const catalog = await api("/catalog");
  $("#name").value = catalog.prefix;
  fillSelect("#domain", catalog.domains);
  fillSelect("#group", catalog.groups, "FORTESTS");
  fillSelect("#source", catalog.sources, "FORTESTS");
  $("#connection").textContent = "Keitaro подключён · " + catalog.timezone;
  const [offers, campaigns] = await Promise.all([api("/offers"), api("/campaigns")]);
  for (const row of offers) {
    state.offers.set(row.id, row.name);
    const option = element("option");
    option.value = row.id;
    option.label = row.name;
    $("#offers-list").append(option);
  }
  for (const row of campaigns) {
    const option = element("option");
    option.value = row.id;
    option.label = row.name;
    $("#campaigns-list").append(option);
  }
  const pending = JSON.parse(sessionStorage.getItem("creation") || "null");
  if (pending) {
    $("#name").value = pending.payload.name;
    $("#geo").value = pending.payload.geo.join(", ");
    $("#create-offer").value = pending.payload.offer_id;
    $("#domain").value = pending.payload.domain_id;
    $("#group").value = pending.payload.group_id;
    $("#source").value = pending.payload.traffic_source_id;
    message("Предыдущая попытка создания не завершилась. Повторите с теми же параметрами для продолжения.");
  }
});
