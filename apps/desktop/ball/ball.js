/* 悬浮球页面逻辑（spec §10 P0）。
 *
 * - 球态：60px 头像 + 未读红点；拖 vs 点：按下位移 > 阈值 = 拖动（窗口
 *   移动由 pywebview 的 DRAG_REGION_SELECTOR 机制承担），松开未位移 = 展开。
 * - 展开态：消息流 + 输入框 + 顶部「助理 / 项目 CEO」切换标签。
 * - 数据源：/api/ball/state、/api/chat/history/{agentId}、/api/ball/chat、
 *   /api/ball/unread/clear —— 与网页端同一条管道（三端互通）。
 * - pywebview 桥（window.pywebview.api）缺失时（直接用浏览器打开调试）
 *   自动降级：不缩放窗口、不存位置，其余功能照常。
 */

(function () {
  "use strict";

  // ── 常量 ────────────────────────────────────────────────
  var DRAG_THRESHOLD_PX = 6;      // §10 拖 vs 点阈值
  var POLL_COLLAPSED_MS = 2500;   // 球态：红点轮询
  var POLL_EXPANDED_MS = 1800;    // 展开态：消息流轮询

  // ── 状态 ────────────────────────────────────────────────
  var state = {
    expanded: false,
    targets: [],            // [{agentId, name, unread}]
    activeAgentId: null,
    lastMsgKey: null,
    pollTimer: null,
    sending: false,
  };

  // ── 工具 ────────────────────────────────────────────────
  function $(id) { return document.getElementById(id); }

  function bridge() {
    // pywebview 桥；浏览器直开时返回 null（降级运行）
    var w = window.pywebview;
    return w && w.api ? w.api : null;
  }

  var _apiKey = null;
  function apiKeyHeaders() {
    // 审计 M3：HIVEWEAVE_API_KEY 部署下经 pywebview 桥取 key（js_api）；
    // 浏览器直开（无桥）或无 key 部署返回空对象。
    if (_apiKey === null) {
      _apiKey = "";
      try {
        var b = bridge();
        if (b && typeof b.get_api_key === "function") {
          _apiKey = String(b.get_api_key() || "");
        }
      } catch (e) { _apiKey = ""; }
    }
    return _apiKey ? { "x-api-key": _apiKey } : {};
  }

  function api(path, opts) {
    var headers = Object.assign({ "Content-Type": "application/json" }, apiKeyHeaders());
    return fetch(path, Object.assign({ headers: headers }, opts || {}))
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status + " " + path);
        return r.json();
      });
  }

  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  // ── 球态红点 ────────────────────────────────────────────
  function renderBadge() {
    var total = 0;
    state.targets.forEach(function (t) { total += t.unread || 0; });
    var badge = $("ball-badge");
    if (total > 0) {
      badge.textContent = total > 99 ? "99+" : String(total);
      badge.classList.remove("hidden");
    } else {
      badge.classList.add("hidden");
    }
  }

  function pollState() {
    return api("/api/ball/state")
      .then(function (data) {
        var targets = [];
        if (data && data.assistant) {
          targets.push({
            agentId: data.assistant.agentId,
            name: data.assistant.name || "助理",
            unread: data.assistant.unread || 0,
          });
        }
        (data && data.projects ? data.projects : []).forEach(function (p) {
          if (p.ceo) {
            targets.push({
              agentId: p.ceo.agentId,
              name: p.name + " · " + (p.ceo.name || "CEO"),
              unread: p.ceo.unread || 0,
            });
          }
        });
        state.targets = targets;
        if (!state.activeAgentId && targets.length) {
          state.activeAgentId = targets[0].agentId;
        }
        // 活动标签可能已消失（项目删除）→ 回落助理
        var still = targets.some(function (t) { return t.agentId === state.activeAgentId; });
        if (!still && targets.length) state.activeAgentId = targets[0].agentId;
        renderBadge();
        renderTabs();
        if (state.expanded) return loadMessages();
      })
      .catch(function () { /* 后端未就绪时静默，下轮重试 */ });
  }

  // ── 标签 ────────────────────────────────────────────────
  function renderTabs() {
    var tabs = $("tabs");
    tabs.innerHTML = "";
    state.targets.forEach(function (t) {
      var b = document.createElement("button");
      b.className = "tab" + (t.agentId === state.activeAgentId ? " active" : "");
      b.innerHTML = esc(t.name) + ((t.unread || 0) > 0 ? ' <span class="dot"></span>' : "");
      b.addEventListener("click", function () {
        state.activeAgentId = t.agentId;
        state.lastMsgKey = null;
        $("messages").innerHTML = "";
        renderTabs();
        clearUnread(t.agentId);
        loadMessages();
      });
      tabs.appendChild(b);
    });
  }

  // ── 消息流 ──────────────────────────────────────────────
  function renderMessages(rows) {
    var box = $("messages");
    var visible = rows.filter(function (m) {
      return (m.role === "user" || m.role === "assistant") &&
        !(m.isBackground || m.is_background) &&
        !(m.isContext || m.is_context);
    });
    var key = visible.map(function (m) { return m.id; }).join(",") +
      "|" + visible.map(function (m) { return (m.content || "").length; }).join(",");
    if (key === state.lastMsgKey) return;
    var nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
    state.lastMsgKey = key;
    box.innerHTML = "";
    if (!visible.length) {
      var tip = document.createElement("div");
      tip.className = "empty-tip";
      tip.textContent = "暂无消息，说点什么吧";
      box.appendChild(tip);
      return;
    }
    visible.forEach(function (m) {
      var d = document.createElement("div");
      d.className = "msg " + (m.role === "user" ? "user" : "assistant");
      d.innerHTML = esc(m.content) +
        '<span class="meta">' + (m.role === "user" ? "我" : "助理") + "</span>";
      box.appendChild(d);
    });
    if (nearBottom) box.scrollTop = box.scrollHeight;
  }

  function loadMessages() {
    if (!state.activeAgentId) return Promise.resolve();
    return api("/api/chat/history/" + encodeURIComponent(state.activeAgentId) + "?limit=60")
      .then(function (data) { renderMessages(data.messages || []); })
      .catch(function () { });
  }

  function clearUnread(agentId) {
    return api("/api/ball/unread/clear", {
      method: "POST",
      body: JSON.stringify({ agentId: agentId }),
    }).catch(function () { });
  }

  // ── 发送 ────────────────────────────────────────────────
  function send() {
    var input = $("input");
    var content = (input.value || "").trim();
    if (!content || state.sending || !state.activeAgentId) return;
    state.sending = true;
    $("btn-send").disabled = true;
    api("/api/ball/chat", {
      method: "POST",
      body: JSON.stringify({
        agentId: state.activeAgentId,
        content: content,
        source: "ball",
      }),
    })
      .then(function () {
        input.value = "";
        return loadMessages();
      })
      .catch(function (e) {
        var box = $("messages");
        var d = document.createElement("div");
        d.className = "msg assistant";
        d.textContent = "发送失败：" + (e && e.message ? e.message : "未知错误");
        box.appendChild(d);
        box.scrollTop = box.scrollHeight;
      })
      .then(function () {
        state.sending = false;
        $("btn-send").disabled = false;
        input.focus();
      });
  }

  // ── 展开 / 收起（球态 ↔ 面板）──────────────────────────
  function expand() {
    state.expanded = true;
    document.body.className = "mode-panel";
    $("ball").classList.add("hidden");
    $("panel").classList.remove("hidden");
    var b = bridge();
    if (b && b.expand) { try { b.expand(); } catch (e) { } }
    pollState().then(function () {
      if (state.activeAgentId) clearUnread(state.activeAgentId);
      $("input").focus();
    });
    restartPoll(POLL_EXPANDED_MS);
  }

  function collapse() {
    state.expanded = false;
    document.body.className = "mode-ball";
    $("panel").classList.add("hidden");
    $("ball").classList.remove("hidden");
    var b = bridge();
    if (b && b.collapse) { try { b.collapse(); } catch (e) { } }
    restartPoll(POLL_COLLAPSED_MS);
  }

  function restartPoll(ms) {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(pollState, ms);
  }

  // ── 拖 vs 点（§10：位移 > 阈值 = 拖动，松开未位移 = 展开/收起）──
  function bindDragTap(el, onTap) {
    var startX = 0, startY = 0, moved = false, down = false;
    el.addEventListener("pointerdown", function (e) {
      if (e.button !== 0) return;
      down = true; moved = false;
      startX = e.clientX; startY = e.clientY;
    });
    el.addEventListener("pointermove", function (e) {
      if (!down) return;
      var dx = e.clientX - startX, dy = e.clientY - startY;
      if (dx * dx + dy * dy > DRAG_THRESHOLD_PX * DRAG_THRESHOLD_PX) moved = true;
    });
    el.addEventListener("pointerup", function () {
      if (down && !moved) onTap();
      down = false;
      if (moved) {
        // 拖动结束 → 让启动器落盘新位置（位置存数据根）
        var b = bridge();
        if (b && b.save_position) { try { b.save_position(); } catch (e) { } }
      }
      moved = false;
    });
    el.addEventListener("pointercancel", function () { down = false; moved = false; });
    // 右键菜单（§10：隐藏/退出；托盘不可用 → P0 以右键菜单代替）
    el.addEventListener("contextmenu", function (e) {
      e.preventDefault();
      var b = bridge();
      if (!b) return;
      var hide = window.confirm("隐藏悬浮球（进程保留，可从主界面/任务管理器退出）？");
      if (hide && b.hide_ball) { try { b.hide_ball(); } catch (err) { } }
    });
  }

  // ── 启动 ────────────────────────────────────────────────
  function init() {
    $("btn-send").addEventListener("click", send);
    $("input").addEventListener("keydown", function (e) {
      if (e.key === "Enter") send();
    });
    $("btn-collapse").addEventListener("click", collapse);
    $("btn-main").addEventListener("click", function () {
      var b = bridge();
      if (b && b.open_main) { try { b.open_main(); } catch (e) { } }
    });
    bindDragTap($("ball"), expand);
    bindDragTap(document.querySelector(".panel-header"), collapse);
    restartPoll(POLL_COLLAPSED_MS);
    pollState();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
