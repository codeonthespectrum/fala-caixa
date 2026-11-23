"""Mini-Painel Web Admin local para gestão Multi-Loja do Fala, Caixa!.

Executa em `http://127.0.0.1:8080` usando apenas biblioteca padrão do Python.
Permite cadastrar lojas (Trial 14 dias), gerenciar sócios autorizados no WhatsApp,
sincronizar automaticamente a allowlist do perfil `falacaixa`, testar conexão Read-Only,
importar planilhas CSV/Excel, ativar/pausar assinaturas e disparar resumos de teste.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any
from urllib.parse import urlparse

from nex_alerts.config import AppConfig, _load_env_file
from nex_alerts.db.store_repository import (
    DEFAULT_HERMES_FALACAIXA_ENV,
    StoreRepository,
)
from nex_alerts.main import build_collector, run_pipeline

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLAN_PRICE_BRL = Decimal("67.00")


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return f"{value[:2]}***{value[-2:]}"


def _serialize_store(store: Any) -> dict[str, Any]:
    eff = store.effective_status()
    return {
        "id": store.id,
        "slug": store.slug,
        "store_name": store.store_name,
        "data_source_mode": store.data_source_mode,
        "nex_web_base_url": store.nex_web_base_url,
        "nex_web_email": store.nex_web_email,
        "has_password": bool(store.nex_web_password),
        "has_token": bool(store.nex_web_token),
        "export_folder_path": store.export_folder_path,
        "pix_key": store.pix_key,
        "margin_threshold": str(store.margin_threshold),
        "whatsapp_notify_phone": store.whatsapp_notify_phone,
        "schedule_time": store.schedule_time,
        "status": store.status,
        "effective_status": eff,
        "trial_ends_at": store.trial_ends_at,
        "days_left": store.days_left_in_trial(),
        "users": [
            {
                "id": u.id,
                "name": u.name,
                "phone": u.phone,
                "role": u.role,
            }
            for u in store.users
        ],
    }


def _dashboard_html() -> str:
    return """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Fala, Caixa! • Painel Admin Multi-Loja</title>
  <style>
    :root {
      --navy-950: #071126;
      --navy-900: #0B1938;
      --green-500: #10B981;
      --green-600: #059669;
      --bg-ice: #F4F7FB;
      --white: #FFFFFF;
      --text-dark: #0F172A;
      --text-muted: #64748B;
      --border: #E2E8F0;
      --amber-bg: #FEF3C7;
      --amber-text: #B45309;
      --red-bg: #FEE2E2;
      --red-text: #B91C1C;
      --green-bg: #D1FAE5;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg-ice);
      color: var(--text-dark);
    }
    header {
      background: var(--navy-950);
      color: #fff;
      padding: 16px 28px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    header h1 { margin: 0; font-size: 20px; }
    header h1 span { color: #34D399; }
    header small { color: #94A3B8; }
    main { max-width: 1180px; margin: 24px auto; padding: 0 20px 60px; }
    .kpis {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .kpi {
      background: var(--white);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px 18px;
    }
    .kpi .label { font-size: 12px; color: var(--text-muted); text-transform: uppercase; font-weight: 700; }
    .kpi .val { font-size: 26px; font-weight: 800; margin-top: 6px; }
    .grid-2 { display: grid; grid-template-columns: 1.4fr 1fr; gap: 20px; align-items: start; }
    @media (max-width: 960px) { .grid-2 { grid-template-columns: 1fr; } }
    .card {
      background: var(--white);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 20px;
      margin-bottom: 20px;
    }
    .card h2 { margin: 0 0 14px; font-size: 17px; }
    .store-card {
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 14px;
      background: #FAFCFF;
    }
    .store-top { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
    .badge {
      display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 700;
    }
    .badge-active { background: var(--green-bg); color: var(--green-600); }
    .badge-trial { background: var(--amber-bg); color: var(--amber-text); }
    .badge-paused, .badge-expired { background: var(--red-bg); color: var(--red-text); }
    .meta { font-size: 13px; color: var(--text-muted); margin: 8px 0 12px; line-height: 1.5; }
    .users-list { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
    .user-pill {
      background: #EEF2FF; color: #1E3A8A; padding: 4px 10px; border-radius: 8px; font-size: 12px;
      display: inline-flex; align-items: center; gap: 6px;
    }
    .user-pill button {
      border: none; background: transparent; color: #B91C1C; cursor: pointer; font-weight: 700; padding: 0;
    }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
    button, .btn {
      border: 1px solid var(--border); background: #fff; color: var(--text-dark);
      padding: 7px 12px; border-radius: 8px; font-size: 12px; font-weight: 600; cursor: pointer;
    }
    button.primary { background: var(--green-500); border-color: var(--green-500); color: #fff; }
    button.primary:hover { background: var(--green-600); }
    button.danger { color: var(--red-text); border-color: #FECACA; background: #FEF2F2; }
    label { display: block; font-size: 12px; font-weight: 700; margin: 10px 0 4px; color: var(--text-dark); }
    input, select {
      width: 100%; padding: 9px 11px; border: 1px solid var(--border); border-radius: 8px;
      font-size: 13px; background: var(--bg-ice);
    }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    #toast {
      position: fixed; bottom: 20px; right: 20px; background: var(--navy-900); color: #fff;
      padding: 12px 16px; border-radius: 10px; font-size: 13px; display: none; max-width: 380px; z-index: 99;
    }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid var(--border); }
    th { color: var(--text-muted); font-size: 11px; text-transform: uppercase; }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Fala, <span>Caixa!</span> • Admin SaaS</h1>
      <small>Gestão Multi-Loja • 1 único WhatsApp (84 92001-2483)</small>
    </div>
    <button onclick="syncAllowlist()">Sincronizar Allowlist Hermes</button>
  </header>

  <main>
    <div class="kpis">
      <div class="kpi"><div class="label">Lojas Ativas (Pagantes)</div><div class="val" id="kpi-active">0</div></div>
      <div class="kpi"><div class="label">Em Trial (14 dias)</div><div class="val" id="kpi-trial">0</div></div>
      <div class="kpi"><div class="label">Pausadas / Vencidas</div><div class="val" id="kpi-inactive">0</div></div>
      <div class="kpi"><div class="label">MRR Estimado (R$ 67)</div><div class="val" id="kpi-mrr">R$ 0,00</div></div>
    </div>

    <div class="grid-2">
      <div>
        <div class="card">
          <h2>Lojas Cadastradas</h2>
          <div id="stores-container">Carregando...</div>
        </div>

        <div class="card">
          <h2>Últimos Disparos Diários</h2>
          <table>
            <thead><tr><th>Data/Hora</th><th>Loja</th><th>Status</th><th>Resumo</th></tr></thead>
            <tbody id="logs-body"></tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <h2>+ Nova Loja (Trial 14 dias)</h2>
        <form id="new-store-form" onsubmit="createStore(event)">
          <label>Nome da Loja</label>
          <input name="store_name" required placeholder="Ex: Mercadinho São José" />

          <div class="row">
            <div>
              <label>Sistema do Caixa</label>
              <select name="data_source_mode" onchange="toggleErpFields(this.value)">
                <option value="nex_web">Nextar (Web Read-Only)</option>
                <option value="folder">Excel / CSV (Pasta local)</option>
              </select>
            </div>
            <div>
              <label>Margem Alvo (%)</label>
              <input name="margin_pct" type="number" value="30" min="5" max="90" />
            </div>
          </div>

          <div id="nex-fields">
            <label>E-mail do Nextar</label>
            <input name="nex_web_email" placeholder="dono@loja.com" />
            <div class="row">
              <div>
                <label>Senha do Nextar</label>
                <input name="nex_web_password" type="password" placeholder="••••••" />
              </div>
              <div>
                <label>Token Nextar (opcional)</label>
                <input name="nex_web_token" placeholder="Opcional" />
              </div>
            </div>
          </div>

          <div class="row">
            <div>
              <label>Chave PIX da Loja</label>
              <input name="pix_key" required placeholder="CPF, CNPJ ou Telefone" />
            </div>
            <div>
              <label>WhatsApp do Dono</label>
              <input name="owner_phone" required placeholder="5584999998888" />
            </div>
          </div>

          <div class="row">
            <div>
              <label>Nome do Dono</label>
              <input name="owner_name" required placeholder="Ex: João Silva" />
            </div>
            <div>
              <label>Status Inicial</label>
              <select name="status">
                <option value="trial">Trial (14 dias grátis)</option>
                <option value="active">Ativo (R$ 67/mês)</option>
              </select>
            </div>
          </div>

          <div style="margin-top:16px">
            <button type="submit" class="primary" style="width:100%;padding:11px">Cadastrar Loja e Sincronizar WhatsApp</button>
          </div>
        </form>
      </div>
    </div>
  </main>

  <div id="toast"></div>

  <div id="login-overlay" style="display:none;position:fixed;inset:0;background:rgba(7,17,38,.85);z-index:100;align-items:center;justify-content:center;">
    <form onsubmit="submitLogin(event)" style="background:#fff;padding:28px;border-radius:14px;max-width:360px;width:90%;">
      <h2 style="margin:0 0 8px;font-size:18px;">🔒 Painel restrito</h2>
      <p style="font-size:13px;color:#64748B;margin:0 0 12px;">Informe o token de acesso do painel (definido em FALACAIXA_ADMIN_TOKEN no .env ou impresso no terminal ao subir o painel).</p>
      <input id="token-input" type="password" placeholder="Token de acesso" style="margin-bottom:12px" />
      <button type="submit" class="primary" style="width:100%;padding:11px">Entrar</button>
    </form>
  </div>

  <script>
    const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

    let adminToken = localStorage.getItem("falacaixa_admin_token") || "";
    const urlToken = new URLSearchParams(location.search).get("token");
    if (urlToken) {
      adminToken = urlToken;
      localStorage.setItem("falacaixa_admin_token", urlToken);
      history.replaceState(null, "", location.pathname);
    }

    function showLogin() {
      document.getElementById("login-overlay").style.display = "flex";
    }

    function submitLogin(ev) {
      ev.preventDefault();
      const v = document.getElementById("token-input").value.trim();
      if (!v) return;
      adminToken = v;
      localStorage.setItem("falacaixa_admin_token", v);
      document.getElementById("login-overlay").style.display = "none";
      loadDashboard();
    }

    function toast(msg) {
      const el = document.getElementById("toast");
      el.textContent = msg;
      el.style.display = "block";
      setTimeout(() => { el.style.display = "none"; }, 4200);
    }

    function toggleErpFields(mode) {
      document.getElementById("nex-fields").style.display = mode === "nex_web" ? "block" : "none";
    }

    async function api(url, opts = {}) {
      opts.headers = {"X-Admin-Token": adminToken, ...(opts.headers || {})};
      const res = await fetch(url, opts);
      const data = await res.json();
      if (res.status === 401) {
        showLogin();
        throw new Error(data.error || "Não autorizado");
      }
      if (!res.ok) throw new Error(data.error || "Erro na requisição");
      return data;
    }

    async function loadDashboard() {
      const data = await api("/api/dashboard");
      const stores = data.stores || [];
      let active = 0, trial = 0, inactive = 0;
      stores.forEach(s => {
        if (s.effective_status === "active") active++;
        else if (s.effective_status === "trial") trial++;
        else inactive++;
      });
      document.getElementById("kpi-active").textContent = active;
      document.getElementById("kpi-trial").textContent = trial;
      document.getElementById("kpi-inactive").textContent = inactive;
      document.getElementById("kpi-mrr").textContent = "R$ " + (active * 67).toFixed(2).replace(".", ",");

      const cont = document.getElementById("stores-container");
      if (!stores.length) {
        cont.innerHTML = "<p>Nenhuma loja cadastrada.</p>";
      } else {
        cont.innerHTML = stores.map(s => {
          const badgeClass = "badge-" + esc(s.effective_status);
          const trialTxt = s.effective_status === "trial" && s.days_left !== null
            ? ` (${s.days_left}d restantes)` : "";
          const usersHtml = (s.users || []).map(u => `
            <span class="user-pill">${esc(u.name || "Sócio")} • ${esc(u.phone)}
              <button title="Remover número" onclick="removeUser(${u.id})">×</button>
            </span>
          `).join("");

          return `
            <div class="store-card">
              <div class="store-top">
                <strong>${esc(s.store_name)}</strong>
                <span class="badge ${badgeClass}">${esc(s.effective_status.toUpperCase())}${trialTxt}</span>
              </div>
              <div class="meta">
                Sistema: <b>${esc(s.data_source_mode)}</b> • PIX: <b>${esc(s.pix_key || "-")}</b> • Notificação: <b>${esc(s.whatsapp_notify_phone || "-")}</b>
              </div>
              <div class="users-list">${usersHtml}</div>
              <div class="actions">
                <button onclick="addUser(${s.id})">+ Sócio/WhatsApp</button>
                <button onclick="testStore(${s.id})">Testar Conexão</button>
                <button onclick="dispatchStore(${s.id})">Enviar Resumo Agora</button>
                ${s.data_source_mode === "folder" ? `<button onclick="uploadCsv(${s.id})">Subir CSV/XLSX</button>` : ""}
                ${s.effective_status !== "active" ? `<button class="primary" onclick="setStatus(${s.id}, 'active')">Ativar (R$ 67)</button>` : `<button onclick="setStatus(${s.id}, 'paused')">Pausar</button>`}
                <button class="danger" onclick="deleteStore(${s.id})">Excluir</button>
              </div>
            </div>
          `;
        }).join("");
      }

      const logsBody = document.getElementById("logs-body");
      logsBody.innerHTML = (data.logs || []).map(l => `
        <tr>
          <td>${esc(l.dispatched_at)}</td>
          <td>${esc(l.store_name)}</td>
          <td><b>${esc(l.status)}</b></td>
          <td>${esc(l.summary)}</td>
        </tr>
      `).join("") || "<tr><td colspan='4'>Nenhum disparo registrado ainda.</td></tr>";
    }

    async function createStore(ev) {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      const payload = Object.fromEntries(fd.entries());
      try {
        await api("/api/stores", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload),
        });
        ev.target.reset();
        toast("Loja cadastrada e allowlist sincronizada!");
        loadDashboard();
      } catch (e) { toast(e.message); }
    }

    async function addUser(storeId) {
      const name = prompt("Nome do dono/sócio:");
      if (!name) return;
      const phone = prompt("WhatsApp com DDD (ex: 5584999998888):");
      if (!phone) return;
      try {
        await api(`/api/stores/${storeId}/users`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({name, phone}),
        });
        toast("Número autorizado adicionado e sincronizado!");
        loadDashboard();
      } catch (e) { toast(e.message); }
    }

    async function removeUser(userId) {
      if (!confirm("Remover este número autorizado?")) return;
      try {
        await api(`/api/users/${userId}`, {method: "DELETE"});
        toast("Número removido e allowlist atualizada.");
        loadDashboard();
      } catch (e) { toast(e.message); }
    }

    async function setStatus(storeId, status) {
      try {
        await api(`/api/stores/${storeId}/status`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({status}),
        });
        toast(`Status alterado para ${status}.`);
        loadDashboard();
      } catch (e) { toast(e.message); }
    }

    async function testStore(storeId) {
      toast("Testando conexão...");
      try {
        const res = await api(`/api/stores/${storeId}/test`, {method: "POST"});
        toast(`OK! ${res.products} produtos e ${res.debts} fiados lidos.`);
      } catch (e) { toast("Falha: " + e.message); }
    }

    async function dispatchStore(storeId) {
      toast("Gerando e enviando resumo...");
      try {
        const res = await api(`/api/stores/${storeId}/dispatch`, {method: "POST"});
        toast(res.sent ? "Resumo enviado no WhatsApp!" : ("Salvo localmente: " + (res.error || "sem envio")));
        loadDashboard();
      } catch (e) { toast("Erro: " + e.message); }
    }

    async function uploadCsv(storeId) {
      const input = document.createElement("input");
      input.type = "file";
      input.accept = ".csv,.xlsx,.xls";
      input.onchange = async () => {
        const file = input.files[0];
        if (!file) return;
        const buf = await file.arrayBuffer();
        try {
          const res = await api(`/api/stores/${storeId}/upload`, {
            method: "POST",
            headers: {"X-Filename": file.name},
            body: buf,
          });
          toast(`Arquivo ${res.saved} enviado com sucesso!`);
        } catch (e) { toast("Erro upload: " + e.message); }
      };
      input.click();
    }

    async function deleteStore(storeId) {
      if (!confirm("Excluir esta loja? Esta ação não pode ser desfeita.")) return;
      try {
        await api(`/api/stores/${storeId}`, {method: "DELETE"});
        toast("Loja removida.");
        loadDashboard();
      } catch (e) { toast(e.message); }
    }

    async function syncAllowlist() {
      try {
        const res = await api("/api/sync-allowlist", {method: "POST"});
        toast(`Allowlist sincronizada (${res.count} variantes no .env).`);
      } catch (e) { toast(e.message); }
    }

    loadDashboard();
  </script>
</body>
</html>"""


MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB — planilhas de estoque não passam disso


def create_handler(
    repo: StoreRepository,
    repo_root: Path = REPO_ROOT,
    hermes_env_path: Path = DEFAULT_HERMES_FALACAIXA_ENV,
    admin_token: str = "",
) -> type[BaseHTTPRequestHandler]:
    """Cria a classe HTTP handler ligada ao repositório informado (facilita testes).

    Toda rota /api/* exige o header ``X-Admin-Token`` conferindo com ``admin_token``
    (comparação em tempo constante). O header customizado também bloqueia CSRF:
    browsers exigem preflight CORS para enviá-lo a partir de outras origens.
    """

    class AdminHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def _check_auth(self) -> bool:
            provided = self.headers.get("X-Admin-Token", "")
            if admin_token and hmac.compare_digest(provided, admin_token):
                return True
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "Não autorizado. Informe o token de acesso do painel."},
            )
            return False

        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            return json.loads(raw.decode("utf-8"))

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                html = _dashboard_html().encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                return

            if path == "/api/dashboard":
                if not self._check_auth():
                    return
                stores = [_serialize_store(s) for s in repo.list_stores()]
                logs = repo.list_dispatch_logs(limit=25)
                self._send_json(HTTPStatus.OK, {"stores": stores, "logs": logs})
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Rota não encontrada"})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if not self._check_auth():
                return
            try:
                if path == "/api/stores":
                    data = self._read_json()
                    margin_pct = Decimal(str(data.get("margin_pct") or "30"))
                    margin_ratio = (margin_pct / Decimal("100")).quantize(Decimal("0.01"))
                    owner_phone = str(data.get("owner_phone") or "").strip()
                    owner_name = str(data.get("owner_name") or "Dono").strip()
                    store = repo.create_store(
                        store_name=str(data["store_name"]),
                        data_source_mode=str(data.get("data_source_mode") or "nex_web"),
                        nex_web_email=str(data.get("nex_web_email") or ""),
                        nex_web_password=str(data.get("nex_web_password") or ""),
                        nex_web_token=str(data.get("nex_web_token") or ""),
                        pix_key=str(data.get("pix_key") or ""),
                        margin_threshold=margin_ratio,
                        whatsapp_notify_phone=owner_phone,
                        status=str(data.get("status") or "trial"),
                        trial_days=14,
                        initial_users=[(owner_name, owner_phone, "owner")] if owner_phone else (),
                    )
                    repo.sync_hermes_allowlist(hermes_env_path)
                    self._send_json(HTTPStatus.CREATED, _serialize_store(store))
                    return

                if path == "/api/sync-allowlist":
                    repo.sync_hermes_allowlist(hermes_env_path)
                    phones = repo.collect_allowed_phones()
                    self._send_json(HTTPStatus.OK, {"ok": True, "count": len(phones)})
                    return

                m_user = re.fullmatch(r"/api/stores/(\d+)/users", path)
                if m_user:
                    store_id = int(m_user.group(1))
                    data = self._read_json()
                    ok = repo.add_store_user(
                        store_id=store_id,
                        name=str(data.get("name") or "Sócio"),
                        phone=str(data.get("phone") or ""),
                    )
                    if not ok:
                        self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Telefone inválido"})
                        return
                    repo.sync_hermes_allowlist(hermes_env_path)
                    self._send_json(HTTPStatus.CREATED, {"ok": True})
                    return

                m_status = re.fullmatch(r"/api/stores/(\d+)/status", path)
                if m_status:
                    store_id = int(m_status.group(1))
                    data = self._read_json()
                    status = str(data.get("status") or "").strip().lower()
                    if status not in {"trial", "active", "paused", "expired"}:
                        self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Status inválido"})
                        return
                    repo.update_store(store_id, status=status)
                    self._send_json(HTTPStatus.OK, {"ok": True})
                    return

                m_test = re.fullmatch(r"/api/stores/(\d+)/test", path)
                if m_test:
                    store = repo.get_store(int(m_test.group(1)))
                    if not store:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Loja não encontrada"})
                        return
                    cfg = repo.to_app_config(store)
                    collector = build_collector(cfg)
                    now = datetime.now()
                    products = collector.fetch_recent_stock_entries(reference_now=now)
                    debts = collector.fetch_customer_debts(reference_today=now.date())
                    self._send_json(
                        HTTPStatus.OK,
                        {"ok": True, "products": len(products), "debts": len(debts)},
                    )
                    return

                m_dispatch = re.fullmatch(r"/api/stores/(\d+)/dispatch", path)
                if m_dispatch:
                    store = repo.get_store(int(m_dispatch.group(1)))
                    if not store:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Loja não encontrada"})
                        return
                    env_path = repo_root / ".env"
                    if env_path.exists():
                        _load_env_file(env_path, override=False)
                    base_cfg = AppConfig.from_env(env_file=env_path if env_path.exists() else None)
                    cfg = replace(
                        repo.to_app_config(store, base_config=base_cfg),
                        whatsapp_provider="hermes",
                        hermes_bridge_url="http://127.0.0.1:3001",
                        output_dir=repo_root / "output" / "alertas_enviados",
                    )
                    res = run_pipeline(config=cfg, print_to_stdout=False)
                    repo.add_dispatch_log(
                        store.id,
                        "ok" if res.webhook_sent else "error",
                        "Disparo manual pelo painel" if res.webhook_sent else (res.webhook_error or "Erro"),
                    )
                    self._send_json(
                        HTTPStatus.OK,
                        {"sent": res.webhook_sent, "error": res.webhook_error},
                    )
                    return

                m_upload = re.fullmatch(r"/api/stores/(\d+)/upload", path)
                if m_upload:
                    store = repo.get_store(int(m_upload.group(1)))
                    if not store:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Loja não encontrada"})
                        return
                    filename = Path(self.headers.get("X-Filename", "dados.csv")).name
                    length = int(self.headers.get("Content-Length", "0"))
                    if length > MAX_UPLOAD_BYTES:
                        self._send_json(
                            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                            {"error": "Arquivo excede o limite de 20 MB."},
                        )
                        return
                    content = self.rfile.read(length)
                    dest_dir = Path(store.export_folder_path)
                    if not dest_dir.is_absolute():
                        dest_dir = repo_root / dest_dir
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest_file = dest_dir / filename
                    dest_file.write_bytes(content)
                    self._send_json(HTTPStatus.OK, {"saved": str(dest_file.name)})
                    return

                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Rota não encontrada"})
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def do_DELETE(self) -> None:
            path = urlparse(self.path).path
            if not self._check_auth():
                return
            m_user = re.fullmatch(r"/api/users/(\d+)", path)
            if m_user:
                repo.remove_store_user(int(m_user.group(1)))
                repo.sync_hermes_allowlist(hermes_env_path)
                self._send_json(HTTPStatus.OK, {"ok": True})
                return

            m_store = re.fullmatch(r"/api/stores/(\d+)", path)
            if m_store:
                repo.delete_store(int(m_store.group(1)))
                repo.sync_hermes_allowlist(hermes_env_path)
                self._send_json(HTTPStatus.OK, {"ok": True})
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Rota não encontrada"})

    return AdminHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Painel Admin Multi-Loja Fala, Caixa!")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    env_path = REPO_ROOT / ".env"
    _load_env_file(env_path, override=False)

    repo = StoreRepository(REPO_ROOT / "data" / "falacaixa.db")
    repo.bootstrap_from_env_if_empty(env_path)
    repo.sync_hermes_allowlist(DEFAULT_HERMES_FALACAIXA_ENV)

    admin_token = os.getenv("FALACAIXA_ADMIN_TOKEN", "").strip()
    ephemeral = False
    if not admin_token:
        # Sem token configurado, gera um temporário para esta sessão — nunca sobe aberto
        admin_token = secrets.token_urlsafe(24)
        ephemeral = True

    handler = create_handler(repo, repo_root=REPO_ROOT, admin_token=admin_token)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    if ephemeral:
        print(
            "ATENÇÃO: FALACAIXA_ADMIN_TOKEN não definido no .env — token temporário gerado.\n"
            f"Acesse: http://{args.host}:{args.port}/?token={admin_token}\n"
            "(Para fixar, adicione FALACAIXA_ADMIN_TOKEN=<seu-token> no .env)"
        )
    else:
        print(f"Painel Admin Fala, Caixa! rodando em http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando painel admin...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
