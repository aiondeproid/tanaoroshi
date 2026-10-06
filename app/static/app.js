"use strict";

// ---------- 状態 ----------
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch {} },
};
const state = {
  meta: null,
  user: store.get("tanaoroshi.user"),
  ym: null,
  pin: null,
};

const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const sheet = $("#sheet");

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v) => { const n = parseFloat(v); return Number.isFinite(n) && n >= 0 ? n : 0; };
const fmtKg = (v) => (v == null ? "-" : `${Math.round(v * 1000) / 1000}kg`);
const jdate = (s) => { if (!s) return ""; const [y, m, d] = s.split("-").map(Number); return `${y}/${m}/${d}`; };
// "2026-09-26T14:03:05" → "2026/9/26 14:03"
const jdatetime = (s) => (s ? `${jdate(s.slice(0, 10))} ${s.slice(11, 16)}` : "");
// 端末の今日。開きっぱなしのタブレットでも日付が変われば追従する
const localToday = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const md = (s) => jdate(s).replace(/^\d+\//, ""); // "2026-09-26" → "9/26"
const mdRange = (from, to) => (from === to ? md(from) : `${md(from)}〜${md(to)}`);
const round3 = (v) => Math.round(v * 1000) / 1000;
// 実在庫 − 予想在庫。どちらかが無ければ null
const diffKg = (total, expected) => (total == null || expected == null ? null : round3(total - expected));
const fmtDiff = (d) => (d == null ? "" : d === 0 ? "差 0kg" : `差 ${d > 0 ? "+" : "−"}${Math.abs(d)}kg`);
const STATUS = { none: "未入力", ok: "レ", warn: "× 1ヶ月未満", expired: "× 期限切れ" };

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (err ? " err" : "");
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), err ? 5000 : 2200);
}

// 管理者PINが要るファイルは <a href> では落とせないので、fetch してから保存させる
async function download(path, fallbackName) {
  const headers = {};
  if (state.user) headers["X-User"] = encodeURIComponent(state.user);
  if (state.pin) headers["X-Admin-Pin"] = state.pin;
  const res = await fetch(path, { headers });
  if (!res.ok) throw new Error(`ダウンロードできませんでした (${res.status})`);
  const m = /filename\*=UTF-8''([^;]+)/.exec(res.headers.get("Content-Disposition") || "");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(await res.blob());
  a.download = m ? decodeURIComponent(m[1]) : fallbackName;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

async function api(method, path, body, raw = false) {
  const headers = { "Content-Type": raw ? "application/octet-stream" : "application/json" };
  if (state.user) headers["X-User"] = encodeURIComponent(state.user);
  if (state.pin) headers["X-Admin-Pin"] = state.pin;
  const res = await fetch(path, { method, headers, body: body === undefined || raw ? body : JSON.stringify(body) });
  if (!res.ok) {
    let msg = `エラー (${res.status})`;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : "入力内容を確認してください";
    } catch {}
    throw new Error(msg);
  }
  return res.json();
}

// ---------- 期限判定（app/logic.py と同じ計算） ----------
const lastDay = (y, m) => new Date(y, m, 0).getDate(); // m は 1-12
function addMonths(iso, months) {
  const [y, m, d] = iso.split("-").map(Number);
  const idx = m - 1 + months;
  const ny = y + Math.floor(idx / 12), nm = ((idx % 12) + 12) % 12 + 1;
  const day = d === lastDay(y, m) ? lastDay(ny, nm) : Math.min(d, lastDay(ny, nm));
  return `${ny}-${String(nm).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}
function monthEnd(ym) { const [y, m] = ym.split("-").map(Number); return `${ym}-${String(lastDay(y, m)).padStart(2, "0")}`; }
function okLimit(ym) { return addMonths(monthEnd(ym), state.meta.warn_months); }
function judge(expiry, ym) {
  if (!expiry) return "none";
  if (expiry < state.meta.today) return "expired";
  if (expiry < okLimit(ym)) return "warn";
  return "ok";
}
function daysLeft(iso) {
  return Math.round((new Date(iso + "T00:00:00") - new Date(state.meta.today + "T00:00:00")) / 86400000);
}

// ---------- 画面の切り替え ----------
function currentYm() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

async function init() {
  state.meta = await api("GET", "/api/meta");
  try { state.ym = sessionStorage.getItem("tanaoroshi.ym") || currentYm(); } catch { state.ym = currentYm(); }
  try { state.pin = sessionStorage.getItem("tanaoroshi.pin"); } catch {}
  const ymInput = $("#ym");
  ymInput.value = state.ym;
  ymInput.addEventListener("change", () => {
    if (!ymInput.value) return;
    state.ym = ymInput.value;
    try { sessionStorage.setItem("tanaoroshi.ym", state.ym); } catch {}
    route();
  });
  $("#who").addEventListener("click", () => go("#/who"));
  window.addEventListener("hashchange", route);
  route();
}

// 同じURLへの移動では hashchange が起きないので、そのときは自分で描き直す
function go(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

function updateWho() { $("#who").textContent = state.user ? `${state.user}` : "名前を選ぶ"; }

async function route() {
  closeSheet();
  updateWho();
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  try {
    if (parts[0] === "admin") return await renderAdmin(parts[1] || "items");
    if (parts[0] === "who" || !state.user || !state.meta.staff.includes(state.user)) return renderWho();
    if (parts[0] === "cat") return await renderCategory(Number(parts[1]));
    if (parts[0] === "alerts") return await renderAlerts();
    return await renderHome();
  } catch (e) {
    view.innerHTML = `<p class="notice">${esc(e.message)}</p>`;
  }
}

// ---------- 名前選択 ----------
function renderWho() {
  const staff = state.meta.staff;
  view.innerHTML = `
    <h1>あなたの名前を選んでください</h1>
    ${staff.length ? `<div class="names">${staff.map((n) => `<button class="btn${n === state.user ? " primary" : ""}" data-name="${esc(n)}">${esc(n)}</button>`).join("")}</div>`
      : `<p class="notice">名前がまだ登録されていません。管理画面の「担当者」から登録してください。</p>`}
    <p style="margin-top:32px"><a href="#/admin/staff">管理画面へ</a></p>`;
  view.querySelectorAll("[data-name]").forEach((b) => b.addEventListener("click", () => {
    state.user = b.dataset.name;
    store.set("tanaoroshi.user", state.user);
    go("#/");
  }));
}

// ---------- トップ ----------
async function renderHome() {
  const s = await api("GET", `/api/months/${state.ym}/summary`);
  const cats = state.meta.categories;
  let warn = 0, expired = 0;
  Object.values(s.categories).forEach((c) => { warn += c.warn; expired += c.expired; });
  const groups = [];
  cats.forEach((c) => {
    let g = groups.find((x) => x.name === c.grp);
    if (!g) groups.push((g = { name: c.grp, cats: [] }));
    g.cats.push(c);
  });
  const [y, m] = state.ym.split("-").map(Number);
  view.innerHTML = `
    <h1>${y}年${m}月の棚卸</h1>
    <p class="muted">判定基準：期限が ${jdate(s.ok_limit)} 以降なら「レ」（月末 ${jdate(s.month_end)} から${state.meta.warn_months}ヶ月）</p>
    ${warn + expired
      ? `<a class="banner warn" href="#/alerts">期限警告 ${warn + expired}件（うち期限切れ ${expired}件）→ 一覧と対応</a>`
      : `<a class="banner ok" href="#/alerts">期限警告はありません</a>`}
    ${groups.map((g) => `
      <h2>${esc(g.name)}</h2>
      <div class="grid">${g.cats.map((c) => catCard(c, s.categories[c.id])).join("")}</div>`).join("")}
    <h2>出力・管理</h2>
    <div class="row">
      <a class="btn primary" href="/api/months/${state.ym}/export.xlsx" style="text-decoration:none;display:inline-flex;align-items:center">Excelで出力</a>
      <a class="btn" href="#/admin" style="text-decoration:none;display:inline-flex;align-items:center">管理画面</a>
    </div>`;
}

function catCard(c, s) {
  s = s || { total: 0, entered: 0, warn: 0, expired: 0, approvals: {} };
  const pct = s.total ? Math.round((s.entered / s.total) * 100) : 0;
  const done = s.total && s.entered === s.total;
  return `<a class="card" href="#/cat/${c.id}">
    <div class="row"><span class="title">${esc(c.name)}</span><span class="spacer"></span>
      ${s.expired ? `<span class="pill s-expired">期限切れ ${s.expired}</span>` : ""}
      ${s.warn ? `<span class="pill s-warn">× ${s.warn}</span>` : ""}</div>
    <div class="bar${done ? " done" : ""}"><i style="width:${pct}%"></i></div>
    <div class="row"><span class="muted">入力 ${s.entered} / ${s.total}${s.counted_from ? `・棚卸日 ${mdRange(s.counted_from, s.counted_to)}` : ""}</span><span class="spacer"></span>
      <span class="dots">${state.meta.roles.map((r) => `<span class="dot${s.approvals[r] ? " on" : ""}">${esc(r)}</span>`).join("")}</span></div>
  </a>`;
}

// ---------- 分類（品目一覧） ----------
const catView = { filter: "all", q: "" };

async function renderCategory(id) {
  const d = await api("GET", `/api/months/${state.ym}/categories/${id}`);
  const draw = () => {
    const q = catView.q.trim().toLowerCase();
    const items = d.items.filter((it) => {
      if (catView.filter === "todo" && it.entry_id) return false;
      if (catView.filter === "x" && !["warn", "expired"].includes(it.status)) return false;
      return !q || it.code.toLowerCase().includes(q) || it.name.toLowerCase().includes(q);
    });
    $("#items").innerHTML = items.length ? items.map((it) => `
      <button class="item${it.active ? "" : " inactive"}" data-id="${it.id}">
        <span class="main"><span class="code">${esc(it.code)}・${esc(it.storage)}</span><br><span class="name">${esc(it.name)}</span></span>
        <span class="side"><span class="pill s-${it.entry_id ? it.status : "none"}">${it.entry_id ? STATUS[it.status] : "未入力"}</span>
          ${it.entry_id ? `<br>${fmtKg(it.total_kg)}<br>${it.expiry_date ? jdate(it.expiry_date) : "期限指定無し"}<br><span class="counted">棚卸 ${md(it.counted_on)} ${esc(it.counted_by)}</span>` : ""}
          ${it.expected_kg != null ? `<br><span class="expected">予想 ${fmtKg(it.expected_kg)}${it.entry_id ? ` <span class="${diffKg(it.total_kg, it.expected_kg) ? "diff-ng" : ""}">${fmtDiff(diffKg(it.total_kg, it.expected_kg))}</span>` : ""}</span>` : ""}</span>
      </button>`).join("") : `<p class="muted" style="padding:14px">該当する品目はありません</p>`;
    $("#items").querySelectorAll("[data-id]").forEach((b) => b.addEventListener("click", () =>
      openEntry(d.items.find((x) => x.id === Number(b.dataset.id)), d.category, () => renderCategory(id))));
    view.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c.dataset.f === catView.filter));
  };
  const left = d.items.filter((x) => !x.entry_id).length;
  view.innerHTML = `
    <a class="back" href="#/">← 戻る</a>
    <h1>${esc(d.category.name)}</h1>
    <input class="search" id="q" type="search" placeholder="コード・品名で探す" value="${esc(catView.q)}">
    <div class="chips">
      <button class="chip" data-f="all">すべて (${d.items.length})</button>
      <button class="chip" data-f="todo">未入力 (${left})</button>
      <button class="chip" data-f="x">×のみ</button>
    </div>
    <div class="list" id="items"></div>
    <h2>確認</h2>
    <div class="approvals">${state.meta.roles.map((r) => {
      const a = d.approvals[r];
      return `<div class="approval"><div><div class="role">${esc(r)}</div><div class="hint">${esc(state.meta.role_hint[r])}</div></div>
        <span class="spacer"></span>
        ${a ? `<span>${esc(a.staff_name)}<br><span class="muted">${esc(a.at.slice(0, 16).replace("T", " "))}${a.detail ? " " + esc(a.detail) : ""}</span></span>
               <button class="btn small danger" data-unapprove="${esc(r)}">取消</button>`
            : `<button class="btn primary" data-approve="${esc(r)}">確認する</button>`}
      </div>`;
    }).join("")}</div>`;
  $("#q").addEventListener("input", (e) => { catView.q = e.target.value; draw(); });
  view.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => { catView.filter = c.dataset.f; draw(); }));
  view.querySelectorAll("[data-approve]").forEach((b) => b.addEventListener("click", async () => {
    if (left && !confirm(`未入力が ${left} 件あります。確認済みにしますか？`)) return;
    try { await api("POST", `/api/months/${state.ym}/categories/${id}/approvals`, { role: b.dataset.approve }); toast("確認しました"); renderCategory(id); }
    catch (e) { toast(e.message, true); }
  }));
  view.querySelectorAll("[data-unapprove]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`${b.dataset.unapprove} の確認を取り消しますか？`)) return;
    try { await api("DELETE", `/api/months/${state.ym}/categories/${id}/approvals/${encodeURIComponent(b.dataset.unapprove)}`); renderCategory(id); }
    catch (e) { toast(e.message, true); }
  }));
  draw();
}

// ---------- 入力シート ----------
function openSheet(html) {
  sheet.innerHTML = `<div class="sheet-body">${html}</div>`;
  sheet.hidden = false;
  document.body.style.overflow = "hidden";
}
function closeSheet() {
  sheet.hidden = true;
  sheet.innerHTML = "";
  document.body.style.overflow = "";
}
sheet.addEventListener("click", (e) => { if (e.target === sheet) closeSheet(); });

// ---------- 大きいカレンダー ----------
// 端末標準の日付ピッカーはタブレットだと小さいので、日付欄（readonly）をタップしたらこちらを出す
const cal = document.createElement("div");
cal.className = "cal";
cal.hidden = true;
document.body.append(cal);
const isoDate = (y, m, d) => `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;

function openCalendar(input) {
  const picked = input.value;
  const start = (picked || localToday()).split("-").map(Number);
  let y = start[0], m = start[1];
  const choose = (v) => {
    input.value = v;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    cal.hidden = true;
  };
  const draw = () => {
    const first = new Date(y, m - 1, 1).getDay();
    const days = new Date(y, m, 0).getDate();
    const today = localToday();
    const cells = Array.from({ length: first }, () => `<span></span>`);
    for (let d = 1; d <= days; d++) {
      const v = isoDate(y, m, d);
      const out = (input.min && v < input.min) || (input.max && v > input.max);
      const wd = (first + d - 1) % 7;
      cells.push(`<button type="button" data-v="${v}" ${out ? "disabled" : ""}
        class="${v === picked ? "on" : ""} ${v === today ? "today" : ""} ${wd === 0 ? "sun" : wd === 6 ? "sat" : ""}">${d}</button>`);
    }
    cal.innerHTML = `<div class="cal-body">
      <div class="cal-nav">
        <button type="button" data-y="-1">«<small>前年</small></button>
        <button type="button" data-m="-1">‹<small>前月</small></button>
        <b>${y}年${m}月</b>
        <button type="button" data-m="1">›<small>翌月</small></button>
        <button type="button" data-y="1">»<small>翌年</small></button>
      </div>
      <div class="cal-grid">${"日月火水木金土".split("").map((w, i) => `<i class="${i === 0 ? "sun" : i === 6 ? "sat" : ""}">${w}</i>`).join("")}${cells.join("")}</div>
      <div class="cal-foot">
        <button class="btn" type="button" data-act="clear">消す</button>
        <button class="btn" type="button" data-act="today">今日</button>
        <span class="spacer"></span>
        <button class="btn" type="button" data-act="close">閉じる</button>
      </div></div>`;
  };
  cal.onclick = (e) => {
    if (e.target === cal) { cal.hidden = true; return; }
    const b = e.target.closest("button");
    if (!b || b.disabled) return;
    if (b.dataset.v) return choose(b.dataset.v);
    if (b.dataset.y) y += Number(b.dataset.y);
    if (b.dataset.m) { m += Number(b.dataset.m); if (m < 1) { m = 12; y--; } if (m > 12) { m = 1; y++; } }
    if (b.dataset.act === "clear") return choose("");
    if (b.dataset.act === "today") { const t = localToday(); if (!input.max || t <= input.max) return choose(t); }
    if (b.dataset.act === "close") { cal.hidden = true; return; }
    draw();
  };
  draw();
  cal.hidden = false;
}
document.addEventListener("click", (e) => {
  const input = e.target.closest?.("input[type=date][readonly]");
  if (!input) return;
  e.preventDefault();
  openCalendar(input);
});

function actionFields(v) {
  return `<div class="box" id="action-box">
    <div class="label">期限切れ・1ヶ月未満の対応</div>
    <div class="seg" id="action-seg">${state.meta.actions.map((a) => `<button type="button" data-v="${esc(a)}" class="${v.action === a ? "on" : ""}">${esc(a)}</button>`).join("")}</div>
    <div class="field"><span>予定日</span><input type="date" readonly id="action_date" value="${esc(v.action_date || "")}"></div>
    <textarea id="action_note" placeholder="メモ（いつ使う、どこへ移動する など）">${esc(v.action_note || "")}</textarea>
  </div>`;
}
function bindSeg(el, onChange) {
  el.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    const on = !b.classList.contains("on") || el.dataset.required;
    el.querySelectorAll("button").forEach((x) => x.classList.remove("on"));
    if (on) b.classList.add("on");
    onChange && onChange();
  }));
}
const segValue = (el) => el.querySelector("button.on")?.dataset.v || "";

function openEntry(it, cat, done) {
  const split = cat.locations === "split";
  const cw = it.entry_id ? it.entry_case_weight ?? it.case_weight : it.case_weight;
  const pc = it.pallet_cases; // 1パレットのケース数（保存時はマスタの値で計算する）
  const v = it.entry_id ? it : { room_cases: 0, room_kg: 0, wh_cases: 0, wh_kg: 0, wh_pallets: 0, expiry_kind: "賞" };
  // 端数は3か所に分けて入れる。内訳がない古い入力は合計を1つ目に入れる
  const KG_PARTS = 3;
  const kgParts = (key) => {
    const parts = v[key + "_kg_parts"] ? JSON.parse(v[key + "_kg_parts"]) : [v[key + "_kg"] || 0];
    return Array.from({ length: KG_PARTS }, (_, i) => parts[i] || 0);
  };
  const stepper = (id) => `<div class="stepper"><button type="button" data-step="${id}" data-d="-1">−</button>
          <input id="${id}" inputmode="decimal" value="${v[id] || 0}">
          <button type="button" data-step="${id}" data-d="1">＋</button></div>`;
  // 倉庫だけパレットも数える。1パレットのケース数が未設定の品目は出さない
  const pallet = (key) => key !== "wh" ? "" : pc
    ? `<div class="field"><span>パレット</span>${stepper("wh_pallets")}<span class="muted">× ${pc}ケース</span></div>`
    : `<div class="muted">1パレットのケース数が未設定のため、パレットは入力できません（管理画面で設定）</div>`;
  const loc = (key, label) => `
    <div class="box"><div class="label">${label}</div>
      ${pallet(key)}
      <div class="field"><span>ケース</span>${stepper(key + "_cases")}</div>
      <div class="field"><span>端数</span><div class="kg-parts">${kgParts(key).map((x, i) =>
        `<input class="num" id="${key}_kg${i}" inputmode="decimal" value="${x || ""}" placeholder="0">`).join("")}</div> kg</div>
    </div>`;
  const expiryHtml = {
    date: `<div class="field"><span>期限</span><input type="date" readonly id="expiry_date" value="${esc(v.expiry_date || "")}"></div>`,
    mfg: `<div class="field"><span>製造日</span><input type="date" readonly id="mfg_date" value="${esc(v.mfg_date || "")}"></div>
          <div class="muted">期限 = 製造日 ＋ ${it.mfg_months}ヶ月 → <b id="calc_expiry">-</b></div>`,
    none: `<div class="muted">この品目は期限の指定がありません</div>`,
  }[it.expiry_mode];

  openSheet(`
    <div class="row"><h3>${esc(it.name)}</h3></div>
    <div class="muted">${esc(it.code)}・${esc(it.storage)}・ケース重量 ${cw ? cw + "kg" : "未設定"}</div>
    <div class="box"><div class="field"><span>棚卸日</span>
        <input type="date" readonly id="counted_on" value="${esc(it.counted_on || localToday())}" max="${localToday()}"></div>
      <div class="muted">${it.entry_id ? `最後の保存：${jdatetime(it.counted_at)}（${esc(it.counted_by)}）` : "今日の日付が入ります。別の日に数えた分はここで直してください"}</div></div>
    ${cw ? "" : `<div class="notice">ケース重量が未設定です。ケース数ではなく「端数」に重さ(kg)を入れてください。</div>`}
    ${split ? loc("room", "資材室") + loc("wh", "倉庫／パレット") : loc("room", "在庫")}
    <div class="box"><div class="row"><span class="label">総重量</span><span class="spacer"></span><span class="total" id="total">0kg</span></div>
      <div class="muted" id="formula"></div></div>
    <div class="box"><div class="label">予想在庫</div>
      <div class="field"><span>予想</span><input class="num" id="expected_kg" inputmode="decimal" value="${it.expected_kg ?? ""}" placeholder="未入力"> kg
        <button class="btn small" type="button" id="save-expected">予想だけ保存</button></div>
      <div class="muted" id="expected_diff"></div></div>
    <div class="box"><div class="label">賞味期限・使用期限</div>
      <div class="seg" id="kind-seg" data-required="1">${["賞", "使", "凍"].map((k) => `<button type="button" data-v="${k}" class="${v.expiry_kind === k ? "on" : ""}">${{ 賞: "賞味期限", 使: "使用期限(開封)", 凍: "冷凍の使用期限" }[k]}</button>`).join("")}</div>
      ${expiryHtml}
      <div class="judge s-none" id="judge"></div>
    </div>
    ${actionFields(v)}
    <div class="actions-bar">
      <button class="btn" type="button" id="close">閉じる</button>
      ${it.entry_id ? `<button class="btn danger" type="button" id="del">入力取消</button>` : ""}
      <button class="btn primary" type="button" id="save">保存</button>
    </div>`);

  const val = (id) => $("#" + id)?.value;
  const partsOf = (key) => Array.from({ length: KG_PARTS }, (_, i) => num(val(`${key}_kg${i}`)));
  const kgOf = (key) => round3(partsOf(key).reduce((a, b) => a + b, 0));
  const expectedValue = () => { const t = val("expected_kg").trim(); return t === "" ? null : num(t); };
  // 変わっていれば予想在庫を保存する
  const saveExpected = async () => {
    const kg = expectedValue();
    if (kg === (it.expected_kg ?? null)) return false;
    await api("PUT", `/api/months/${state.ym}/items/${it.id}/expected`, { kg });
    it.expected_kg = kg;
    return true;
  };
  const recalc = () => {
    const pallets = split && pc ? num(val("wh_pallets")) : 0;
    const cases = round3(num(val("room_cases")) + (split ? num(val("wh_cases")) : 0) + pallets * pc);
    const loose = round3(kgOf("room") + (split ? kgOf("wh") : 0));
    const total = round3(cases * (cw || 0) + loose);
    $("#total").textContent = `${total}kg`;
    const exp = expectedValue();
    const d = diffKg(total, exp);
    $("#expected_diff").textContent = exp == null ? "入れると総重量との差を表示します" : `総重量 ${total}kg − 予想 ${exp}kg → ${fmtDiff(d)}`;
    $("#expected_diff").className = d ? "diff-ng" : "muted";
    const pl = pallets ? `（うちパレット ${pallets}枚 × ${pc}ケース）` : "";
    $("#formula").textContent = cw ? `${cases}ケース${pl} × ${cw}kg ＋ 端数 ${loose}kg` : `端数の合計 ${loose}kg`;
    let expiry = null;
    if (it.expiry_mode === "date") expiry = val("expiry_date") || null;
    if (it.expiry_mode === "mfg" && val("mfg_date")) { expiry = addMonths(val("mfg_date"), it.mfg_months); $("#calc_expiry").textContent = jdate(expiry); }
    const st = it.expiry_mode === "none" ? "ok" : judge(expiry, state.ym);
    const j = $("#judge");
    j.className = `judge s-${st}`;
    j.textContent = st === "none" ? "期限を入れると判定します" : st === "ok" ? "レ（1ヶ月以上あり）" :
      st === "warn" ? `× 月末から1ヶ月未満（${jdate(okLimit(state.ym))} より前）` : "× 期限切れ";
    $("#action-box").hidden = !["warn", "expired"].includes(st);
  };
  sheet.querySelectorAll("[data-step]").forEach((b) => b.addEventListener("click", () => {
    const inp = $("#" + b.dataset.step);
    inp.value = Math.max(0, num(inp.value) + Number(b.dataset.d));
    recalc();
  }));
  sheet.querySelectorAll("input").forEach((i) => i.addEventListener("input", recalc));
  bindSeg($("#kind-seg"));
  bindSeg($("#action-seg"));
  $("#close").addEventListener("click", closeSheet);
  $("#del")?.addEventListener("click", async () => {
    if (!confirm("この品目の入力を取り消しますか？")) return;
    try { await api("DELETE", `/api/months/${state.ym}/items/${it.id}`); closeSheet(); toast("取り消しました"); done(); }
    catch (e) { toast(e.message, true); }
  });
  $("#save").addEventListener("click", async () => {
    const body = {
      room_cases: num(val("room_cases")), room_kg: kgOf("room"), room_kg_parts: partsOf("room"),
      wh_cases: split ? num(val("wh_cases")) : 0, wh_pallets: split && pc ? num(val("wh_pallets")) : 0, wh_kg: split ? kgOf("wh") : 0, wh_kg_parts: split ? partsOf("wh") : null,
      expiry_kind: segValue($("#kind-seg")) || "賞",
      expiry_date: val("expiry_date") || null, mfg_date: val("mfg_date") || null,
      action: segValue($("#action-seg")), action_date: val("action_date") || null, action_note: val("action_note") || "",
      counted_on: val("counted_on") || null,
    };
    if (body.counted_on && body.counted_on > localToday()) return toast("棚卸日に未来の日付は入れられません", true);
    try { await saveExpected(); await api("PUT", `/api/months/${state.ym}/items/${it.id}`, body); closeSheet(); toast("保存しました"); done(); }
    catch (e) { toast(e.message, true); }
  });
  $("#save-expected").addEventListener("click", async () => {
    try { if (await saveExpected()) { toast("予想在庫を保存しました"); done(); } else toast("予想在庫は変わっていません"); }
    catch (e) { toast(e.message, true); }
  });
  recalc();
}

// ---------- 期限警告一覧 ----------
async function renderAlerts() {
  const d = await api("GET", `/api/months/${state.ym}/alerts`);
  view.innerHTML = `
    <a class="back" href="#/">← 戻る</a>
    <h1>期限警告一覧</h1>
    <p class="muted">期限が近い順。タップして対応を記録します。</p>
    <div class="list">${d.items.length ? d.items.map((it) => {
      const left = daysLeft(it.expiry_date);
      return `<button class="item" data-id="${it.id}">
        <span class="main"><span class="code">${esc(it.category_name)}・${esc(it.code)}</span><br><span class="name">${esc(it.name)}</span>
          <br><span class="muted">${fmtKg(it.total_kg)}・${esc(it.expiry_kind)} ${jdate(it.expiry_date)}（${left < 0 ? `${-left}日超過` : `あと${left}日`}）</span>
          <br><span class="counted">棚卸 ${md(it.counted_on)} ${esc(it.counted_by)}</span></span>
        <span class="side"><span class="pill s-${it.status}">${STATUS[it.status]}</span><br>
          ${it.action ? `<b>${esc(it.action)}</b>${it.action_date ? "<br>" + jdate(it.action_date) : ""}` : `<span style="color:var(--ng)">対応未記入</span>`}</span>
      </button>`;
    }).join("") : `<p class="muted" style="padding:14px">警告はありません</p>`}</div>`;
  view.querySelectorAll("[data-id]").forEach((b) => b.addEventListener("click", () => {
    const it = d.items.find((x) => x.id === Number(b.dataset.id));
    openSheet(`<h3>${esc(it.name)}</h3>
      <div class="muted">${esc(it.code)}・${fmtKg(it.total_kg)}・${esc(it.expiry_kind)} ${jdate(it.expiry_date)}</div>
      ${actionFields(it)}
      <div class="actions-bar"><button class="btn" id="close">閉じる</button><button class="btn primary" id="save">保存</button></div>`);
    bindSeg($("#action-seg"));
    $("#close").addEventListener("click", closeSheet);
    $("#save").addEventListener("click", async () => {
      try {
        await api("PUT", `/api/months/${state.ym}/items/${it.id}/action`, {
          action: segValue($("#action-seg")), action_date: $("#action_date").value || null, action_note: $("#action_note").value,
        });
        closeSheet(); toast("保存しました"); renderAlerts();
      } catch (e) { toast(e.message, true); }
    });
  }));
}

// ---------- 管理画面 ----------
const EXPIRY_MODE = { date: "期限を入力", mfg: "製造日から計算", none: "期限指定無し" };
const ENTITY = { item: "品目", category: "分類", staff: "担当者", entry: "棚卸入力", expected: "予想在庫", approval: "確認" };
const adminView = { q: "", cat: "", inactive: false, hq: "", hentity: "" };

async function renderAdmin(tab) {
  if (!state.user) {
    view.innerHTML = `<p class="notice">先に名前を選んでください（変更履歴に残すため）。</p>
      <p><a href="#/who">名前を選ぶ</a></p>`;
    if (!state.meta.staff.length) {
      view.innerHTML = `<h1>はじめに</h1><p>担当者を1人登録すると使い始められます。管理者PINを入れて、最初の名前を登録してください。</p>
        <div class="form-grid" style="max-width:360px"><label>管理者PIN<input class="text" id="pin" type="password" inputmode="numeric"></label>
        <label>名前<input class="text" id="first"></label><button class="btn primary" id="go">登録</button></div>`;
      $("#go").addEventListener("click", async () => {
        const name = $("#first").value.trim();
        if (!name) return;
        state.pin = $("#pin").value;
        state.user = name;
        try {
          await api("POST", "/api/admin/staff", { name });
          try { sessionStorage.setItem("tanaoroshi.pin", state.pin); } catch {}
          store.set("tanaoroshi.user", name);
          state.meta = await api("GET", "/api/meta");
          go("#/admin/staff");
        } catch (e) { state.user = null; state.pin = null; toast(e.message, true); }
      });
    }
    return;
  }
  if (!state.pin) {
    view.innerHTML = `<a class="back" href="#/">← 戻る</a><h1>管理画面</h1>
      <div class="form-grid" style="max-width:360px"><label>管理者PIN<input class="text" id="pin" type="password" inputmode="numeric" autofocus></label>
      <button class="btn primary" id="go">開く</button></div>`;
    const go = async () => {
      state.pin = $("#pin").value;
      try {
        await api("POST", "/api/admin/login");
        try { sessionStorage.setItem("tanaoroshi.pin", state.pin); } catch {}
        renderAdmin(tab);
      } catch (e) { state.pin = null; toast(e.message, true); }
    };
    $("#go").addEventListener("click", go);
    $("#pin").addEventListener("keydown", (e) => e.key === "Enter" && go());
    return;
  }
  const tabs = { items: "品目", categories: "分類", staff: "担当者", expected: "予想在庫", history: "変更履歴" };
  view.innerHTML = `<a class="back" href="#/">← 戻る</a><h1>管理画面</h1>
    <nav class="tabs">${Object.entries(tabs).map(([k, v]) => `<a href="#/admin/${k}" class="${k === tab ? "on" : ""}">${v}</a>`).join("")}</nav>
    <div id="admin"></div>`;
  try {
    await { items: adminItems, categories: adminCategories, staff: adminStaff, expected: adminExpected, history: adminHistory }[tab]();
  } catch (e) {
    if (/PIN/.test(e.message)) { state.pin = null; try { sessionStorage.removeItem("tanaoroshi.pin"); } catch {} return renderAdmin(tab); }
    throw e;
  }
}

async function adminItems() {
  const [items, cats] = await Promise.all([api("GET", "/api/admin/items"), api("GET", "/api/admin/categories")]);
  const el = $("#admin");
  el.innerHTML = `
    <div class="row"><input class="search" id="aq" type="search" placeholder="コード・品名" value="${esc(adminView.q)}" style="flex:1;min-width:180px">
      <select id="acat"><option value="">すべての分類</option>${cats.map((c) => `<option value="${c.id}" ${String(c.id) === adminView.cat ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
      <label class="row"><input type="checkbox" id="ainactive" ${adminView.inactive ? "checked" : ""}>終売も表示</label>
      <button class="btn primary" id="add">＋ 品目を追加</button></div>
    <div class="row" style="margin-top:8px"><button class="btn" id="xout">Excelで出力</button>
      <button class="btn" id="xin">Excelで一括変更</button></div>
    <p class="muted" id="acount"></p>
    <div class="table-wrap"><table><thead><tr><th>コード</th><th>品名</th><th>分類</th><th>保管</th><th>ケース重量</th><th>パレット</th><th>期限</th><th>状態</th></tr></thead><tbody id="arows"></tbody></table></div>`;
  const draw = () => {
    const q = adminView.q.trim().toLowerCase();
    const rows = items.filter((i) => (adminView.inactive || i.active) && (!adminView.cat || String(i.category_id) === adminView.cat)
      && (!q || i.code.toLowerCase().includes(q) || i.name.toLowerCase().includes(q)));
    $("#acount").textContent = `${rows.length} 件`;
    $("#arows").innerHTML = rows.map((i) => `<tr class="click" data-id="${i.id}"><td>${esc(i.code)}</td><td>${esc(i.name)}</td><td>${esc(i.category_name)}</td>
      <td>${esc(i.storage)}</td><td>${i.case_weight == null ? `<span style="color:var(--ng)">未設定</span>` : i.case_weight + "kg"}</td>
      <td>${i.pallet_cases == null ? `<span class="muted">-</span>` : i.pallet_cases + "ケース"}</td>
      <td>${esc(EXPIRY_MODE[i.expiry_mode])}${i.expiry_mode === "mfg" ? `+${i.mfg_months}ヶ月` : ""}</td><td>${i.active ? "有効" : `<span style="color:var(--ng)">終売</span>`}</td></tr>`).join("");
    el.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", () => itemForm(items.find((x) => x.id === Number(tr.dataset.id)), cats)));
  };
  $("#aq").addEventListener("input", (e) => { adminView.q = e.target.value; draw(); });
  $("#acat").addEventListener("change", (e) => { adminView.cat = e.target.value; draw(); });
  $("#ainactive").addEventListener("change", (e) => { adminView.inactive = e.target.checked; draw(); });
  $("#add").addEventListener("click", () => itemForm(null, cats));
  $("#xout").addEventListener("click", () => download("/api/admin/items.xlsx", "品目マスタ.xlsx").catch((e) => toast(e.message, true)));
  $("#xin").addEventListener("click", itemsImport);
  draw();
}

// 品目マスタをExcelでまとめて変える。先に変更内容を見せて、確認してから反映する
function itemsImport() {
  openSheet(`<h3>Excelで品目を一括変更</h3>
    <ol class="steps" style="margin-top:12px">
      <li>「Excelで出力」で今の品目マスタをダウンロードする</li>
      <li>変えたい所を書き換えて保存する。新しいコードの行は品目の追加になります。行を消しても品目は消えません（終売は「状態」で）</li>
      <li>ファイルを選んで「内容を確認」→ 変更内容を見てから「反映する」</li>
    </ol>
    <div class="row"><input type="file" id="i_file" accept=".xlsx"><button class="btn" id="i_check">内容を確認</button></div>
    <div id="i_result"></div>
    <div class="actions-bar"><button class="btn" id="close">閉じる</button>
      <button class="btn primary" id="i_apply" disabled>反映する</button></div>`);
  const out = $("#i_result");
  let data = null;
  const send = async (apply) => api("POST", `/api/admin/items/import${apply ? "?apply=1" : ""}`, data, true);
  const table = (changes) => `<div class="table-wrap" style="margin-top:8px;max-height:45vh;overflow:auto"><table>
    <thead><tr><th>行</th><th>コード</th><th>品名</th><th>種類</th><th>変更内容</th></tr></thead>
    <tbody>${changes.map((c) => `<tr><td>${c.line}</td><td>${esc(c.code)}</td><td>${esc(c.name)}</td><td>${esc(c.kind)}</td><td>${esc(c.detail)}</td></tr>`).join("")}</tbody></table></div>`;
  $("#i_file").addEventListener("change", () => { data = null; out.innerHTML = ""; $("#i_apply").disabled = true; });
  $("#close").addEventListener("click", () => { closeSheet(); adminItems(); });
  $("#i_check").addEventListener("click", async () => {
    const f = $("#i_file").files[0];
    if (!f) return toast("ファイルを選んでください", true);
    $("#i_apply").disabled = true;
    try {
      data = await f.arrayBuffer();
      const r = await send(false);
      if (!r.ok) {
        out.innerHTML = `<div class="notice"><b>取り込めません（${r.errors.length}件のエラー）。直してからもう一度選んでください。</b>
          <ul>${r.errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>`;
      } else if (!r.changes.length) {
        out.innerHTML = `<div class="banner ok">${r.rows}件を読み込みました。変わる所はありません</div>`;
      } else {
        out.innerHTML = `<div class="banner warn">${r.rows}件を読み込みました。追加 ${r.added}件・変更 ${r.changed}件です。よければ「反映する」を押してください</div>${table(r.changes)}`;
        $("#i_apply").disabled = false;
      }
    } catch (e) { toast(e.message, true); }
  });
  $("#i_apply").addEventListener("click", async () => {
    $("#i_apply").disabled = true;
    try {
      const r = await send(true);
      if (!r.ok) { toast("取り込めませんでした。もう一度「内容を確認」してください", true); return; }
      closeSheet(); toast(`追加 ${r.added}件・変更 ${r.changed}件を反映しました`);
      state.meta = await api("GET", "/api/meta"); adminItems();
    } catch (e) { toast(e.message, true); $("#i_apply").disabled = false; }
  });
}

function itemForm(it, cats) {
  const v = it || { code: "", name: "", category_id: Number(adminView.cat) || cats[0]?.id, storage: "常温", case_weight: null, pallet_cases: null, expiry_mode: "date", mfg_months: null, note: "", sort: 0, active: 1 };
  openSheet(`<h3>${it ? "品目を変更" : "品目を追加"}</h3>
    <div class="form-grid" style="margin-top:12px">
      <label>コード<input class="text" id="f_code" value="${esc(v.code)}"></label>
      <label>品名<input class="text" id="f_name" value="${esc(v.name)}"></label>
      <label>分類<select id="f_cat">${cats.map((c) => `<option value="${c.id}" ${c.id === v.category_id ? "selected" : ""}>${esc(c.grp)} / ${esc(c.name)}</option>`).join("")}</select></label>
      <label>保管（未開封時）<select id="f_storage">${state.meta.storages.map((s) => `<option ${s === v.storage ? "selected" : ""}>${s}</option>`).join("")}</select></label>
      <label>ケース重量(kg)<input class="text" id="f_cw" inputmode="decimal" value="${v.case_weight ?? ""}" placeholder="空欄=未設定"></label>
      <label>1パレットのケース数<input class="text" id="f_pc" inputmode="decimal" value="${v.pallet_cases ?? ""}" placeholder="空欄=未設定（パレットを数えない）"></label>
      <label>期限の種類<select id="f_mode">${Object.entries(EXPIRY_MODE).map(([k, l]) => `<option value="${k}" ${k === v.expiry_mode ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label id="f_months_wrap">製造日からの月数<input class="text" id="f_months" inputmode="numeric" value="${v.mfg_months ?? ""}"></label>
      <label>メモ<input class="text" id="f_note" value="${esc(v.note)}"></label>
      <label>並び順（小さいほど上）<input class="text" id="f_sort" inputmode="numeric" value="${v.sort}"></label>
      <label>状態<select id="f_active"><option value="1" ${v.active ? "selected" : ""}>有効</option><option value="0" ${v.active ? "" : "selected"}>終売（棚卸に出さない）</option></select></label>
    </div>
    <div class="actions-bar"><button class="btn" id="close">閉じる</button><button class="btn primary" id="save">保存</button></div>`);
  const syncMode = () => ($("#f_months_wrap").hidden = $("#f_mode").value !== "mfg");
  $("#f_mode").addEventListener("change", syncMode);
  syncMode();
  $("#close").addEventListener("click", closeSheet);
  $("#save").addEventListener("click", async () => {
    const cw = $("#f_cw").value.trim();
    const pcv = $("#f_pc").value.trim();
    const body = {
      code: $("#f_code").value, name: $("#f_name").value, category_id: Number($("#f_cat").value), storage: $("#f_storage").value,
      case_weight: cw === "" ? null : Number(cw), pallet_cases: pcv === "" ? null : Number(pcv), expiry_mode: $("#f_mode").value,
      mfg_months: $("#f_months").value ? Number($("#f_months").value) : null, note: $("#f_note").value,
      sort: Number($("#f_sort").value) || 0, active: $("#f_active").value === "1",
    };
    if (cw !== "" && !Number.isFinite(body.case_weight)) return toast("ケース重量は数字で入れてください", true);
    if (pcv !== "" && !(body.pallet_cases > 0)) return toast("1パレットのケース数は0より大きい数字で入れてください", true);
    try {
      await api(it ? "PUT" : "POST", it ? `/api/admin/items/${it.id}` : "/api/admin/items", body);
      closeSheet(); toast("保存しました"); state.meta = await api("GET", "/api/meta"); adminItems();
    } catch (e) { toast(e.message, true); }
  });
}

async function adminCategories() {
  const cats = await api("GET", "/api/admin/categories");
  const el = $("#admin");
  el.innerHTML = `<div class="row"><span class="spacer"></span><button class="btn primary" id="add">＋ 分類を追加</button></div>
    <div class="table-wrap" style="margin-top:10px"><table><thead><tr><th>並び順</th><th>グループ</th><th>分類名</th><th>置き場所</th><th>状態</th></tr></thead>
    <tbody>${cats.map((c) => `<tr class="click" data-id="${c.id}"><td>${c.sort}</td><td>${esc(c.grp)}</td><td>${esc(c.name)}</td>
      <td>${c.locations === "split" ? "資材室＋倉庫" : "1か所"}</td><td>${c.active ? "有効" : "使わない"}</td></tr>`).join("")}</tbody></table></div>`;
  const form = (c) => {
    const v = c || { grp: "", name: "", locations: "split", sort: (cats.at(-1)?.sort || 0) + 10, active: 1 };
    openSheet(`<h3>${c ? "分類を変更" : "分類を追加"}</h3><div class="form-grid" style="margin-top:12px">
      <label>グループ（トップ画面の見出し）<input class="text" id="c_grp" value="${esc(v.grp)}"></label>
      <label>分類名<input class="text" id="c_name" value="${esc(v.name)}"></label>
      <label>置き場所<select id="c_loc"><option value="split" ${v.locations === "split" ? "selected" : ""}>資材室と倉庫/パレットに分ける</option><option value="single" ${v.locations === "single" ? "selected" : ""}>1か所</option></select></label>
      <label>並び順<input class="text" id="c_sort" inputmode="numeric" value="${v.sort}"></label>
      <label>状態<select id="c_active"><option value="1" ${v.active ? "selected" : ""}>有効</option><option value="0" ${v.active ? "" : "selected"}>使わない</option></select></label>
      </div><div class="actions-bar"><button class="btn" id="close">閉じる</button><button class="btn primary" id="save">保存</button></div>`);
    $("#close").addEventListener("click", closeSheet);
    $("#save").addEventListener("click", async () => {
      const body = { grp: $("#c_grp").value.trim(), name: $("#c_name").value.trim(), locations: $("#c_loc").value, sort: Number($("#c_sort").value) || 0, active: $("#c_active").value === "1" };
      if (!body.grp || !body.name) return toast("グループと分類名を入れてください", true);
      try {
        await api(c ? "PUT" : "POST", c ? `/api/admin/categories/${c.id}` : "/api/admin/categories", body);
        closeSheet(); toast("保存しました"); state.meta = await api("GET", "/api/meta"); adminCategories();
      } catch (e) { toast(e.message, true); }
    });
  };
  $("#add").addEventListener("click", () => form(null));
  el.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", () => form(cats.find((x) => x.id === Number(tr.dataset.id)))));
}

async function adminStaff() {
  const staff = await api("GET", "/api/admin/staff");
  const el = $("#admin");
  el.innerHTML = `<div class="row"><input class="text" id="s_new" placeholder="名前" style="flex:1"><button class="btn primary" id="add">追加</button></div>
    <div class="table-wrap" style="margin-top:10px"><table><thead><tr><th>名前</th><th>並び順</th><th>状態</th><th></th></tr></thead>
    <tbody>${staff.map((s) => `<tr><td>${esc(s.name)}</td><td>${s.sort}</td><td>${s.active ? "有効" : "無効"}</td>
      <td><button class="btn small" data-edit="${s.id}">変更</button> <button class="btn small" data-toggle="${s.id}">${s.active ? "無効にする" : "有効にする"}</button></td></tr>`).join("")}</tbody></table></div>`;
  const reload = async () => { state.meta = await api("GET", "/api/meta"); adminStaff(); };
  $("#add").addEventListener("click", async () => {
    const name = $("#s_new").value.trim();
    if (!name) return;
    try { await api("POST", "/api/admin/staff", { name, sort: (staff.at(-1)?.sort || 0) + 10 }); toast("追加しました"); reload(); }
    catch (e) { toast(e.message, true); }
  });
  el.querySelectorAll("[data-toggle]").forEach((b) => b.addEventListener("click", async () => {
    const s = staff.find((x) => x.id === Number(b.dataset.toggle));
    try { await api("PUT", `/api/admin/staff/${s.id}`, { name: s.name, sort: s.sort, active: !s.active }); reload(); }
    catch (e) { toast(e.message, true); }
  }));
  el.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", async () => {
    const s = staff.find((x) => x.id === Number(b.dataset.edit));
    const name = prompt("名前", s.name);
    if (name == null) return;
    const sort = prompt("並び順（小さいほど前）", s.sort);
    if (sort == null) return;
    try { await api("PUT", `/api/admin/staff/${s.id}`, { name, sort: Number(sort) || 0, active: !!s.active }); reload(); }
    catch (e) { toast(e.message, true); }
  }));
}

async function adminExpected() {
  const [y, m] = state.ym.split("-").map(Number);
  const el = $("#admin");
  el.innerHTML = `
    <p>${y}年${m}月の予想在庫をExcelでまとめて入れます（月は画面上の年月で切り替え）。</p>
    <ol class="steps">
      <li>ひな形をダウンロードする（その月の品目と、いま入っている予想在庫が並んでいます）
        <div><a class="btn" href="/api/months/${state.ym}/expected.xlsx" style="text-decoration:none;display:inline-flex;align-items:center;margin-top:6px">ひな形をダウンロード</a></div></li>
      <li>「予想在庫(kg)」の列に数字を入れて保存する。空欄の行は変更しません</li>
      <li>ファイルを選んで取り込む
        <div class="row" style="margin-top:6px"><input type="file" id="x_file" accept=".xlsx"><button class="btn primary" id="x_go">取り込む</button></div></li>
    </ol>
    <p class="muted">「コード」と「予想在庫(kg)」の列があれば、ひな形以外の表でも取り込めます。エラーが1件でもあると何も取り込みません。</p>
    <div id="x_result"></div>`;
  $("#x_go").addEventListener("click", async () => {
    const f = $("#x_file").files[0];
    if (!f) return toast("ファイルを選んでください", true);
    const out = $("#x_result");
    $("#x_go").disabled = true;
    try {
      const r = await api("POST", `/api/admin/months/${state.ym}/expected/import`, await f.arrayBuffer(), true);
      out.innerHTML = r.ok
        ? `<div class="banner ok">${esc(f.name)}：${r.rows}件を読み込み、${r.changed}件を更新しました（${r.rows - r.changed}件は同じ値）</div>`
        : `<div class="notice"><b>取り込めませんでした（${r.errors.length}件のエラー）。直してからもう一度取り込んでください。</b>
            <ul>${r.errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>`;
      if (r.ok) toast("取り込みました");
    } catch (e) { toast(e.message, true); }
    finally { $("#x_go").disabled = false; }
  });
}

async function adminHistory() {
  const el = $("#admin");
  el.innerHTML = `<div class="row"><select id="h_entity"><option value="">すべて</option>${Object.entries(ENTITY).map(([k, v]) => `<option value="${k}" ${k === adminView.hentity ? "selected" : ""}>${v}</option>`).join("")}</select>
    <input class="search" id="h_q" type="search" placeholder="内容・名前で探す" value="${esc(adminView.hq)}" style="flex:1;min-width:180px"></div>
    <div class="table-wrap" style="margin-top:10px"><table><thead><tr><th>日時</th><th>名前</th><th>種類</th><th>操作</th><th>内容</th></tr></thead><tbody id="h_rows"></tbody></table></div>`;
  const load = async () => {
    const rows = await api("GET", `/api/admin/history?entity=${encodeURIComponent(adminView.hentity)}&q=${encodeURIComponent(adminView.hq)}&limit=300`);
    $("#h_rows").innerHTML = rows.map((r) => `<tr><td style="white-space:nowrap">${esc(r.at.replace("T", " "))}</td><td>${esc(r.actor)}</td>
      <td>${esc(ENTITY[r.entity] || r.entity)}</td><td>${esc(r.action)}</td><td>${esc(r.summary)}</td></tr>`).join("") || `<tr><td colspan="5">履歴はありません</td></tr>`;
  };
  $("#h_entity").addEventListener("change", (e) => { adminView.hentity = e.target.value; load(); });
  let t;
  $("#h_q").addEventListener("input", (e) => { adminView.hq = e.target.value; clearTimeout(t); t = setTimeout(load, 300); });
  await load();
}

init().catch((e) => { view.innerHTML = `<p class="notice">サーバーにつながりません：${esc(e.message)}</p>`; });
