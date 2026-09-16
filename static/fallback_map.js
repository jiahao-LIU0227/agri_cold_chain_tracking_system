/* 简图模式。
 *
 * 高德地图的脚本加载不出来时（断网、Key 没配好、域名没进白名单），
 * 就用这个把路线和轨迹画在一个 canvas 上。画得不好看，但形状是对的，
 * 回放、停留点、偏航段、温度异常点全都能看。
 *
 * 为什么自己画而不是引一个图表库：本项目有一条硬要求是「断网也能演示」，
 * 从 CDN 引任何脚本都可能加载不出来，那就白搭了。
 *
 * 用法：
 *     var m = new FallbackMap(canvas);
 *     m.draw({route: [...], track: [...], stops: [...], deviations: [...], temperatures: [...]});
 *     m.moveMarker(lat, lng);
 */

function FallbackMap(canvas) {
  this.canvas = canvas;
  this.ctx = canvas.getContext("2d");
  this.data = null;
  this.marker = null;
  this.fit = null;

  var self = this;
  window.addEventListener("resize", function () { self.redraw(); });
}

/* 空白处留 24 像素，别让线贴着边框 */
FallbackMap.PADDING = 24;

/*
 * 算出经纬度 -> 画布像素的转换参数。
 *
 * 两个细节：
 *   - 经度要乘 cos(纬度)：同样 1 度，经度对应的实际距离比纬度短，
 *     不乘的话地图会被横向压扁
 *   - 纬度和画布的 y 方向是反的：纬度越大越靠北，在屏幕上越靠上
 */
FallbackMap.prototype._fit = function (points) {
  var width = this.canvas.clientWidth;
  var height = this.canvas.clientHeight;
  var pad = FallbackMap.PADDING;

  var lats = [], lngs = [];
  for (var i = 0; i < points.length; i++) {
    lats.push(points[i][0]);
    lngs.push(points[i][1]);
  }
  var minLat = Math.min.apply(null, lats), maxLat = Math.max.apply(null, lats);
  var minLng = Math.min.apply(null, lngs), maxLng = Math.max.apply(null, lngs);

  var kx = Math.cos((minLat + maxLat) / 2 * Math.PI / 180);
  var spanX = Math.max((maxLng - minLng) * kx, 1e-6);
  var spanY = Math.max(maxLat - minLat, 1e-6);

  var scale = Math.min((width - pad * 2) / spanX, (height - pad * 2) / spanY);

  // 缩放比算好之后，把图形在画布里居中
  var offsetX = (width - spanX * scale) / 2;
  var offsetY = (height - spanY * scale) / 2;

  return {
    minLat: minLat, minLng: minLng, kx: kx, scale: scale,
    offsetX: offsetX, offsetY: offsetY, height: height
  };
};

FallbackMap.prototype._project = function (lat, lng) {
  var f = this.fit;
  return {
    x: f.offsetX + (lng - f.minLng) * f.kx * f.scale,
    y: f.height - f.offsetY - (lat - f.minLat) * f.scale
  };
};

FallbackMap.prototype._polyline = function (points, color, width, alpha) {
  if (!points || points.length < 2) return;
  var ctx = this.ctx;
  ctx.save();
  ctx.globalAlpha = alpha === undefined ? 1 : alpha;
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.beginPath();
  for (var i = 0; i < points.length; i++) {
    var p = this._project(points[i][0], points[i][1]);
    if (i === 0) ctx.moveTo(p.x, p.y);
    else ctx.lineTo(p.x, p.y);
  }
  ctx.stroke();
  ctx.restore();
};

FallbackMap.prototype._dot = function (lat, lng, color, radius) {
  var p = this._project(lat, lng);
  var ctx = this.ctx;
  ctx.save();
  ctx.fillStyle = color;
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.restore();
};

FallbackMap.prototype.draw = function (data) {
  this.data = data;

  // 用路线和轨迹一起算范围，保证两者都在画面里
  var all = (data.route || []).concat(data.track || []);
  if (!all.length) return;
  this.fit = this._fit(all);

  this._resize();
  this.redraw();
};

FallbackMap.prototype._resize = function () {
  // canvas 的绘制分辨率要跟上 CSS 尺寸，否则高分屏上糊
  var ratio = window.devicePixelRatio || 1;
  var w = this.canvas.clientWidth;
  var h = this.canvas.clientHeight;
  if (this.canvas.width !== w * ratio || this.canvas.height !== h * ratio) {
    this.canvas.width = w * ratio;
    this.canvas.height = h * ratio;
  }
  this.ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
};

FallbackMap.prototype.redraw = function () {
  if (!this.data || !this.fit) return;
  var ctx = this.ctx;
  this._resize();

  ctx.clearRect(0, 0, this.canvas.clientWidth, this.canvas.clientHeight);
  ctx.fillStyle = "#fbfcfd";
  ctx.fillRect(0, 0, this.canvas.clientWidth, this.canvas.clientHeight);

  var data = this.data;

  // 规划路线画底下，实际轨迹画上面
  this._polyline(data.route, "#90a4ae", 5, 0.9);
  this._polyline(data.track, "#2e7d32", 3, 0.95);

  // 偏航段：加粗的橙线盖在轨迹上，一眼能看见
  (data.deviations || []).forEach(function (seg) {
    this._polyline(seg, "#fb8c00", 6, 0.85);
  }, this);

  (data.stops || []).forEach(function (s) {
    this._dot(s.lat, s.lng, "#1e88e5", 6);
  }, this);

  (data.temperatures || []).forEach(function (t) {
    this._dot(t.lat, t.lng, "#e53935", 6);
  }, this);

  if (this.marker) this._drawMarker();

  // 左上角标一下这是简图，免得演示时误会
  ctx.save();
  ctx.fillStyle = "rgba(255,255,255,.9)";
  ctx.fillRect(8, 8, 150, 24);
  ctx.fillStyle = "#77879b";
  ctx.font = "13px 'Microsoft YaHei', sans-serif";
  ctx.fillText("简图模式（未加载高德地图）", 14, 24);
  ctx.restore();
};

FallbackMap.prototype._drawMarker = function () {
  var ctx = this.ctx;
  var p = this._project(this.marker[0], this.marker[1]);
  ctx.save();
  ctx.fillStyle = "#1e88e5";
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(p.x, p.y, 7, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.restore();
};

FallbackMap.prototype.moveMarker = function (lat, lng) {
  this.marker = [lat, lng];
  this.redraw();
};

FallbackMap.prototype.fitView = function () { this.redraw(); };
