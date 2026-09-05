(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    socket: null,
    symbol: "AAPL",
    orderType: "MARKET",
    lastPrice: null,
    previousClose: null,
    lastMarketAt: 0,
    staleSeconds: 8,
    tradingEnabled: false,
    busy: false,
    orders: [],
    positions: { items: [], status: "offline", updatedAt: null, error: "", refreshing: false },
    limits: { maxQty: 0, maxValue: 0 },
    connectionStatus: "offline",
    controllerRole: "controller",
    readOnly: false,
    liveArmed: false,
    liveTogglePending: false,
    accountMasked: "",
    risk: {},
    usdBuyingPower: null,
    searchResults: [],
    searchQuery: "",
    selectedCandidate: null,
    searchIndex: -1,
    pendingClientOrderId: null,
    pendingCancelIds: new Set(),
    credentialState: "UNBOUND",
    connectionState: "DISCONNECTED",
    accountMask: "",
    canRemember: false,
    autoConnect: false,
    bindingSessionId: "",
    bindingAccounts: [],
    selectedAccountRef: "",
    latestConnectionErrorId: "",
    lastFocusedElement: null,
    sandbox: true,
  };

  const sessionToken = document.querySelector('meta[name="fast-order-session-token"]')?.content || "";
  const socketUrl = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;

  function connectSocket() {
    const socket = new WebSocket(socketUrl, [`fast-order-session.${sessionToken}`]);
    state.socket = socket;
    socket.addEventListener("open", () => {
      setLocalStatus("offline", "本地服务已连接", "点击连接账户");
      sendVisibility();
    });
    socket.addEventListener("message", (event) => {
      try {
        const message = JSON.parse(event.data);
        if (!message || typeof message !== "object" || Array.isArray(message)) throw new Error("invalid message");
        handleMessage(message);
      } catch (_error) {
        showToast("收到无法识别的服务消息，已忽略");
      }
    });
    socket.addEventListener("close", () => {
      state.tradingEnabled = false;
      state.liveArmed = false;
      clearPositions();
      updateLiveToggle();
      updateTradeControls("本地连接已断开");
      setLocalStatus("error", "本地连接已断开", "正在重新连接");
      setTimeout(connectSocket, 1500);
    });
    socket.addEventListener("error", () => showToast("无法连接本地交易服务"));
  }

  function send(type, payload = {}) {
    if (!state.socket || state.socket.readyState !== WebSocket.OPEN) throw new Error("本地连接不可用");
    state.socket.send(JSON.stringify({ type, payload }));
  }

  function sendVisibility() {
    if (state.socket?.readyState === WebSocket.OPEN) {
      send("page.visibility", { visible: document.visibilityState === "visible" });
    }
  }

  function handleMessage(message) {
    switch (message.type) {
      case "state":
        state.symbol = message.symbol;
        state.orders = message.orders || [];
        applyPositions(message.positions || {});
        state.limits = message.limits || state.limits;
        state.staleSeconds = state.limits.staleSeconds || 8;
        applyConnection(message.connection || {});
        applyAccount(message.account || {});
        applyRisk(message.risk || message.connection?.risk || {});
        applySymbol();
        if (message.quote && Object.keys(message.quote).length) applyMarket("quote", message.quote, Date.now() / 1000);
        if (message.snapshot && Object.keys(message.snapshot).length) applyMarket("snapshot", message.snapshot, Date.now() / 1000);
        (message.ticks || []).slice().reverse().forEach((tick) => applyTick(tick));
        renderOrders();
        break;
      case "connection": applyConnection(message); break;
      case "account.state": applyAccount(message); break;
      case "account.binding.accounts": showAccountCandidates(message); break;
      case "account.binding.complete":
        state.busy = false;
        closeDialog("accountBindingDialog");
        showToast(message.warning || "券商API账户已连接");
        break;
      case "connection.error.detail":
        $("errorDetailsMessage").textContent = message.detail || "没有可用的技术详情";
        openDialog("errorDetailsDialog");
        break;
      case "risk.state": applyRisk(message.risk || message); break;
      case "positions": applyPositions(message); break;
      case "symbol.changed":
        state.symbol = message.symbol;
        state.lastPrice = null;
        state.previousClose = null;
        state.lastMarketAt = 0;
        clearMarket();
        applySymbol();
        break;
      case "symbol.results": applySymbolResults(message); break;
      case "quote": applyMarket("quote", message.data || {}, message.receivedAt); break;
      case "snapshot": applyMarket("snapshot", message.data || {}, message.receivedAt); break;
      case "tick": applyTick(message.data || {}, message.receivedAt); break;
      case "order.update": applyOrder(message.order || {}); break;
      case "error":
        state.busy = false;
        state.liveTogglePending = false;
        state.pendingClientOrderId = null;
        state.pendingCancelIds.clear();
        showToast(message.message || "操作失败");
        if (message.source === "connection") {
          state.latestConnectionErrorId = message.errorId || "";
          const errorTarget = $("accountBindingStepAccounts").hidden ? $("bindingCredentialsError") : $("bindingAccountsError");
          errorTarget.textContent = message.message || "连接失败";
          errorTarget.hidden = false;
          $("bindingNextButton").disabled = false;
          $("bindingConfirmButton").disabled = !state.selectedAccountRef;
        }
        if (!$("symbolResults").hidden) {
          $("symbolResults").innerHTML = '<div class="symbol-results-state error">候选查询失败，请稍后重试</div>';
          $("symbolSearchHint").textContent = "未切换股票";
        }
        updateTradeControls();
        updateLiveToggle();
        renderAccountHeader();
        break;
    }
  }

  function applyConnection(data) {
    const status = data.status || "offline";
    state.connectionStatus = status;
    state.tradingEnabled = Boolean(data.tradingEnabled);
    state.controllerRole = data.controllerRole || data.role || state.controllerRole;
    state.readOnly = Boolean(data.readOnly) || state.controllerRole === "readonly";
    if (Object.prototype.hasOwnProperty.call(data, "liveArmed")) {
      state.liveArmed = Boolean(data.liveArmed);
      state.liveTogglePending = false;
    }
    state.lastMarketAt = data.lastMarketAt || state.lastMarketAt;
    state.accountMasked = data.accountMasked || data.account_id || data.accountId || state.accountMasked;
    state.connectionState = data.connectionState || state.connectionState;
    state.credentialState = data.credentialState || state.credentialState;
    if (typeof data.sandbox === "boolean") state.sandbox = data.sandbox;
    $("modeBadge").textContent = state.sandbox ? "SANDBOX" : "LIVE · 实盘";
    $("modeBadge").classList.toggle("live", !state.sandbox);
    setLocalStatus(status, statusLabel(status), data.message || data.blockReason || "");
    $("feedBadge").textContent = status === "connected" ? "STREAMING" : status.toUpperCase();
    $("connectButton").disabled = ["connecting", "subscribing"].includes(status);
    $("connectButton").textContent = status === "connected" ? "已连接" : status === "connecting" ? "连接中…" : state.credentialState === "UNBOUND" ? "绑定账户" : "连接账户";
    $("accountText").textContent = state.accountMasked ? `账户 ${maskAccount(state.accountMasked)}` : "账户未连接";
    $("liveGate").hidden = state.sandbox;
    $("controllerNotice").hidden = !state.readOnly;
    $("connectButton").disabled = $("connectButton").disabled || state.readOnly;
    ["symbolInput", "quantityInput", "limitInput"].forEach((id) => { $(id).disabled = state.readOnly; });
    document.querySelectorAll("[data-order-type], #quantityPresets button").forEach((button) => { button.disabled = state.readOnly; });
    updateLiveToggle();
    updateTradeControls(data.blockReason);
    renderAccountHeader();
  }

  function applyAccount(data) {
    if (!data || typeof data !== "object") return;
    state.credentialState = data.credentialState || state.credentialState;
    state.connectionState = data.connectionState || state.connectionState;
    state.accountMask = data.accountMask || state.accountMask;
    state.accountMasked = data.accountMask || state.accountMasked;
    state.canRemember = Boolean(data.canRemember);
    state.autoConnect = Boolean(data.autoConnect);
    $("bindingRemember").disabled = !state.canRemember;
    if (!state.canRemember) {
      $("bindingRemember").checked = false;
      $("bindingAutoConnect").checked = false;
      $("bindingAutoConnect").disabled = true;
    }
    renderAccountHeader();
  }

  function renderAccountHeader() {
    const connected = state.connectionStatus === "connected" || state.connectionState === "CONNECTED";
    const connecting = ["connecting", "subscribing"].includes(state.connectionStatus) || state.connectionState === "CONNECTING";
    const hasCredentials = !["UNBOUND", "ERROR"].includes(state.credentialState);
    const mask = state.accountMasked || state.accountMask;
    $("accountText").textContent = mask ? `账户 ${maskAccount(mask)}` : hasCredentials ? "API 凭据已保存" : "账户未绑定";
    $("popoverAccountName").textContent = mask ? `账户 ${maskAccount(mask)}` : hasCredentials ? "已保存 OpenAPI 凭据" : "账户未绑定";
    $("popoverConnectionState").textContent = connected ? "CONNECTED" : connecting ? "CONNECTING" : state.credentialState === "CREDENTIALS_SAVED" ? "SAVED" : "OFFLINE";
    $("connectButton").disabled = connecting || connected || state.readOnly;
    $("connectButton").textContent = connected ? "已连接" : connecting ? "连接中…" : hasCredentials ? "连接账户" : "绑定账户";
    $("popoverReconnectButton").disabled = connected || connecting || !hasCredentials || state.readOnly;
    $("popoverDisconnectButton").disabled = !connected || state.readOnly;
    $("popoverSwitchButton").disabled = connecting || state.readOnly;
    $("popoverForgetButton").disabled = (!hasCredentials && !mask) || state.readOnly;
  }

  function statusLabel(status) {
    return ({ connected: "券商API已连接", connecting: "正在连接", subscribing: "正在订阅", degraded: "行情链路异常", error: "连接失败", offline: "本地服务已就绪" })[status] || status;
  }

  function setLocalStatus(status, title, detail) {
    $("statusDot").className = `status-dot ${status}`;
    $("statusText").textContent = title;
    $("statusDetail").textContent = detail;
  }

  function applyRisk(data) {
    if (!data || typeof data !== "object") return;
    state.risk = { ...state.risk, ...(data.risk || data) };
    const risk = state.risk;
    if (Object.prototype.hasOwnProperty.call(risk, "liveArmed")) {
      state.liveArmed = Boolean(risk.liveArmed);
      state.liveTogglePending = false;
    }
    const account = risk.accountMasked || risk.accountId || risk.account_id || state.accountMasked;
    const buyingPower = risk.buyingPower ?? risk.availableCash ?? risk.cash;
    state.usdBuyingPower = risk.usdBuyingPower ?? null;
    $("headerUsdBuyingPower").textContent = state.usdBuyingPower == null ? "—" : money(state.usdBuyingPower);
    const available = risk.availablePosition ?? risk.sellableQuantity ?? risk.position;
    $("riskSummary").hidden = !account && buyingPower == null && available == null;
    $("riskAccount").textContent = account ? maskAccount(account) : "—";
    $("buyingPower").textContent = buyingPower == null ? "—" : money(buyingPower);
    $("availablePosition").textContent = available == null ? "—" : compact(available);
    if (account) {
      state.accountMasked = account;
      $("accountText").textContent = `账户 ${maskAccount(account)}`;
    }
    updateLiveToggle();
  }

  function clearPositions() {
    state.positions = { items: [], status: "offline", updatedAt: null, error: "", refreshing: false };
    renderPositions();
  }

  function normalisePosition(value) {
    if (!value || typeof value !== "object") return null;
    const currency = deepValue(value, ["currency", "currencycode", "assetcurrency", "settlementcurrency"]);
    if (currency && String(currency).toUpperCase() !== "USD") return null;
    const quantity = number(deepValue(value, ["quantity", "qty", "position", "holdingquantity", "holdings"]));
    if (quantity === null || quantity === 0) return null;
    const symbol = deepValue(value, ["symbol", "ticker", "stockcode", "instrumentcode"]);
    if (!symbol) return null;
    return {
      symbol: String(symbol),
      name: String(deepValue(value, ["name", "symbolname", "securityname", "instrumentname"]) || symbol),
      lastPrice: number(deepValue(value, ["lastprice", "last_price", "marketprice", "currentprice", "price"])),
      quantity,
      unitCost: number(deepValue(value, ["unitcost", "unit_cost", "costprice", "averagecost", "avgcost"])),
      unrealizedPnl: number(deepValue(value, ["unrealizedpnl", "unrealized_pnl", "unrealizedprofitloss", "unrealizedprofit", "pnl"])),
    };
  }

  function applyPositions(message) {
    if (Array.isArray(message)) message = { positions: message };
    if (!message || typeof message !== "object") return;
    if (message.data && typeof message.data === "object" && !Array.isArray(message.data)) message = { ...message.data, ...message };
    const source = Array.isArray(message.positions) ? message.positions : Array.isArray(message.items) ? message.items : [];
    const statusValue = String(message.status || message.refreshStatus || (message.error ? "error" : "success")).toLowerCase();
    const status = ["loading", "refreshing", "pending"].includes(statusValue) ? "loading" : ["error", "failed", "failure"].includes(statusValue) ? "error" : "success";
    const parsed = source.map(normalisePosition).filter(Boolean);
    if (status === "success") state.positions.items = parsed;
    state.positions.status = status;
    state.positions.updatedAt = message.updatedAt || message.dataAt || message.receivedAt || (status === "success" ? Date.now() / 1000 : state.positions.updatedAt);
    state.positions.error = message.error || message.message || "";
    state.positions.refreshing = Boolean(message.refreshing) || status === "loading";
    renderPositions();
  }

  function positionNumber(value) {
    const parsed = number(value);
    return parsed === null ? "—" : parsed.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }

  function positionMoney(value) {
    const parsed = number(value);
    return parsed === null ? "—" : `$${parsed.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
  }

  function positionPnl(value) {
    const parsed = number(value);
    if (parsed === null) return "—";
    const sign = parsed > 0 ? "+" : parsed < 0 ? "−" : "";
    return `${sign}$${Math.abs(parsed).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
  }

  function positionsDateText(value) {
    if (!value) return "—";
    const numeric = number(value);
    const date = numeric ? new Date(numeric > 1e12 ? numeric : numeric * 1000) : new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString([], { hour12: false });
  }

  function renderPositions() {
    const stateNode = $("positionsState");
    const target = $("positionsBody");
    const connected = ["connected", "degraded"].includes(state.connectionStatus) || state.connectionState === "CONNECTED";
    const positions = state.positions.items || [];
    const positionStatus = state.positions.status;
    const updated = state.positions.updatedAt ? ` · 数据时间 ${positionsDateText(state.positions.updatedAt)}` : "";
    const statusNode = $("positionsStatus");
    statusNode.className = `soft-badge positions-status ${positionStatus === "error" ? "positions-error" : positionStatus === "loading" ? "positions-loading" : ""}`;
    statusNode.textContent = !connected ? "未连接" : positionStatus === "loading" && !positions.length ? "加载中…" : positionStatus === "error" ? `更新失败${updated}` : state.positions.updatedAt ? `已更新 ${positionsDateText(state.positions.updatedAt)}` : "等待更新";
    target.querySelectorAll(".position-row, .positions-state").forEach((node) => node.remove());
    if (!connected) {
      stateNode.textContent = "账户未连接";
      target.appendChild(stateNode);
      return;
    }
    if (positionStatus === "loading" && !positions.length) {
      stateNode.textContent = "正在读取账户持仓…";
      target.appendChild(stateNode);
      return;
    }
    if (positionStatus === "error" && !positions.length) {
      stateNode.textContent = state.positions.error ? `持仓读取失败：${state.positions.error}` : "持仓读取失败，请稍后重试";
      target.appendChild(stateNode);
      return;
    }
    if (!positions.length) {
      stateNode.textContent = "暂无美元股票 / ETF 持仓";
      target.appendChild(stateNode);
      return;
    }
    positions.forEach((position) => {
      const row = document.createElement("div");
      row.className = "position-row positions-grid";
      row.setAttribute("role", "row");
      const pnl = number(position.unrealizedPnl);
      row.innerHTML = [
        ["股票代码", escapeHtml(position.symbol)],
        ["股票名称", escapeHtml(position.name)],
        ["当前单价", escapeHtml(positionMoney(position.lastPrice))],
        ["股数", escapeHtml(positionNumber(position.quantity))],
        ["每股成本价", escapeHtml(positionMoney(position.unitCost))],
        ["未实现盈亏", `<span class="${pnl === null || pnl === 0 ? "neutral" : pnl > 0 ? "positive" : "negative"}">${escapeHtml(positionPnl(pnl))}</span>`],
      ].map(([label, content]) => `<span role="cell" data-label="${label}">${content}</span>`).join("");
      target.appendChild(row);
    });
  }

  function applySymbol() {
    $("symbolInput").value = state.symbol;
    $("symbolName").textContent = state.symbol;
    $("previewSymbol").textContent = state.symbol;
    $("buyButton").querySelector("strong").textContent = `买入 ${state.symbol}`;
    $("sellButton").querySelector("strong").textContent = `卖出 ${state.symbol}`;
    updateEstimate();
    $("symbolSearchHint").textContent = state.selectedCandidate?.symbol === state.symbol
      ? `${state.selectedCandidate.name} · ${exchangeName(state.selectedCandidate.exchange)}`
      : "输入完整代码后从候选中选择";
  }

  function applySymbolResults(message) {
    const currentQuery = normalizeSearch($("symbolInput").value);
    if (message.query !== currentQuery) return;
    state.searchQuery = message.query;
    state.searchResults = Array.isArray(message.items) ? message.items : [];
    state.searchIndex = state.searchResults.findIndex((item) => item.status === "OC");
    $("symbolSearchHint").textContent = state.searchResults.length ? "请选择要订阅的股票" : "没有找到匹配候选";
    renderSymbolResults();
  }

  function renderSymbolResults(loading = false) {
    const target = $("symbolResults");
    target.hidden = false;
    $("symbolInput").setAttribute("aria-expanded", "true");
    if (loading) {
      target.innerHTML = '<div class="symbol-results-state">正在加载券商API股票候选…</div>';
      return;
    }
    if (!state.searchResults.length) {
      target.innerHTML = '<div class="symbol-results-state">没有找到匹配的美股</div>';
      return;
    }
    target.innerHTML = state.searchResults.map((item, index) => {
      const status = instrumentStatus(item.status);
      const disabled = item.status !== "OC";
      return `<button type="button" class="symbol-result ${index === state.searchIndex ? "active" : ""}" role="option" aria-selected="${index === state.searchIndex}" aria-disabled="${disabled}" data-symbol-index="${index}">
        <span class="symbol-result-main"><strong>${escapeHtml(item.name)}</strong><span><b>${escapeHtml(item.symbol)}</b> · ${escapeHtml(exchangeName(item.exchange))} · ${escapeHtml(categoryName(item.subCategory))}</span></span>
        <em class="instrument-status ${String(item.status).toLowerCase()}">${status}</em>
      </button>`;
    }).join("");
  }

  function closeSymbolResults() {
    $("symbolResults").hidden = true;
    $("symbolInput").setAttribute("aria-expanded", "false");
    state.searchIndex = -1;
  }

  function selectSymbolCandidate(index) {
    const item = state.searchResults[index];
    if (!item) return;
    if (item.status !== "OC") {
      showToast(`${item.symbol} 当前${instrumentStatus(item.status)}，不能作为新交易标的`);
      return;
    }
    state.selectedCandidate = item;
    $("symbolInput").value = item.symbol;
    $("symbolSearchHint").textContent = `${item.name} · ${exchangeName(item.exchange)}`;
    closeSymbolResults();
    send("symbol.change", { symbol: item.symbol });
  }

  const exchangeName = (value) => ({ NSQ: "NASDAQ", NAS: "NASDAQ", NYS: "NYSE", NYSE: "NYSE", ASE: "NYSE American", AMEX: "NYSE American", ARCA: "NYSE Arca", PINK: "OTC" })[String(value || "").toUpperCase()] || String(value || "US");
  const categoryName = (value) => ({ COMMON_STOCK: "股票", ETF: "ETF", PREFERRED_STOCK: "优先股", WARRANT: "权证", UNITS: "单位", RIGHT: "权利" })[String(value || "").toUpperCase()] || "股票";
  const instrumentStatus = (value) => ({ OC: "可交易", CO: "仅可平仓", NT: "不可交易" })[String(value || "").toUpperCase()] || "状态未知";
  const normalizeSearch = (value) => String(value || "").trim().toUpperCase().replace(/\s+/g, " ");

  function applyMarket(kind, data, receivedAt) {
    state.lastMarketAt = receivedAt || Date.now() / 1000;
    if (kind === "snapshot") {
      state.lastPrice = number(deepValue(data, ["price", "last_price", "last", "close"])) || state.lastPrice;
      state.previousClose = number(deepValue(data, ["pre_close", "previous_close", "prev_close"])) || state.previousClose;
      setText("openPrice", money(deepValue(data, ["open"])));
      setText("highPrice", money(deepValue(data, ["high"])));
      setText("lowPrice", money(deepValue(data, ["low"])));
      setText("volume", compact(deepValue(data, ["volume"])));
    } else {
      renderBook(data);
      const asks = arrayValue(data, "asks");
      const bids = arrayValue(data, "bids");
      const ask = number(deepValue(asks[0] || {}, ["price"]));
      const bid = number(deepValue(bids[0] || {}, ["price"]));
      if (!state.lastPrice && ask && bid) state.lastPrice = (ask + bid) / 2;
    }
    renderHeroPrice();
    updateEstimate();
  }

  function applyTick(data, receivedAt) {
    state.lastMarketAt = receivedAt || Date.now() / 1000;
    const price = number(deepValue(data, ["price"]));
    if (price) state.lastPrice = price;
    const row = document.createElement("div");
    const side = String(deepValue(data, ["side"]) || "—").toUpperCase();
    row.className = "tape-row";
    row.innerHTML = `<span>${escapeHtml(timeText(deepValue(data, ["time", "trade_time", "timestamp"])))}</span><span class="${side.includes("BUY") ? "positive" : side.includes("SELL") ? "negative" : ""}">${money(price)}</span><span>${compact(deepValue(data, ["volume", "size", "quantity"]))}</span><span>${escapeHtml(side)}</span>`;
    const tape = $("tape");
    if (tape.querySelector(".empty")) tape.innerHTML = "";
    tape.prepend(row);
    while (tape.children.length > 12) tape.lastElementChild.remove();
    renderHeroPrice();
    updateEstimate();
  }

  function renderBook(data) {
    const bids = arrayValue(data, "bids").slice(0, 5);
    const asks = arrayValue(data, "asks").slice(0, 5);
    renderLevels("bidBook", bids, "BID");
    renderLevels("askBook", asks, "ASK");
    const bid = number(deepValue(bids[0] || {}, ["price"]));
    const ask = number(deepValue(asks[0] || {}, ["price"]));
    $("spread").textContent = bid && ask ? (ask - bid).toFixed(4) : "—";
  }

  function renderLevels(id, levels, label) {
    const target = $(id);
    target.innerHTML = "";
    const sizes = levels.map((row) => number(deepValue(row, ["size", "volume", "quantity"])) || 0);
    const max = Math.max(...sizes, 1);
    for (let index = 0; index < 5; index += 1) {
      const level = levels[index] || {};
      const price = deepValue(level, ["price"]);
      const size = deepValue(level, ["size", "volume", "quantity"]);
      const row = document.createElement("div");
      row.className = "book-row";
      const depth = Math.round(((sizes[index] || 0) / max) * 20);
      row.innerHTML = `<i data-level="${depth}"></i><span>${label} ${index + 1}</span><span>${money(price)}</span><span>${compact(size)}</span>`;
      target.appendChild(row);
    }
  }

  function renderHeroPrice() {
    $("lastPrice").textContent = money(state.lastPrice);
    if (state.lastPrice && state.previousClose) {
      const change = state.lastPrice - state.previousClose;
      const pct = change / state.previousClose * 100;
      const node = $("priceChange");
      node.textContent = `${change >= 0 ? "+" : ""}${change.toFixed(2)}  ${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
      node.className = `price-change ${change > 0 ? "positive" : change < 0 ? "negative" : "neutral"}`;
    }
  }

  function clearMarket() {
    ["lastPrice", "openPrice", "highPrice", "lowPrice", "volume", "spread"].forEach((id) => setText(id, "—"));
    $("priceChange").textContent = "—";
    $("bidBook").innerHTML = "";
    $("askBook").innerHTML = "";
    $("tape").innerHTML = '<div class="empty">等待成交推送</div>';
  }

  function updateTradeControls(reason = "") {
    const enabled = state.tradingEnabled && !state.busy && !state.readOnly;
    $("buyButton").disabled = !enabled;
    $("sellButton").disabled = !enabled;
    $("tradeBlock").hidden = enabled;
    $("tradeBlock").querySelector("p").textContent = state.busy ? "订单正在提交，请勿重复点击" : (reason || "连接账户并等待实时行情后才能交易");
  }

  function updateEstimate() {
    const qty = number($("quantityInput").value);
    const reference = state.orderType === "LIMIT" ? number($("limitInput").value) : state.lastPrice;
    $("estimatedValue").textContent = qty && reference ? `$${(qty * reference).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—";
    $("riskLimits").textContent = state.limits.maxQty ? `${state.limits.maxQty} 股 / $${Number(state.limits.maxValue).toLocaleString()}` : "—";
  }

  function applyOrder(order) {
    const id = order.clientOrderId || order.client_order_id;
    const status = String(order.status || "UNKNOWN").toUpperCase();
    if (["PREPARED", "SENDING", "SUBMITTING"].includes(status)) {
      state.busy = true;
      state.pendingClientOrderId = id || state.pendingClientOrderId;
    } else if (!state.pendingClientOrderId || !id || id === state.pendingClientOrderId) {
      state.busy = false;
      if (id === state.pendingClientOrderId) state.pendingClientOrderId = null;
    }
    if (status !== "CANCEL_REQUESTED" && status !== "CANCEL_PENDING") state.pendingCancelIds.delete(id);
    const index = state.orders.findIndex((item) => (item.clientOrderId || item.client_order_id) === id);
    if (index >= 0) state.orders[index] = { ...state.orders[index], ...order };
    else state.orders.unshift(order);
    state.orders = state.orders.slice(0, 50);
    renderOrders();
    updateTradeControls();
    if (["FILLED", "PARTIAL", "PARTIAL_FILLED", "EXECUTED", "RECONCILED"].includes(status)) requestPositionsRefresh();
  }

  function renderOrders() {
    const target = $("orders");
    $("orderCount").textContent = `${state.orders.length} ORDERS`;
    if (!state.orders.length) {
      target.innerHTML = '<div class="empty">本次会话还没有订单</div>';
      return;
    }
    target.innerHTML = "";
    for (const order of state.orders) {
      const status = String(order.status || "UNKNOWN").toUpperCase();
      const cancellable = ["SUBMITTED", "ACKNOWLEDGED", "OPEN", "PARTIAL", "PARTIAL_FILLED", "RECONCILED"].includes(status);
      const cancelling = state.pendingCancelIds.has(order.clientOrderId) || ["CANCEL_REQUESTED", "CANCEL_PENDING"].includes(status);
      const row = document.createElement("div");
      row.className = "order-row";
       row.innerHTML = `<span>${timeText(order.createdAt)}</span><span class="order-main"><b class="side-pill ${String(order.side).toLowerCase()}">${escapeHtml(order.side || "—")}</b>${escapeHtml(order.symbol || "—")} · ${escapeHtml(order.orderType || "—")}</span><span>${escapeHtml(order.quantity)}</span><span>${order.limitPrice ? `$${escapeHtml(order.limitPrice)}` : "MARKET"}</span><span class="status-label">${escapeHtml(status)}</span><span>${cancellable || cancelling ? `<button class="cancel-button" data-cancel="${escapeHtml(order.clientOrderId)}" ${cancelling ? "disabled" : ""}>${cancelling ? "撤单中…" : "撤单"}</button>` : ""}</span>`;
      target.appendChild(row);
    }
  }

  function placeOrder(side) {
    if (state.busy || !state.tradingEnabled) return;
    state.busy = true;
    updateTradeControls();
    const payload = {
      requestId: crypto.randomUUID(),
      symbol: state.symbol,
      side,
      orderType: state.orderType,
      quantity: $("quantityInput").value,
    };
    if (state.orderType === "LIMIT") payload.limitPrice = $("limitInput").value;
    try { send("order.submit", payload); }
    catch (error) { state.busy = false; updateTradeControls(); showToast(error.message); }
  }

  function maskAccount(value) {
    const raw = String(value || "").trim();
    if (!raw) return "—";
    if (/[•*]/.test(raw)) return raw;
    return raw.length <= 4 ? `••••${raw}` : `••••${raw.slice(-4)}`;
  }

  function deepValue(value, keys) {
    if (!value || typeof value !== "object") return null;
    for (const [key, item] of Object.entries(value)) {
      if (keys.includes(key.toLowerCase()) && item !== null && item !== "") return item;
    }
    for (const item of Object.values(value)) {
      if (item && typeof item === "object") {
        const found = deepValue(item, keys);
        if (found !== null) return found;
      }
    }
    return null;
  }

  function arrayValue(value, key) {
    if (!value || typeof value !== "object") return [];
    if (Array.isArray(value[key])) return value[key];
    for (const item of Object.values(value)) {
      const found = arrayValue(item, key);
      if (found.length) return found;
    }
    return [];
  }

  const number = (value) => {
    if (value === null || value === undefined || String(value).trim() === "") return null;
    const parsed = Number(String(value).replaceAll(",", ""));
    return Number.isFinite(parsed) ? parsed : null;
  };
  const money = (value) => { const parsed = number(value); return parsed === null ? "—" : parsed.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 }); };
  const compact = (value) => { const parsed = number(value); return parsed === null ? "—" : Intl.NumberFormat(undefined, { notation: parsed >= 10000 ? "compact" : "standard", maximumFractionDigits: 2 }).format(parsed); };
  const setText = (id, value) => { $(id).textContent = value; };
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
  function timeText(value) {
    if (!value) return "—";
    const numeric = number(value);
    const date = numeric ? new Date(numeric > 1e12 ? numeric : numeric * 1000) : new Date(value);
    return Number.isNaN(date.getTime()) ? String(value).slice(-8) : date.toLocaleTimeString([], { hour12: false });
  }
  let toastTimer;
  function showToast(message) { $("toast").textContent = message; $("toast").classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(() => $("toast").classList.remove("show"), 4500); }

  function openDialog(id) {
    const dialog = $(id);
    state.lastFocusedElement = document.activeElement;
    dialog.hidden = false;
    document.body.classList.add("dialog-open");
    requestAnimationFrame(() => dialog.querySelector("input, button:not(:disabled)")?.focus());
  }

  function closeDialog(id) {
    const dialog = $(id);
    if (dialog.hidden) return;
    dialog.hidden = true;
    if (!document.querySelector(".dialog-backdrop:not([hidden])")) document.body.classList.remove("dialog-open");
    state.lastFocusedElement?.focus?.();
  }

  function openBindingDialog() {
    if (state.connectionStatus === "connected") {
      showToast("请先断开当前账户，再修改 API 凭据");
      return;
    }
    state.bindingSessionId = "";
    state.bindingAccounts = [];
    state.selectedAccountRef = "";
    $("bindingAppKey").value = "";
    $("bindingPasswordSecret").value = "";
    $("bindingCredentialsError").hidden = true;
    $("bindingAccountsError").hidden = true;
    showBindingStep("credentials");
    openDialog("accountBindingDialog");
  }

  function showBindingStep(step) {
    const accounts = step === "accounts";
    $("accountBindingStepCredentials").hidden = accounts;
    $("accountBindingStepAccounts").hidden = !accounts;
    document.querySelectorAll(".dialog-stepper span").forEach((item, index) => item.classList.toggle("active", index === (accounts ? 1 : 0)));
  }

  function showAccountCandidates(message) {
    state.busy = false;
    state.bindingSessionId = String(message.bindingSessionId || "");
    state.bindingAccounts = Array.isArray(message.accounts) ? message.accounts : [];
    state.selectedAccountRef = "";
    $("bindingAccountCandidates").innerHTML = state.bindingAccounts.map((account) => `
      <button type="button" class="account-candidate" role="option" aria-selected="false" data-account-ref="${escapeHtml(account.accountRef)}">
        <strong>${escapeHtml(account.accountMask || "账户")}</strong><small>${escapeHtml(account.accountType || "交易账户")}</small>
      </button>`).join("");
    $("bindingConfirmButton").disabled = true;
    showBindingStep("accounts");
    if ($("accountBindingDialog").hidden) openDialog("accountBindingDialog");
  }

  function closeBindingDialog() {
    if (state.bindingSessionId) {
      try { send("account.binding.cancel", { bindingSessionId: state.bindingSessionId }); } catch (_error) { /* TTL cleanup remains server-side. */ }
    }
    state.bindingSessionId = "";
    state.bindingAccounts = [];
    state.selectedAccountRef = "";
    $("bindingPasswordSecret").value = "";
    closeDialog("accountBindingDialog");
  }

  function toggleAccountPopover(force) {
    const next = typeof force === "boolean" ? force : $("accountPopover").hidden;
    $("accountPopover").hidden = !next;
    $("accountMenuButton").setAttribute("aria-expanded", String(next));
  }

  $("connectButton").addEventListener("click", () => {
    if (["UNBOUND", "ERROR"].includes(state.credentialState)) openBindingDialog();
    else { $("connectButton").disabled = true; send("account.connect"); }
  });
  $("accountMenuButton").addEventListener("click", () => {
    if (state.latestConnectionErrorId && state.connectionState === "CONNECTION_ERROR") {
      send("connection.error.details", { errorId: state.latestConnectionErrorId });
      return;
    }
    toggleAccountPopover();
  });
  $("popoverReconnectButton").addEventListener("click", () => { toggleAccountPopover(false); send("account.connect"); });
  $("popoverSwitchButton").addEventListener("click", () => { toggleAccountPopover(false); state.liveArmed = false; clearPositions(); updateLiveToggle(); send("account.switch"); setTimeout(openBindingDialog, 180); });
  $("popoverApiButton").addEventListener("click", () => { toggleAccountPopover(false); openBindingDialog(); });
  $("popoverDisconnectButton").addEventListener("click", () => { toggleAccountPopover(false); state.liveArmed = false; clearPositions(); updateLiveToggle(); send("account.disconnect"); });
  $("popoverForgetButton").addEventListener("click", () => { toggleAccountPopover(false); openDialog("forgetAccountDialog"); });

  $("bindingRemember").addEventListener("change", () => {
    $("bindingAutoConnect").disabled = !$("bindingRemember").checked;
    if (!$("bindingRemember").checked) $("bindingAutoConnect").checked = false;
  });
  $("bindingNextButton").addEventListener("click", () => {
    const appKey = $("bindingAppKey").value.trim();
    const appSecret = $("bindingPasswordSecret").value.trim();
    if (!appKey || !appSecret) {
      $("bindingCredentialsError").textContent = "请输入 App Key 和 App Secret";
      $("bindingCredentialsError").hidden = false;
      return;
    }
    $("bindingCredentialsError").hidden = true;
    $("bindingNextButton").disabled = true;
    state.busy = true;
    send("account.binding.start", {
      appKey,
      appSecret,
      rememberCredentials: $("bindingRemember").checked,
      autoConnect: $("bindingAutoConnect").checked,
    });
    // The secret must not remain in the DOM after it has crossed the local link.
    $("bindingPasswordSecret").value = "";
  });
  $("bindingAccountCandidates").addEventListener("click", (event) => {
    const button = event.target.closest("[data-account-ref]");
    if (!button) return;
    state.selectedAccountRef = button.dataset.accountRef;
    $("bindingAccountCandidates").querySelectorAll("[data-account-ref]").forEach((item) => {
      const selected = item === button;
      item.classList.toggle("selected", selected);
      item.setAttribute("aria-selected", String(selected));
    });
    $("bindingConfirmButton").disabled = false;
  });
  $("bindingConfirmButton").addEventListener("click", () => {
    if (!state.bindingSessionId || !state.selectedAccountRef) return;
    $("bindingConfirmButton").disabled = true;
    send("account.binding.complete", { bindingSessionId: state.bindingSessionId, accountRef: state.selectedAccountRef });
    state.bindingSessionId = "";
  });
  $("bindingBackButton").addEventListener("click", () => {
    if (state.bindingSessionId) send("account.binding.cancel", { bindingSessionId: state.bindingSessionId });
    state.bindingSessionId = "";
    showBindingStep("credentials");
    $("bindingPasswordSecret").focus();
  });
  ["accountBindingClose", "bindingCancelButton"].forEach((id) => $(id).addEventListener("click", closeBindingDialog));

  $("forgetAccountConfirm").addEventListener("click", () => {
    $("forgetAccountConfirm").disabled = true;
    send("account.forget", { confirm: true });
    closeDialog("forgetAccountDialog");
    setTimeout(() => { $("forgetAccountConfirm").disabled = false; }, 600);
  });
  ["forgetAccountClose", "forgetAccountCancel"].forEach((id) => $(id).addEventListener("click", () => closeDialog("forgetAccountDialog")));
  ["errorDetailsClose", "errorDetailsOkButton"].forEach((id) => $(id).addEventListener("click", () => closeDialog("errorDetailsDialog")));
  $("copyErrorButton").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("errorDetailsMessage").textContent); showToast("错误详情已复制"); }
    catch (_error) { showToast("无法复制，请手动选择文本"); }
  });

  document.addEventListener("keydown", (event) => {
    const dialog = document.querySelector(".dialog-backdrop:not([hidden])");
    if (!dialog) return;
    if (event.key === "Escape") {
      event.preventDefault();
      if (dialog.id === "accountBindingDialog") closeBindingDialog(); else closeDialog(dialog.id);
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...dialog.querySelectorAll("button:not(:disabled), input:not(:disabled), [tabindex]:not([tabindex='-1'])")];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  document.querySelectorAll(".dialog-backdrop").forEach((backdrop) => backdrop.addEventListener("mousedown", (event) => {
    if (event.target !== backdrop) return;
    if (backdrop.id === "accountBindingDialog") closeBindingDialog(); else closeDialog(backdrop.id);
  }));

  document.addEventListener("visibilitychange", sendVisibility);
  setInterval(() => {
    if (document.visibilityState === "visible") sendVisibility();
  }, 10000);
  let symbolSearchTimer;
  function requestSymbolSearch() {
    const query = normalizeSearch($("symbolInput").value);
    state.selectedCandidate = null;
    if (!query) { closeSymbolResults(); return; }
    if (state.connectionStatus !== "connected") {
      showToast("请先连接券商API后再搜索股票");
      return;
    }
    state.searchQuery = query;
    state.searchResults = [];
    renderSymbolResults(true);
    send("symbol.search", { query });
  }
  $("symbolInput").addEventListener("input", () => {
    state.selectedCandidate = null;
    $("symbolSearchHint").textContent = "正在查找候选…";
    clearTimeout(symbolSearchTimer);
    symbolSearchTimer = setTimeout(requestSymbolSearch, 280);
  });
  $("symbolInput").addEventListener("focus", () => {
    if (state.searchResults.length && state.searchQuery === normalizeSearch($("symbolInput").value)) renderSymbolResults();
  });
  $("symbolInput").addEventListener("keydown", (event) => {
    if ($("symbolResults").hidden) return;
    if (event.key === "Escape") { closeSymbolResults(); return; }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      let next = state.searchIndex;
      for (let count = 0; count < state.searchResults.length; count += 1) {
        next = (next + step + state.searchResults.length) % state.searchResults.length;
        if (state.searchResults[next]?.status === "OC") break;
      }
      state.searchIndex = next;
      renderSymbolResults();
      $("symbolResults").querySelector(".symbol-result.active")?.scrollIntoView({ block: "nearest" });
    }
  });
  $("symbolForm").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!$("symbolResults").hidden && state.searchResults[state.searchIndex]) {
      selectSymbolCandidate(state.searchIndex);
    } else {
      requestSymbolSearch();
      showToast("请从搜索候选中选择股票");
    }
  });
  $("symbolResults").addEventListener("click", (event) => {
    const button = event.target.closest("[data-symbol-index]");
    if (button) selectSymbolCandidate(Number(button.dataset.symbolIndex));
  });
  document.addEventListener("click", (event) => {
    if (!$("symbolForm").contains(event.target)) closeSymbolResults();
    if (!$("accountPopover").contains(event.target) && !$("accountMenuButton").contains(event.target)) toggleAccountPopover(false);
  });
  document.querySelectorAll("[data-order-type]").forEach((button) => button.addEventListener("click", () => {
    state.orderType = button.dataset.orderType;
    document.querySelectorAll("[data-order-type]").forEach((item) => item.classList.toggle("active", item === button));
    $("limitField").classList.toggle("hidden", state.orderType !== "LIMIT");
    updateEstimate();
  }));
  $("quantityPresets").addEventListener("click", (event) => { if (event.target.dataset.qty) { $("quantityInput").value = event.target.dataset.qty; updateEstimate(); } });
  $("quantityInput").addEventListener("input", updateEstimate);
  $("limitInput").addEventListener("input", updateEstimate);
  $("buyButton").addEventListener("click", () => placeOrder("BUY"));
  $("sellButton").addEventListener("click", () => placeOrder("SELL"));
  $("orders").addEventListener("click", (event) => {
    const button = event.target.closest("[data-cancel]");
    const id = button?.dataset.cancel;
    if (!id || state.pendingCancelIds.has(id) || button.disabled || state.readOnly) return;
    state.pendingCancelIds.add(id);
    button.disabled = true;
    button.textContent = "撤单中…";
    try { send("order.cancel", { clientOrderId: id }); }
    catch (error) { state.pendingCancelIds.delete(id); renderOrders(); showToast(error.message); }
  });

  function updateLiveToggle() {
    const connected = state.connectionStatus === "connected";
    const disabled = state.sandbox || state.readOnly || state.liveTogglePending || !connected;
    const toggle = $("liveToggle");
    if (!toggle) return;
    toggle.setAttribute("aria-checked", String(Boolean(state.liveArmed)));
    toggle.disabled = disabled;
    toggle.setAttribute("aria-label", state.liveArmed ? "关闭实盘交易" : "开启实盘交易");
    $("liveToggleHint").textContent = state.liveTogglePending ? "正在更新交易权限…" : state.liveArmed ? "已启用直发，再次点击关闭" : !connected ? "连接账户后可启用" : "点击开关启用直发";
  }

  $("liveToggle").addEventListener("click", () => {
    if (state.readOnly || state.sandbox || state.liveTogglePending || state.connectionStatus !== "connected") return;
    state.liveTogglePending = true;
    updateLiveToggle();
    try { send(state.liveArmed ? "live.disarm" : "live.arm", {}); }
    catch (error) {
      state.liveTogglePending = false;
      updateLiveToggle();
      showToast(error.message);
    }
  });

  clearMarket();
  renderPositions();
  updateLiveToggle();
  connectSocket();
  setInterval(() => {
    const age = state.lastMarketAt ? Date.now() / 1000 - state.lastMarketAt : Infinity;
    const ratio = Number.isFinite(age) ? Math.max(0, 1 - age / state.staleSeconds) : 0;
    $("freshnessBar").dataset.level = String(Math.round(ratio * 20));
    $("freshnessBar").classList.toggle("stale", ratio < .25);
    $("freshnessText").textContent = Number.isFinite(age) ? `${age.toFixed(1)} 秒前` : "未启动";
    $("marketState").textContent = state.tradingEnabled ? "实时 · 可交易" : "等待有效行情";
  }, 250);
})();
