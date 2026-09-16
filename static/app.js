/* 页面主逻辑：地图、回放、图表、异常列表。
 *
 * 数据全部来自后端 API，前端不做任何分析。这样「页面显示的」和
 * 「后端算出来的」永远是同一份，不会出现两边算法不一致的问题。
 *
 * 地图有两种：正常用高德，加载不出来就切简图模式（fallback_map.js）。
 * 切换只影响「怎么画」，不影响数据和交互。
 */

(function () {
  "use strict";

  // 每个轨迹点在回放时停多久（毫秒）。1× 走这个速度，
  // 整趟 167 分钟的行车大约 3 分钟放完；想细看就拖时间轴。
  var POINT_INTERVAL_MS = 200;

  var state = {
    tasks: [],
    taskId: null,
    route: [],        // 规划路线 [[lng, lat], ...]
    points: [],       // 轨迹点
    segments: [],
    events: [],
    report: null,
    summary: {},
    index: 0,
    playing: false,
    speed: 1,
    timer: null,
    map: null,        // 高德地图对象，简图模式下为 null
    overlays: [],     // 高德地图上自己加的图层，重画前要清掉
    marker: null,
    fallback: null,   // 简图模式对象
    mapError: ""
  };

  function $(id) { return document.getElementById(id); }

  function api(path, options) {
    return fetch(path, options).then(function (resp) {
      return resp.json().then(function (body) {
        if (!resp.ok) throw new Error(body.error || ("请求失败 " + resp.status));
        return body;
      });
    });
  }

  function pad(n) { return n < 10 ? "0" + n : String(n); }

  /* Unix 秒 -> 本地时间 08:23:10 */
  function clockText(ts) {
    var d = new Date(ts * 1000);
    return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }

  function durationText(seconds) {
    seconds = Math.round(seconds);
    var h = Math.floor(seconds / 3600);
    var m = Math.floor((seconds % 3600) / 60);
    if (h) return h + " 小时 " + m + " 分钟";
    if (m) return m + " 分钟";
    return seconds + " 秒";
  }

  var STOP_NAMES = {
    load: "装货", unload: "卸货", transfer: "中途装卸", refuel: "加油",
    rest: "休息", abnormal: "异常停留", unknown: "未知"
  };

  // ============================================================
  // 地图
  // ============================================================

  /* 动态加载高德 JS API 2.0。
   *
   * Key 必须由后端给（/api/config），不能写死在 js 里：
   * 写死了换 Key 要改代码，而且仓库里就多了一份配置。
   * 安全密钥要在脚本加载之前挂到 window 上，顺序反了会鉴权失败。
   */
  function loadAmap(cfg) {
    return new Promise(function (resolve, reject) {
      if (!cfg.amap_js_key) {
        reject(new Error("没有配置高德 JS Key，请检查 .env 里的 AMAP_JS_KEY"));
        return;
      }

      window._AMapSecurityConfig = { securityJsCode: cfg.amap_js_security_code || "" };

      var script = document.createElement("script");
      script.src = "https://webapi.amap.com/maps?v=2.0&key=" +
                   encodeURIComponent(cfg.amap_js_key);
      script.onload = function () {
        if (window.AMap) resolve(window.AMap);
        else reject(new Error("高德脚本加载了但没有挂上 AMap 对象"));
      };
      script.onerror = function () {
        reject(new Error("高德地图脚本加载失败（断网，或当前域名不在高德控制台的白名单里）"));
      };
      document.head.appendChild(script);

      // 断网时 onerror 不一定触发，加个超时兜底
      setTimeout(function () {
        if (!window.AMap) reject(new Error("高德地图脚本加载超时"));
      }, 8000);
    });
  }

  function useFallback(reason) {
    state.mapError = reason;
    state.fallback = state.fallback || new FallbackMap($("fallback-canvas"));
    $("map").hidden = true;
    $("fallback-canvas").hidden = false;

    var notice = $("map-notice");
    notice.hidden = false;
    notice.textContent = "已切换到简图模式：" + reason + "。地图之外的功能都能正常用。";

    // 地图可能在数据之后才准备好，所以要主动补画一次
    redrawCurrent();
  }

  /* 数据或地图任一边就绪后都可以调用，谁后到谁负责把画面补齐 */
  function redrawCurrent() {
    drawMap();
    if (state.points.length) moveTo(state.index);
  }

  function clearOverlays() {
    if (!state.map) return;
    state.overlays.forEach(function (o) { state.map.remove(o); });
    state.overlays = [];
    state.marker = null;
  }

  function addOverlay(obj) {
    state.map.add(obj);
    state.overlays.push(obj);
    return obj;
  }

  function drawMap() {
    if (state.map) drawAmap();
    else if (state.fallback) drawFallback();
    // 两个都还没准备好就先不画，谁先就绪谁调用 redrawCurrent()
  }

  function drawAmap() {
    clearOverlays();

    if (state.route.length > 1) {
      addOverlay(new AMap.Polyline({
        path: state.route,
        strokeColor: "#90a4ae", strokeWeight: 6, strokeOpacity: 0.9,
        lineJoin: "round", zIndex: 50
      }));
      addOverlay(new AMap.Marker({
        position: state.route[0],
        label: { content: "起", direction: "right" }, zIndex: 100
      }));
      addOverlay(new AMap.Marker({
        position: state.route[state.route.length - 1],
        label: { content: "终", direction: "left" }, zIndex: 100
      }));
    }

    var trackPath = state.points.map(function (p) { return [p.longitude, p.latitude]; });
    if (trackPath.length > 1) {
      addOverlay(new AMap.Polyline({
        path: trackPath,
        strokeColor: "#2e7d32", strokeWeight: 4, zIndex: 60
      }));
    }

    // 偏航段：把落在事件时间窗里的轨迹点单独连一条粗橙线盖上去
    state.events.forEach(function (e) {
      if (e.event_type !== "deviation") return;
      var seg = pointsBetween(e.start_ts, e.end_ts)
        .map(function (p) { return [p.longitude, p.latitude]; });
      if (seg.length > 1) {
        addOverlay(new AMap.Polyline({
          path: seg, strokeColor: "#fb8c00", strokeWeight: 9,
          strokeOpacity: 0.85, zIndex: 70
        }));
      }
    });

    // 停留点
    state.segments.forEach(function (s) {
      if (s.segment_type !== "stop") return;
      addOverlay(new AMap.CircleMarker({
        center: [s.start_lng, s.start_lat],
        radius: 7, strokeColor: "#fff", strokeWeight: 2,
        fillColor: "#1e88e5", fillOpacity: 0.95, zIndex: 90
      }));
    });

    // 温度异常点
    state.events.forEach(function (e) {
      if (e.event_type !== "temperature" || e.latitude === null) return;
      addOverlay(new AMap.CircleMarker({
        center: [e.longitude, e.latitude],
        radius: 8, strokeColor: "#fff", strokeWeight: 2,
        fillColor: "#e53935", fillOpacity: 0.95, zIndex: 95
      }));
    });

    // 车的位置。轨迹点为空时就放在起点，别让 marker 没地方站
    var first = state.points.length
      ? [state.points[0].longitude, state.points[0].latitude]
      : state.route[0];
    if (first) {
      state.marker = addOverlay(new AMap.Marker({ position: first, zIndex: 200 }));
    }

    if (state.overlays.length) state.map.setFitView();
  }

  function drawFallback() {
    var stops = state.segments
      .filter(function (s) { return s.segment_type === "stop"; })
      .map(function (s) { return { lat: s.start_lat, lng: s.start_lng }; });

    var temps = state.events
      .filter(function (e) { return e.event_type === "temperature" && e.latitude !== null; })
      .map(function (e) { return { lat: e.latitude, lng: e.longitude }; });

    var deviations = state.events
      .filter(function (e) { return e.event_type === "deviation"; })
      .map(function (e) {
        return pointsBetween(e.start_ts, e.end_ts)
          .map(function (p) { return [p.latitude, p.longitude]; });
      });

    state.fallback.draw({
      route: state.route.map(function (p) { return [p[1], p[0]]; }),  // 转成 [lat, lng]
      track: state.points.map(function (p) { return [p.latitude, p.longitude]; }),
      stops: stops,
      temperatures: temps,
      deviations: deviations
    });
  }

  function pointsBetween(startTs, endTs) {
    return state.points.filter(function (p) { return p.ts >= startTs && p.ts <= endTs; });
  }

  // ============================================================
  // 回放
  // ============================================================

  function moveTo(index) {
    if (!state.points.length) return;
    index = Math.max(0, Math.min(index, state.points.length - 1));
    state.index = index;

    var p = state.points[index];
    $("timeline").value = String(index);

    if (state.marker) {
      state.marker.setPosition([p.longitude, p.latitude]);
    } else if (state.fallback) {
      state.fallback.moveMarker(p.latitude, p.longitude);
    }

    updateNow(p);
    drawTempChart();
  }

  /* 当前时刻落在哪个分段里，用来显示「行驶中 / 加油」这类状态 */
  function segmentAt(ts) {
    for (var i = 0; i < state.segments.length; i++) {
      var s = state.segments[i];
      if (ts >= s.start_ts && ts <= s.end_ts) return s;
    }
    return null;
  }

  function updateNow(p) {
    var task = state.report && state.report.task;
    $("now-time").textContent = clockText(p.ts);
    $("now-speed").textContent = p.speed.toFixed(1) + " m/s";
    $("now-temp").textContent = p.temperature.toFixed(1) + " ℃";
    $("now-fridge").textContent = p.refrigerator_on ? "运行中" : "已停止";
    $("now-door").textContent = p.door_open ? "打开" : "关闭";
    $("now-pos").textContent = p.latitude.toFixed(5) + ", " + p.longitude.toFixed(5);
    $("clock").textContent = clockText(p.ts);

    var seg = segmentAt(p.ts);
    var label = "行驶中";
    if (seg && seg.segment_type === "stop") {
      label = "停留：" + (STOP_NAMES[seg.stop_type] || "未知");
    }
    $("now-state").textContent = label;

    // 温度超限时把那一格标红，一眼能看出来
    var tempCell = $("now-temp");
    var over = task && (p.temperature > task.temp_max || p.temperature < task.temp_min);
    tempCell.style.color = over ? "#e53935" : "";
    tempCell.style.fontWeight = over ? "600" : "";
  }

  function play() {
    if (state.playing || state.points.length < 2) return;
    state.playing = true;
    $("btn-play").textContent = "暂停";
    state.timer = setInterval(function () {
      if (state.index >= state.points.length - 1) {
        pause();
        return;
      }
      moveTo(state.index + 1);
    }, POINT_INTERVAL_MS / state.speed);
  }

  function pause() {
    state.playing = false;
    $("btn-play").textContent = "播放";
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
  }

  // ============================================================
  // 温度曲线
  // ============================================================

  function drawTempChart() {
    var canvas = $("temp-chart");
    if (!state.points.length || !state.report || !state.report.task) return;

    var ratio = window.devicePixelRatio || 1;
    var width = canvas.clientWidth;
    var height = 140;
    canvas.width = width * ratio;
    canvas.height = height * ratio;

    var ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);

    var padLeft = 32, padRight = 6, padTop = 8, padBottom = 16;
    var plotW = width - padLeft - padRight;
    var plotH = height - padTop - padBottom;

    var task = state.report.task;
    var t0 = state.points[0].ts;
    var t1 = state.points[state.points.length - 1].ts;
    var span = Math.max(t1 - t0, 1);

    var temps = state.points.map(function (p) { return p.temperature; });
    var lo = Math.min.apply(null, temps.concat([task.temp_min]));
    var hi = Math.max.apply(null, temps.concat([task.temp_max]));
    var margin = Math.max((hi - lo) * 0.1, 0.5);
    lo -= margin;
    hi += margin;
    var range = hi - lo;

    function xOf(ts) { return padLeft + (ts - t0) / span * plotW; }
    function yOf(temp) { return padTop + (hi - temp) / range * plotH; }

    // 允许温度范围画成一条绿色横带，超出去的部分一眼可见
    ctx.fillStyle = "rgba(67, 160, 71, .14)";
    ctx.fillRect(padLeft, yOf(task.temp_max), plotW, yOf(task.temp_min) - yOf(task.temp_max));

    // 上下限虚线
    ctx.save();
    ctx.setLineDash([4, 3]);
    ctx.strokeStyle = "rgba(67, 160, 71, .6)";
    ctx.lineWidth = 1;
    [task.temp_min, task.temp_max].forEach(function (t) {
      ctx.beginPath();
      ctx.moveTo(padLeft, yOf(t));
      ctx.lineTo(padLeft + plotW, yOf(t));
      ctx.stroke();
    });
    ctx.restore();

    // 温度曲线。逐段判断颜色，超限的那几段画成红色
    ctx.lineWidth = 1.6;
    for (var i = 1; i < state.points.length; i++) {
      var a = state.points[i - 1], b = state.points[i];
      var bad = b.temperature > task.temp_max || b.temperature < task.temp_min;
      ctx.strokeStyle = bad ? "#e53935" : "#1e88e5";
      ctx.beginPath();
      ctx.moveTo(xOf(a.ts), yOf(a.temperature));
      ctx.lineTo(xOf(b.ts), yOf(b.temperature));
      ctx.stroke();
    }

    // 纵轴刻度
    ctx.fillStyle = "#77879b";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "right";
    ctx.fillText(hi.toFixed(0) + "℃", padLeft - 4, padTop + 8);
    ctx.fillText(lo.toFixed(0) + "℃", padLeft - 4, padTop + plotH);

    // 当前回放位置
    var cur = state.points[state.index];
    if (cur) {
      ctx.strokeStyle = "rgba(31, 45, 61, .45)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(xOf(cur.ts), padTop);
      ctx.lineTo(xOf(cur.ts), padTop + plotH);
      ctx.stroke();

      ctx.fillStyle = "#1f2d3d";
      ctx.beginPath();
      ctx.arc(xOf(cur.ts), yOf(cur.temperature), 3, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // ============================================================
  // 侧栏渲染
  // ============================================================

  function renderStats() {
    var s = state.summary;
    var rows = [
      { label: "运输时长", value: s.duration_text || "--" },
      { label: "行驶里程", value: s.distance_text || "--" },
      { label: "平均速度", value: (s.avg_speed || 0).toFixed(1) + " m/s" },
      { label: "停留次数", value: (s.stop_count || 0) + " 次" },
      {
        label: "温度达标率",
        value: (s.temp_compliance || 0).toFixed(1) + "%",
        cls: (s.temp_compliance || 0) >= 95 ? "ok" : "bad"
      },
      { label: "偏航", value: (s.deviation_count || 0) + " 次", cls: s.deviation_count ? "bad" : "" },
      { label: "温度异常", value: (s.temperature_event_count || 0) + " 次", cls: s.temperature_event_count ? "bad" : "" },
      { label: "温度范围", value: (s.temp_min || 0).toFixed(1) + " ~ " + (s.temp_max || 0).toFixed(1) + " ℃" }
    ];

    $("stats").innerHTML = rows.map(function (r) {
      return '<div class="stat"><div class="label">' + r.label + '</div>' +
             '<div class="value ' + (r.cls || "") + '">' + r.value + '</div></div>';
    }).join("");
  }

  function renderEvents() {
    var list = $("event-list");
    $("event-count").textContent = String(state.events.length);

    if (!state.events.length) {
      list.innerHTML = '<li class="hint">这次运输没有异常事件</li>';
      return;
    }

    list.innerHTML = state.events.map(function (e, i) {
      var isDev = e.event_type === "deviation";
      var kind = isDev ? "偏航" : "温度异常";
      var detail = isDev
        ? "最远 " + (e.max_distance_m || 0).toFixed(0) + " 米"
        : (e.temp_min || 0).toFixed(1) + " ~ " + (e.temp_max || 0).toFixed(1) + " ℃";
      var where = e.during_stop ? "停留期间" : "行驶途中";
      return '<li data-index="' + i + '">' +
             '<span><span class="kind ' + e.event_type + '">' + kind + '</span> ' +
             durationText(e.duration_s) + '，' + detail + '</span>' +
             '<span class="meta">' + clockText(e.start_ts) + ' ' + where + '</span></li>';
    }).join("");

    // 点一条异常，时间轴就跳到那个时候，方便对着地图看
    Array.prototype.forEach.call(list.children, function (li) {
      li.addEventListener("click", function () {
        var e = state.events[Number(li.dataset.index)];
        if (!e) return;
        pause();
        seekToTs(e.start_ts);
      });
    });
  }

  function seekToTs(ts) {
    for (var i = 0; i < state.points.length; i++) {
      if (state.points[i].ts >= ts) { moveTo(i); return; }
    }
    moveTo(state.points.length - 1);
  }

  function renderStops() {
    var counts = {};
    var order = [];
    state.segments.forEach(function (s) {
      if (s.segment_type !== "stop") return;
      var name = STOP_NAMES[s.stop_type] || "未知";
      if (!counts[name]) { counts[name] = { n: 0, seconds: 0 }; order.push(name); }
      counts[name].n += 1;
      counts[name].seconds += s.duration_s;
    });

    if (!order.length) {
      $("stop-list").innerHTML = '<li class="hint">没有停留</li>';
      return;
    }

    $("stop-list").innerHTML = order.map(function (name) {
      return '<li><span class="name">' + name + '</span>' +
             '<span class="meta">' + counts[name].n + " 次 · " +
             durationText(counts[name].seconds) + '</span></li>';
    }).join("");
  }

  function renderConclusions() {
    var list = (state.report && state.report.conclusions) || [];
    $("conclusions").innerHTML = list.length
      ? list.map(function (c) { return "<li>" + c + "</li>"; }).join("")
      : '<li class="hint">暂无结论</li>';
  }

  // ============================================================
  // 加载数据
  // ============================================================

  function loadTasks() {
    return api("/api/tasks").then(function (data) {
      state.tasks = data.tasks || [];
      var select = $("task-select");

      if (!state.tasks.length) {
        select.innerHTML = '<option value="">（还没有任务）</option>';
        $("stats").innerHTML =
          '<p class="hint">数据库里还没有任务。先在命令行跑：<br>' +
          '<code>python scripts/seed_example.py</code></p>';
        return null;
      }

      select.innerHTML = state.tasks.map(function (t) {
        var points = t.track_points ? "（" + t.track_points + " 点）" : "（无轨迹）";
        return '<option value="' + t.id + '">' + t.task_no + " " +
               t.origin_name + " → " + t.dest_name + points + "</option>";
      }).join("");

      return state.tasks[0].id;
    });
  }

  function loadTask(taskId) {
    state.taskId = taskId;
    pause();

    var paths = [
      api("/api/tasks/" + taskId + "/route"),
      api("/api/tasks/" + taskId + "/track"),
      api("/api/tasks/" + taskId + "/analysis"),
      api("/api/tasks/" + taskId + "/report")
    ];

    return Promise.all(paths).then(function (res) {
      state.route = res[0].points || [];
      state.points = res[1].points || [];
      state.segments = res[2].segments || [];
      state.events = res[2].events || [];
      state.summary = res[2].summary || {};
      state.report = res[3];
      state.index = 0;   // 换任务要从头开始放，不能沿用上一个任务的时间下标

      redrawCurrent();
      renderStats();
      renderEvents();
      renderStops();
      renderConclusions();

      var timeline = $("timeline");
      timeline.max = String(Math.max(state.points.length - 1, 0));
      timeline.value = "0";
    });
  }

  function simulate() {
    var button = $("btn-simulate");
    button.disabled = true;
    $("busy").hidden = false;

    api("/api/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.taskId ? { task_id: state.taskId } : {})
    }).then(function () {
      return loadTasks();
    }).then(function (taskId) {
      // 重新生成之后任务可能变了，统一按最新数据重画
      return loadTask(state.taskId || taskId);
    }).catch(function (err) {
      $("map-notice").hidden = false;
      $("map-notice").textContent = "生成失败：" + err.message;
    }).then(function () {
      button.disabled = false;
      $("busy").hidden = true;
    });
  }

  // ============================================================
  // 启动
  // ============================================================

  function bindEvents() {
    $("task-select").addEventListener("change", function (e) {
      if (state.playing) pause();
      loadTask(Number(e.target.value)).catch(showError);
    });

    $("btn-simulate").addEventListener("click", simulate);
    $("btn-play").addEventListener("click", function () {
      if (state.playing) pause(); else play();
    });

    $("timeline").addEventListener("input", function (e) {
      // 手动拖动时先停下，否则会和定时器抢着改下标
      if (state.playing) pause();
      moveTo(Number(e.target.value));
    });

    $("speeds").addEventListener("click", function (e) {
      var button = e.target.closest("button[data-speed]");
      if (!button) return;
      state.speed = Number(button.dataset.speed);
      Array.prototype.forEach.call($("speeds").children, function (b) {
        b.classList.toggle("active", b === button);
      });
      // 速度变了要重建定时器才会生效
      if (state.playing) { pause(); play(); }
    });
  }

  function showError(err) {
    var notice = $("map-notice");
    notice.hidden = false;
    notice.textContent = err.message;
  }

  function boot() {
    bindEvents();

    // 地图初始化失败不算致命错误：切简图模式，其余功能照常。
    // 所以这里 catch 掉，不让它影响后面的数据加载。
    api("/api/config")
      .then(loadAmap)
      .then(function (AMap) {
        state.map = new AMap.Map("map", {
          zoom: 10,
          center: [116.6, 39.7],
          viewMode: "2D"
        });
        // 地图可能比数据晚准备好，这里补画一次
        redrawCurrent();
      })
      .catch(function (err) { useFallback(err.message); });

    loadTasks()
      .then(function (taskId) {
        if (!taskId) return null;
        return loadTask(taskId);
      })
      .catch(showError);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
