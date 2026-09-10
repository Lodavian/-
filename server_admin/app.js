(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const tokenKey = "fastorder.admin.access-token";
  const verifierKey = "fastorder.admin.pkce-verifier";
  const stateKey = "fastorder.admin.oauth-state";
  let config;

  const b64 = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes))).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
  const random = () => b64(crypto.getRandomValues(new Uint8Array(32)));
  const message = (text) => { $("message").textContent = text; };

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { "Authorization": `Bearer ${sessionStorage.getItem(tokenKey) || ""}`, "Content-Type": "application/json", ...(options.headers || {}) },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error?.message || `HTTP ${response.status}`);
    return body;
  }

  async function login() {
    const verifier = random();
    const challenge = b64(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
    const state = random();
    sessionStorage.setItem(verifierKey, verifier);
    sessionStorage.setItem(stateKey, state);
    const url = new URL(config.authorizationEndpoint);
    Object.entries({client_id:config.clientId,response_type:"code",redirect_uri:config.redirectUri,scope:config.scope,state,code_challenge:challenge,code_challenge_method:"S256"}).forEach(([key,value]) => url.searchParams.set(key,value));
    location.assign(url);
  }

  async function finishLogin(code, state) {
    if (!code || state !== sessionStorage.getItem(stateKey)) throw new Error("登录回调校验失败");
    const body = new URLSearchParams({grant_type:"authorization_code",client_id:config.clientId,code,redirect_uri:config.redirectUri,code_verifier:sessionStorage.getItem(verifierKey) || ""});
    const response = await fetch(config.tokenEndpoint, {method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body});
    const tokens = await response.json();
    if (!response.ok || !tokens.access_token) throw new Error("无法取得管理员令牌");
    sessionStorage.setItem(tokenKey, tokens.access_token);
    history.replaceState({}, "", "/admin/");
  }

  function table(rows) {
    if (!rows?.length) return "<p>暂无记录</p>";
    const keys = Object.keys(rows[0]);
    return `<table><thead><tr>${keys.map(k=>`<th>${escapeHtml(k)}</th>`).join("")}</tr></thead><tbody>${rows.map(row=>`<tr>${keys.map(k=>`<td>${escapeHtml(String(row[k] ?? ""))}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  }
  const escapeHtml = (value) => value.replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);

  async function refresh() {
    const state = await api("/admin/v1/state");
    $("globalState").textContent = state.control.global_trade_enabled ? "已开启" : "已中断";
    $("state").innerHTML = `<h3>用户</h3>${table(state.users)}<h3>账户</h3>${table(state.accounts)}<h3>设备</h3>${table(state.devices)}`;
    $("app").hidden = false;
  }

  async function submitForm(form, path) {
    const data = Object.fromEntries(new FormData(form));
    const result = await api(path, {method:"POST",body:JSON.stringify(data)});
    form.reset(); message(`操作成功：${result.userId || result.accountRef || "OK"}`); await refresh();
  }

  async function start() {
    config = await fetch("/admin/config").then(r=>r.json());
    $("login").addEventListener("click", login);
    const params = new URLSearchParams(location.search);
    if (params.has("code")) await finishLogin(params.get("code"), params.get("state"));
    $("userForm").addEventListener("submit", e=>{e.preventDefault();submitForm(e.currentTarget,"/admin/v1/users").catch(err=>message(err.message));});
    $("accountForm").addEventListener("submit", e=>{e.preventDefault();submitForm(e.currentTarget,"/admin/v1/accounts").catch(err=>message(err.message));});
    $("bindingForm").addEventListener("submit", e=>{e.preventDefault();submitForm(e.currentTarget,"/admin/v1/bindings").catch(err=>message(err.message));});
    $("unbindForm").addEventListener("submit", async e=>{
      e.preventDefault(); const data=Object.fromEntries(new FormData(e.currentTarget));
      try { await api(`/admin/v1/bindings/${encodeURIComponent(data.accountRef)}/remove`,{method:"POST",body:"{}"}); e.currentTarget.reset(); await refresh(); }
      catch(err){message(err.message);}
    });
    $("userStatusForm").addEventListener("submit", async e=>{
      e.preventDefault(); const data=Object.fromEntries(new FormData(e.currentTarget));
      try { await api(`/admin/v1/users/${encodeURIComponent(data.userId)}/status`,{method:"POST",body:JSON.stringify({enabled:data.enabled==="true"})}); e.currentTarget.reset(); await refresh(); }
      catch(err){message(err.message);}
    });
    $("passwordForm").addEventListener("submit", async e=>{
      e.preventDefault(); const data=Object.fromEntries(new FormData(e.currentTarget));
      try { await api(`/admin/v1/users/${encodeURIComponent(data.userId)}/password`,{method:"POST",body:JSON.stringify({password:data.password})}); e.currentTarget.reset(); message("密码已重置"); }
      catch(err){message(err.message);}
    });
    $("deviceForm").addEventListener("submit", async e=>{
      e.preventDefault(); const data=Object.fromEntries(new FormData(e.currentTarget));
      try { await api(`/admin/v1/devices/${encodeURIComponent(data.deviceId)}`,{method:"POST",body:JSON.stringify({enabled:data.enabled==="true"})}); e.currentTarget.reset(); await refresh(); }
      catch(err){message(err.message);}
    });
    $("controlForm").addEventListener("submit", async e=>{
      e.preventDefault(); const data=Object.fromEntries(new FormData(e.currentTarget));
      try { await api(`/admin/v1/controls/${data.scope}`,{method:"POST",body:JSON.stringify({key:data.key,enabled:data.enabled==="true",reason:data.reason})}); await refresh(); }
      catch(err){message(err.message);}
    });
    document.querySelectorAll("[data-global]").forEach(button=>button.addEventListener("click", async()=>{
      try { await api("/admin/v1/controls/global",{method:"POST",body:JSON.stringify({enabled:button.dataset.global==="true",reason:"管理员管理台操作"})}); await refresh(); }
      catch(err){message(err.message);}
    }));
    if (sessionStorage.getItem(tokenKey)) await refresh();
  }
  start().catch(err=>message(err.message));
})();
