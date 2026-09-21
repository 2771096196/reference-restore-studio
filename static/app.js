const $ = (s) => document.querySelector(s);
let project = null,
  frame = 0,
  shownFrame = 0,
  view = 'composite',
  shownView = 'composite',
  tool = 'restore',
  zoom = 1,
  playing = false,
  drawing = false,
  points = [],
  selectionStart = null,
  refreshId = 0,
  lastProject = null,
  revision = -1;
let settingsTimer,
  previewTimer,
  toastTimer,
  settingsPromise = Promise.resolve(),
  polling = false;
let spaceDown = false,
  gesture = null,
  lastPointer = null;
let previewController = null,
  exportSubmitting = false,
  cancelRequested = false,
  lastCompletedExport = null;
let editTarget = 'mask',
  thumbTimer = null,
  strokeConfig = null;
let strokeQueue = Promise.resolve(),
  pendingStrokes = 0;
const previewChoices = {
  eighth: '1/8 · 最流畅',
  quarter: '1/4 · 流畅',
  half: '1/2 · 均衡',
  video: '视频原尺寸',
  double: '2倍尺寸',
  original: '原图尺寸',
};
let previewResolution = 'half';
try {
  const saved = localStorage.getItem('retouch-preview-quality');
  if (Object.hasOwn(previewChoices, saved)) previewResolution = saved;
} catch {}
const previewClient = crypto.randomUUID();
const canvas = $('#paint-canvas'),
  ctx = canvas.getContext('2d'),
  image = $('#frame-image'),
  cursor = $('#cursor-canvas'),
  cx = cursor.getContext('2d');
function toast(s) {
  $('#toast').textContent = s;
  $('#toast').classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('#toast').classList.remove('show'), 4500);
}
async function api(path, body) {
  const r = await fetch('/api/' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(15000),
  });
  if (!r.ok) {
    const t = await r.text();
    try {
      throw Error(JSON.parse(t).error || t);
    } catch (e) {
      throw Error(e.message || t);
    }
  }
  return r.json();
}
function ready() {
  return project?.status?.phase === 'ready';
}
function timecode(f) {
  const s = f / (project?.meta?.fps || 24);
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${(s % 60).toFixed(2).padStart(5, '0')}`;
}
function settingsFromUI() {
  return {
    enabled: $('#layer-enabled').checked,
    auto: $('#auto-mask').checked,
    threshold: +$('#threshold').value,
    feather: +$('#feather').value,
    strength: +$('#strength').value / 100,
    face: $('#face-enabled').checked,
    face_feather: +$('#face-feather').value,
    face_strength: +$('#face-strength').value / 100,
  };
}
function labels() {
  for (const [id, unit] of [
    ['brush-size', ' px'],
    ['threshold', ' px'],
    ['feather', ' px'],
    ['face-feather', ' px'],
    ['strength', '%'],
    ['face-strength', '%'],
    ['brush-hardness', '%'],
    ['brush-opacity', '%'],
    ['brush-flow', '%'],
  ])
    $('#' + id + '-out').textContent = $('#' + id).value + unit;
}
function fillSettings(s) {
  $('#layer-enabled').checked = s.enabled !== false;
  $('#auto-mask').checked = s.auto;
  $('#threshold').value = s.threshold;
  $('#feather').value = s.feather;
  $('#strength').value = s.strength * 100;
  $('#face-enabled').checked = s.face;
  $('#face-feather').value = s.face_feather;
  $('#face-strength').value = s.face_strength * 100;
  labels();
}
function updateHint() {
  const hints = {
    restore: '白笔恢复原图；不透明度是单笔上限，流量是累积速度。',
    protect: '黑笔保留视频，柔边挖去回贴内容。',
    auto: '清除手绘覆盖，回到自动蒙版判断。',
    face: '拖框选择脸部，原图表情会固定。',
    hand: '拖动画布平移；按 B 返回画笔。',
    zoom: '点击放大，Alt 点击缩小；Ctrl+0 适应窗口。',
  };
  $('#tool-hint').textContent =
    editTarget === 'mask'
      ? hints[tool] || '编辑蒙版'
      : '当前内容只读，点击右侧蒙版缩略图或按 B 继续擦画。';
  $('#face-status').textContent = project?.face_region
    ? '已设置选区 · 可重新框选'
    : '尚未设置脸部选区';
  $('#current-tool-label').textContent =
    {
      restore: '画笔 · 擦回原图',
      protect: '画笔 · 保留视频',
      auto: '画笔 · 恢复自动',
      face: '刚性区域框选',
      hand: '抓手工具',
      zoom: '缩放工具',
    }[tool] || '画笔';
  $('#swap-brush').classList.toggle('black', tool === 'protect');
}
function selectLayer(target, changeView = true) {
  editTarget = target;
  $('#repair-layer-row').classList.toggle('selected', target !== 'video');
  $('#video-layer-row').classList.toggle('selected', target === 'video');
  for (const [key, id] of [
    ['mask', 'select-mask'],
    ['image', 'select-image'],
    ['video', 'select-video'],
  ])
    $('#' + id).classList.toggle('selected', target === key);
  $('#mask-selected-label').textContent =
    target === 'mask' ? '蒙版已选中' : '原图内容';
  $('#edit-target-label').textContent =
    target === 'mask'
      ? '修复蒙版'
      : target === 'image'
        ? '原图内容 · 只读'
        : '视频 · 只读';
  document.body.classList.toggle('readonly-canvas', target !== 'mask');
  if (changeView)
    setView(
      target === 'mask'
        ? 'composite'
        : target === 'image'
          ? 'aligned'
          : 'video',
    );
  updateHint();
}
function refreshThumbnails() {
  if (!ready() || thumbTimer) return;
  thumbTimer = setTimeout(
    () => {
      thumbTimer = null;
      if (!ready()) return;
      const rev = project.revision;
      const id = encodeURIComponent(project.project);
      if ($('#thumb-reference').dataset.project !== id) {
        $('#thumb-reference').src =
          `/api/thumbnail?mode=reference&project=${id}`;
        $('#thumb-reference').dataset.project = id;
      }
      $('#thumb-video').src =
        `/api/thumbnail?mode=video&frame=${shownFrame}&project=${id}`;
      $('#thumb-mask').src =
        `/api/thumbnail?mode=mask&frame=${shownFrame}&rev=${rev}&project=${id}`;
    },
    playing ? 500 : 80,
  );
}
function setView(v) {
  pause();
  view = v;
  document
    .querySelectorAll('[data-view]')
    .forEach((b) => b.classList.toggle('selected', b.dataset.view === view));
  refresh();
  updateHint();
}
function setTool(t) {
  if (drawing) return;
  pause();
  tool = t;
  document
    .querySelectorAll('[data-tool]')
    .forEach((b) => b.classList.toggle('selected', b.dataset.tool === t));
  $('#hand-tool').classList.toggle('active-tool', t === 'hand');
  $('#zoom-tool').classList.toggle('active-tool', t === 'zoom');
  $('#face-button').classList.toggle('active-tool', t === 'face');
  if (['restore', 'protect', 'auto', 'face'].includes(t)) {
    selectLayer('mask', false);
    if (['video', 'aligned'].includes(view)) setView('composite');
  }
  if (t === 'face') {
    setView('reference');
    toast('拖框包住脸部，松开即可保存。');
  }
  canvas.style.cursor =
    t === 'hand' ? 'grab' : t === 'zoom' ? 'zoom-in' : 'none';
  updateHint();
}
function drawOverlay() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (shownView === 'reference' && project?.face_region) {
    const [x0, y0, x1, y1] = project.face_region;
    ctx.save();
    ctx.strokeStyle = '#efbe72';
    ctx.lineWidth = 1.7;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    ctx.ellipse(
      ((x0 + x1) * canvas.width) / 2,
      ((y0 + y1) * canvas.height) / 2,
      ((x1 - x0) * canvas.width) / 2,
      ((y1 - y0) * canvas.height) / 2,
      0,
      0,
      Math.PI * 2,
    );
    ctx.stroke();
    ctx.restore();
  }
}
function previewDimensions(q = previewResolution) {
  const m = project?.meta;
  if (!m) return [784, 336];
  if (q === 'original') return [m.image_width, m.image_height];
  const scale = {
    eighth: 0.125,
    quarter: 0.25,
    half: 0.5,
    video: 1,
    double: 2,
  }[q];
  return [
    Math.max(1, Math.round(m.width * scale)),
    Math.max(1, Math.round(m.height * scale)),
  ];
}
function updatePreviewOptions() {
  for (const option of $('#preview-resolution').options) {
    const [w, h] = previewDimensions(option.value);
    option.textContent = `${previewChoices[option.value]} · ${w}×${h}`;
    option.disabled = w * h > 60000000 || Math.max(w, h) > 16384;
  }
  $('#preview-resolution').value = previewResolution;
}
function previewPixelWidth() {
  return image.naturalWidth || project?.meta?.width || 1;
}
function updateZoomLabel() {
  const width = $('#surface').getBoundingClientRect().width;
  $('#zoom-value').textContent =
    Math.abs(zoom - fitZoom()) < 0.01
      ? '适应'
      : Math.round((width / previewPixelWidth()) * 100) + '%';
}
async function refresh() {
  if (!ready() || drawing || pendingStrokes) return;
  const id = ++refreshId,
    f = frame,
    v = view,
    q = previewResolution;
  previewController?.abort();
  const controller = new AbortController();
  previewController = controller;
  const timer = setTimeout(() => controller.abort(), 45000);
  let url = null;
  document.body.classList.add('preview-loading');
  $('#preview-label').textContent = '更新预览…';
  try {
    const r = await fetch(
      `/api/frame?frame=${f}&mode=${v}&rev=${project.revision}&resolution=${q}&client=${previewClient}`,
      { signal: controller.signal },
    );
    if (!r.ok) throw Error(await r.text());
    const blob = await r.blob();
    if (id !== refreshId) return;
    url = URL.createObjectURL(blob);
    const loaded = new Image();
    loaded.src = url;
    await loaded.decode();
    if (id !== refreshId) return;
    const previous = image.dataset.blob;
    image.src = url;
    await image.decode();
    if (id !== refreshId) return;
    image.dataset.blob = url;
    if (previous) URL.revokeObjectURL(previous);
    shownFrame = f;
    shownView = v;
    canvas.width = project.meta.pw;
    canvas.height = project.meta.ph;
    cursor.width = canvas.width;
    cursor.height = canvas.height;
    $('#empty-stage').hidden = true;
    $('#frame-counter').textContent = `${f + 1} / ${project.meta.frames} 帧`;
    $('#timecode').textContent = timecode(f);
    $('#scrubber').value = f;
    updateZoomLabel();
    drawOverlay();
    refreshThumbnails();
  } catch (e) {
    if (id === refreshId && e.name !== 'AbortError')
      toast('预览暂未更新：' + e.message);
  } finally {
    clearTimeout(timer);
    if (url && image.dataset.blob !== url) URL.revokeObjectURL(url);
    if (id === refreshId) {
      document.body.classList.remove('preview-loading');
      $('#preview-label').textContent =
        `${image.naturalWidth}×${image.naturalHeight} · 不含AI超分`;
    }
  }
}
function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(refresh, 70);
}
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const s = await api('state');
    const wasReady = ready();
    project = s;
    $('#sample-button').hidden = !s.sample_available;
    const isReady = ready();
    if (s.project !== lastProject) lastCompletedExport = null;
    if (!lastCompletedExport && s.project) {
      try {
        lastCompletedExport = JSON.parse(
          localStorage.getItem('last-export-' + s.project) || 'null',
        );
      } catch {}
    }
    const exporting = s.export?.phase === 'exporting';
    $('#export-button').disabled = !isReady || exporting || exportSubmitting;
    $('#export-button').textContent = exporting
      ? `导出中 · ${s.export.progress}%`
      : exportSubmitting
        ? '正在启动…'
        : '导出 MP4 ↗';
    $('#export-button').title = exporting
      ? '正在导出，不能重复提交；可点击旁边的终止导出'
      : '';
    $('#top-export-state').hidden = !exporting;
    $('#top-export-detail').textContent = exporting ? s.export.message : '';
    $('#top-cancel-export').hidden = !exporting;
    if (!exporting) cancelRequested = false;
    for (const id of ['cancel-export', 'top-cancel-export']) {
      $('#' + id).disabled = cancelRequested;
      $('#' + id).textContent = cancelRequested ? '正在终止…' : '终止导出';
    }
    $('#undo-button').disabled = !isReady || !s.undo;
    $('#empty-stage').hidden = isReady;
    document.body.classList.toggle('busy', !isReady);
    if (s.meta?.image_name) {
      $('#image-name').textContent = s.meta.image_name;
      $('#video-name').textContent = s.meta.video_name;
      $('#project-info').textContent =
        `${s.meta.width} × ${s.meta.height} · ${s.meta.frames}帧 · ${s.meta.fps.toFixed(2)} FPS · ${s.meta.duration.toFixed(2)}秒`;
      $('#timeline-end').textContent = s.meta.duration.toFixed(2) + ' 秒';
      $('#scrubber').max = s.meta.frames - 1;
    }
    const busy =
      s.status.phase === 'analyzing'
        ? s.status
        : s.export?.phase === 'exporting'
          ? s.export
          : null;
    $('#job-panel').hidden = !busy;
    $('#cancel-export').hidden = s.export?.phase !== 'exporting';
    if (busy) {
      $('#job-title').textContent =
        s.status.phase === 'analyzing' ? '自动对齐与蒙版分析' : '导出视频';
      $('#job-percent').textContent = busy.progress + '%';
      $('#job-progress').value = busy.progress;
      $('#job-message').textContent = busy.message;
    }
    if (s.status.phase === 'error') {
      $('#job-panel').hidden = false;
      $('#job-title').textContent = '分析未完成';
      $('#job-message').textContent = s.status.message;
    }
    if (isReady && (lastProject !== s.project || !wasReady)) {
      lastProject = s.project;
      frame = 0;
      fillSettings(s.settings);
      updatePreviewOptions();
      revision = s.revision;
      $('#brush-size').max = Math.max(6, Math.floor(s.meta.width / 2));
      fitCanvas();
      refresh();
      updateHint();
    } else if (
      isReady &&
      revision !== s.revision &&
      !drawing &&
      !pendingStrokes
    ) {
      revision = s.revision;
      refresh();
      updateHint();
    }
    if (s.export?.phase === 'cancelled') {
      $('#job-panel').hidden = false;
      $('#job-title').textContent = '已取消导出';
      $('#job-message').textContent = s.export.message;
    }
    if (s.export?.phase === 'done') {
      lastCompletedExport = s.export;
      try {
        localStorage.setItem(
          'last-export-' + s.project,
          JSON.stringify(s.export),
        );
      } catch {}
    }
    const completed =
      s.export?.phase === 'done' ? s.export : lastCompletedExport;
    if (completed && s.export?.phase !== 'exporting') {
      $('#export-result').hidden = false;
      $('#export-path').textContent = completed.path;
      $('#download-video').href =
        '/api/download/' + encodeURIComponent(completed.filename);
      $('#download-video').download = completed.filename;
      $('#preview-export').dataset.filename = completed.filename;
    } else if (s.export?.phase === 'error') {
      $('#job-panel').hidden = false;
      $('#job-title').textContent = '导出失败';
      $('#job-message').textContent = s.export.message;
    } else if (s.export?.phase === 'exporting')
      $('#export-result').hidden = true;
  } catch (e) {
    $('#tool-hint').textContent =
      '本地服务未连接，请运行 server.py 或双击 start.bat。';
  } finally {
    polling = false;
  }
}
async function changeSettings() {
  if (!ready()) return;
  pause();
  clearTimeout(settingsTimer);
  settingsPromise = api('settings', settingsFromUI())
    .then(async (r) => {
      project.revision = r.revision;
      revision = r.revision;
      void refresh();
      return true;
    })
    .catch((e) => {
      toast(e.message);
      return false;
    });
  return settingsPromise;
}
document
  .querySelectorAll('[data-view]')
  .forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));
document
  .querySelectorAll('[data-tool]')
  .forEach((b) => b.addEventListener('click', () => setTool(b.dataset.tool)));
for (const id of [
  'layer-enabled',
  'auto-mask',
  'threshold',
  'feather',
  'strength',
  'face-enabled',
  'face-feather',
  'face-strength',
])
  $('#' + id).addEventListener('input', () => {
    labels();
    clearTimeout(settingsTimer);
    settingsTimer = setTimeout(changeSettings, 170);
  });
for (const id of [
  'brush-size',
  'brush-hardness',
  'brush-opacity',
  'brush-flow',
])
  $('#' + id).addEventListener('input', labels);
$('#face-button').onclick = () => setTool('face');
function coords(e) {
  const r = image.getBoundingClientRect();
  return [
    Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
    Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)),
  ];
}
function drawCursor(e, adjusting = false) {
  if (!ready()) return;
  lastPointer = e;
  const pp = coords(e);
  cx.clearRect(0, 0, cursor.width, cursor.height);
  if (
    (tool === 'face' ||
      tool === 'hand' ||
      tool === 'zoom' ||
      editTarget !== 'mask') &&
    !adjusting
  )
    return;
  const rr = (+$('#brush-size').value / 2 / project.meta.width) * cursor.width;
  const x = pp[0] * cursor.width,
    y = pp[1] * cursor.height;
  if (adjusting) {
    const hard = +$('#brush-hardness').value / 100;
    const grad = cx.createRadialGradient(
      x,
      y,
      Math.max(0.01, rr * hard),
      x,
      y,
      Math.max(0.02, rr),
    );
    grad.addColorStop(0, 'rgba(255,70,80,.55)');
    grad.addColorStop(1, 'rgba(255,70,80,0)');
    cx.fillStyle = grad;
    cx.fillRect(x - rr, y - rr, rr * 2, rr * 2);
  }
  cx.strokeStyle = 'rgba(0,0,0,.9)';
  cx.lineWidth = 2;
  cx.beginPath();
  cx.arc(x, y, rr, 0, Math.PI * 2);
  cx.stroke();
  cx.strokeStyle = 'white';
  cx.lineWidth = 1;
  cx.stroke();
  cx.setLineDash([2, 3]);
  cx.beginPath();
  cx.arc(x, y, rr * (+$('#brush-hardness').value / 100), 0, Math.PI * 2);
  cx.stroke();
  cx.setLineDash([]);
}
function finishGesture() {
  gesture = null;
  $('#brush-hud').hidden = true;
  canvas.style.cursor =
    spaceDown || tool === 'hand'
      ? 'grab'
      : tool === 'zoom'
        ? 'zoom-in'
        : 'none';
  drawOverlay();
  if (lastPointer && !spaceDown) drawCursor(lastPointer);
}
canvas.addEventListener('pointerdown', (e) => {
  if (!ready()) return;
  pause();
  canvas.focus({ preventScroll: true });
  if (spaceDown && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    zoomTo(zoom * (e.altKey ? 0.8 : 1.25), { x: e.clientX, y: e.clientY });
    return;
  }
  if (spaceDown || tool === 'hand' || e.button === 1) {
    e.preventDefault();
    gesture = {
      kind: 'pan',
      x: e.clientX,
      y: e.clientY,
      left: $('#viewport').scrollLeft,
      top: $('#viewport').scrollTop,
    };
    canvas.setPointerCapture(e.pointerId);
    canvas.style.cursor = 'grabbing';
    return;
  }
  if (tool === 'zoom' && e.button === 0) {
    e.preventDefault();
    zoomTo(zoom * (e.altKey ? 0.8 : 1.25), { x: e.clientX, y: e.clientY });
    return;
  }
  if (e.altKey && (e.button === 0 || e.button === 2)) {
    e.preventDefault();
    gesture = {
      kind: 'brush',
      x: e.clientX,
      y: e.clientY,
      size: +$('#brush-size').value,
      hardness: +$('#brush-hardness').value,
      scale: project.meta.width / canvas.getBoundingClientRect().width,
    };
    canvas.setPointerCapture(e.pointerId);
    drawCursor(e, true);
    return;
  }
  if (e.button !== 0 || document.body.classList.contains('preview-loading'))
    return;
  if (editTarget !== 'mask') {
    toast('先点击修复层的蒙版缩略图，再用画笔编辑。');
    return;
  }
  strokeConfig = {
    radius: +$('#brush-size').value / 2 / project.meta.width,
    hardness: +$('#brush-hardness').value / 100,
    opacity: +$('#brush-opacity').value / 100,
    flow: +$('#brush-flow').value / 100,
    mode: tool,
    frame: shownFrame,
    space: shownView === 'reference' ? 'reference' : 'current',
  };
  drawing = true;
  canvas.setPointerCapture(e.pointerId);
  points = [coords(e)];
  selectionStart = points[0];
  e.preventDefault();
});
canvas.addEventListener('pointermove', (e) => {
  if (gesture?.kind === 'pan') {
    $('#viewport').scrollLeft = gesture.left - (e.clientX - gesture.x);
    $('#viewport').scrollTop = gesture.top - (e.clientY - gesture.y);
    return;
  }
  if (gesture?.kind === 'brush') {
    const dx = e.clientX - gesture.x,
      dy = e.clientY - gesture.y;
    $('#brush-size').value = Math.max(
      6,
      Math.min(
        +$('#brush-size').max,
        Math.round(gesture.size + dx * gesture.scale),
      ),
    );
    $('#brush-hardness').value = Math.max(
      0,
      Math.min(100, Math.round(gesture.hardness + dy * 0.5)),
    );
    labels();
    drawCursor(e, true);
    const hud = $('#brush-hud');
    hud.hidden = false;
    hud.textContent = `直径 ${$('#brush-size').value}px · 硬度 ${$('#brush-hardness').value}%`;
    hud.style.left =
      Math.min(innerWidth - 235, Math.max(5, e.clientX + 18)) + 'px';
    hud.style.top = Math.min(innerHeight - 45, e.clientY + 22) + 'px';
    return;
  }
  if (!spaceDown) drawCursor(e);
  if (!drawing) return;
  const pt = coords(e);
  points.push(pt);
  if (points.length > 3990) points = points.filter((_, i) => i % 2 === 0);
  if (tool === 'face') {
    drawOverlay();
    ctx.strokeStyle = '#edbb76';
    ctx.lineWidth = 2;
    ctx.strokeRect(
      selectionStart[0] * canvas.width,
      selectionStart[1] * canvas.height,
      (pt[0] - selectionStart[0]) * canvas.width,
      (pt[1] - selectionStart[1]) * canvas.height,
    );
  } else {
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.strokeStyle =
      tool === 'restore'
        ? 'rgba(244,200,125,.65)'
        : tool === 'protect'
          ? 'rgba(20,28,34,.8)'
          : 'rgba(135,175,190,.6)';
    ctx.lineWidth =
      (+$('#brush-size').value / project.meta.width) * canvas.width;
    const last = points[points.length - 2];
    ctx.beginPath();
    ctx.moveTo(last[0] * canvas.width, last[1] * canvas.height);
    ctx.lineTo(pt[0] * canvas.width, pt[1] * canvas.height);
    ctx.stroke();
  }
});
canvas.addEventListener('contextmenu', (e) => e.preventDefault());
async function finishStroke(e) {
  if (gesture) {
    finishGesture();
    return;
  }
  if (!drawing) return;
  const final = coords(e);
  const submitted = {
    points: [...points.map((p) => p.slice()), final],
    config: { ...strokeConfig },
    start: selectionStart?.slice(),
  };
  drawing = false;
  points = [];
  selectionStart = null;
  strokeConfig = null;
  pendingStrokes++;
  const apply = async () => {
    try {
      if (submitted.config.mode === 'face') {
        const a = submitted.start,
          b = final;
        const region = [
          Math.min(a[0], b[0]),
          Math.min(a[1], b[1]),
          Math.max(a[0], b[0]),
          Math.max(a[1], b[1]),
        ];
        await api('face', { region });
        project.face_region = region;
        toast('脸部选区已保存。切到合成结果检查。');
        if (!drawing) setTool('restore');
      } else {
        await api('stroke', { points: submitted.points, ...submitted.config });
      }
    } catch (error) {
      toast(error.message);
    } finally {
      pendingStrokes--;
      if (!pendingStrokes) {
        await poll();
        if (!drawing) await refresh();
      }
    }
  };
  strokeQueue = strokeQueue.then(apply, apply);
  await strokeQueue;
}
canvas.addEventListener('pointerleave', () =>
  cx.clearRect(0, 0, cursor.width, cursor.height),
);
canvas.addEventListener('pointerup', finishStroke);
canvas.addEventListener('pointercancel', () => {
  drawing = false;
  points = [];
  strokeConfig = null;
  selectionStart = null;
  finishGesture();
  drawOverlay();
});
$('#scrubber').addEventListener('input', () => {
  pause();
  frame = +$('#scrubber').value;
  schedulePreview();
});
$('#previous-frame').onclick = () => {
  if (ready()) {
    pause();
    frame = Math.max(0, frame - 1);
    refresh();
  }
};
$('#next-frame').onclick = () => {
  if (ready()) {
    pause();
    frame = Math.min(project.meta.frames - 1, frame + 1);
    refresh();
  }
};
function pause() {
  playing = false;
  $('#play-button').textContent = '▶';
  $('#play-button').setAttribute('aria-label', '播放修复预览');
}
$('#play-button').onclick = async () => {
  if (!ready()) return;
  if (playing) {
    pause();
    return;
  }
  if (view === 'reference') setView('composite');
  playing = true;
  $('#play-button').textContent = 'Ⅱ';
  $('#play-button').setAttribute('aria-label', '暂停修复预览');
  while (playing) {
    const start = performance.now();
    frame =
      (frame + Math.max(1, Math.round(project.meta.fps / 8))) %
      project.meta.frames;
    await refresh();
    await new Promise((r) =>
      setTimeout(r, Math.max(0, 125 - (performance.now() - start))),
    );
  }
};
function fitZoom() {
  const p = $('#viewport');
  return ready()
    ? Math.min(
        1,
        p.clientHeight /
          (p.clientWidth *
            (image.naturalHeight && image.naturalWidth
              ? image.naturalHeight / image.naturalWidth
              : project.meta.height / project.meta.width)),
      )
    : 1;
}
function zoomTo(n, anchor) {
  if (drawing) return;
  const p = $('#viewport'),
    surface = $('#surface'),
    old = surface.getBoundingClientRect(),
    vr = p.getBoundingClientRect();
  const point = anchor || {
    x: vr.left + p.clientWidth / 2,
    y: vr.top + p.clientHeight / 2,
  };
  const u = (point.x - old.left) / old.width,
    v = (point.y - old.top) / Math.max(1, old.height);
  zoom = Math.min(32, Math.max(0.05, n));
  surface.style.width = Math.round(zoom * p.clientWidth) + 'px';
  const next = surface.getBoundingClientRect();
  p.scrollLeft += next.left + u * next.width - point.x;
  p.scrollTop += next.top + v * next.height - point.y;
  updateZoomLabel();
  cx.clearRect(0, 0, cursor.width, cursor.height);
}
function fitCanvas() {
  zoomTo(fitZoom());
  $('#viewport').scrollLeft = 0;
  $('#viewport').scrollTop = 0;
}
$('#zoom-in').onclick = () => zoomTo(zoom * 1.25);
$('#zoom-out').onclick = () => zoomTo(zoom * 0.8);
$('#zoom-fit').onclick = fitCanvas;
$('#zoom-actual').onclick = () => {
  if (ready()) zoomTo(previewPixelWidth() / $('#viewport').clientWidth);
};
$('#viewport').addEventListener(
  'wheel',
  (e) => {
    if (!ready() || !e.altKey) return;
    e.preventDefault();
    zoomTo(zoom * Math.exp(-e.deltaY * 0.002), { x: e.clientX, y: e.clientY });
  },
  { passive: false },
);
$('#undo-button').onclick = async () => {
  if (!ready() || drawing) return;
  try {
    await strokeQueue;
    await api('undo', {});
    await poll();
    refresh();
  } catch (e) {
    toast(e.message);
  }
};
$('#clear-button').onclick = async () => {
  if (!ready() || drawing) return;
  try {
    await strokeQueue;
    await api('reset-mask', {});
    await poll();
    refresh();
    toast('已清除手绘蒙版，可撤销。脸部选区保持不变。');
  } catch (e) {
    toast(e.message);
  }
};
$('#download-mask').onclick = () => {
  if (!ready()) return;
  const a = document.createElement('a');
  a.href = `/api/mask-download?frame=${frame}`;
  a.download = `mask-${String(frame).padStart(4, '0')}.png`;
  a.click();
};
$('#help-button').onclick = () => $('#help-dialog').showModal();
function showImport() {
  pause();
  $('#import-dialog').showModal();
}
$('#import-button').onclick = showImport;
$('#empty-import').onclick = showImport;
document.querySelectorAll('[data-close]').forEach(
  (b) =>
    (b.onclick = () => {
      $('#' + b.dataset.close).close();
      if (b.dataset.close === 'video-dialog') $('#export-player').pause();
    }),
);
$('#sample-button').onclick = async () => {
  try {
    await api('sample', {});
    await poll();
  } catch (e) {
    toast(e.message);
  }
};
$('#import-form').onsubmit = async (e) => {
  e.preventDefault();
  $('#start-import').disabled = true;
  try {
    const r = await fetch('/api/import', {
      method: 'POST',
      body: new FormData(e.target),
    });
    if (!r.ok) throw Error(await r.text());
    await r.json();
    $('#import-dialog').close();
    lastProject = null;
    await poll();
    toast('已导入，正在自动对齐。');
  } catch (e) {
    toast(e.message);
  } finally {
    $('#start-import').disabled = false;
  }
};
function exportDimensions() {
  const m = project.meta;
  const mode = $('#export-size').value;
  if (mode === 'custom')
    return [+$('#export-width').value, +$('#export-height').value];
  if (mode === 'original') return [m.image_width, m.image_height];
  const scale = { video: 1, '2x': 2, '4x': 4 }[mode];
  return [m.width * scale, m.height * scale];
}
function updateExportDialog() {
  const custom = $('#export-size').value === 'custom';
  const [w, h] = exportDimensions();
  if (!custom) {
    $('#export-width').value = w;
    $('#export-height').value = h;
  }
  $('#export-width').disabled = !custom;
  $('#export-height').disabled = !custom;
  const ow = Math.ceil(w / 2) * 2,
    oh = Math.ceil(h / 2) * 2;
  $('#export-size-note').textContent =
    `输出 ${ow}×${oh} · ${((ow * oh) / 1000000).toFixed(1)}百万像素。原图 ${project.meta.image_width}×${project.meta.image_height}；视频 ${project.meta.width}×${project.meta.height}。比例不同时按目标尺寸缩放，奇数边长会补至偶数。`;
  $('#ai-options').hidden = $('#export-upscale').value !== 'realesrgan';
  $('#export-denoise-out').textContent = $('#export-denoise').value + '%';
  $('#large-export-warning').hidden = ow <= 4096 && oh <= 4096;
  $('#confirm-export').disabled =
    !Number.isInteger(w) ||
    !Number.isInteger(h) ||
    w < 2 ||
    h < 2 ||
    w > 16384 ||
    h > 16384 ||
    w * h > 60000000;
}
$('#export-button').onclick = () => {
  if (!ready()) return;
  pause();
  const o = project.export_preferences || {};
  $('#export-size').value = o.size_mode || 'video';
  $('#export-width').value = o.width || project.meta.width;
  $('#export-height').value = o.height || project.meta.height;
  $('#export-upscale').value = o.upscale || 'lanczos';
  $('#export-denoise').value = (o.denoise ?? 0.5) * 100;
  $('#export-device').value = o.device || 'auto';
  $('#export-tile').value = o.tile || 192;
  const choice = $('#export-upscale').querySelector(
    'option[value="realesrgan"]',
  );
  choice.disabled = !project.super_resolution?.available;
  updateExportDialog();
  $('#export-dialog').showModal();
};
for (const id of [
  'export-size',
  'export-width',
  'export-height',
  'export-upscale',
  'export-denoise',
])
  $('#' + id).addEventListener('input', updateExportDialog);
$('#export-form').onsubmit = async (e) => {
  e.preventDefault();
  if (exportSubmitting) return;
  exportSubmitting = true;
  $('#confirm-export').disabled = true;
  $('#confirm-export').textContent = '正在启动…';
  clearTimeout(settingsTimer);
  const [width, height] = exportDimensions();
  const options = {
    size_mode: $('#export-size').value,
    width,
    height,
    upscale: $('#export-upscale').value,
    denoise: +$('#export-denoise').value / 100,
    device: $('#export-device').value,
    tile: +$('#export-tile').value,
  };
  try {
    await strokeQueue;
    const applied = await changeSettings();
    if (!applied) return;
    await api('export', options);
    $('#export-dialog').close();
    await poll();
    toast('开始导出，高清回贴内容直接来自原图。');
  } catch (e) {
    toast(e.message);
  } finally {
    exportSubmitting = false;
    $('#confirm-export').disabled = false;
    $('#confirm-export').textContent = '开始导出';
  }
};
async function cancelExport() {
  if (cancelRequested) return;
  cancelRequested = true;
  for (const id of ['cancel-export', 'top-cancel-export']) {
    $('#' + id).disabled = true;
    $('#' + id).textContent = '正在终止…';
  }
  try {
    await api('cancel-export', {});
    toast('已请求终止，等待当前分块结束。蒙版和已完成视频不会删除。');
    await poll();
  } catch (e) {
    cancelRequested = false;
    toast(e.message);
  }
}
$('#cancel-export').onclick = cancelExport;
$('#top-cancel-export').onclick = cancelExport;
$('#top-export-state').onclick = () =>
  $('#job-panel').scrollIntoView({ behavior: 'smooth', block: 'center' });
$('#preview-export').onclick = () => {
  $('#export-player').src =
    '/api/export-video/' +
    encodeURIComponent($('#preview-export').dataset.filename);
  $('#video-dialog').showModal();
  $('#export-player')
    .play()
    .catch(() => {});
};
document.addEventListener('keydown', (e) => {
  if (document.querySelector('dialog[open]')) return;
  const command = e.ctrlKey || e.metaKey;
  if (command && ['+', '=', '-', '0', '1'].includes(e.key)) {
    e.preventDefault();
    if (!ready()) return;
    if (e.key === '0') fitCanvas();
    else if (e.key === '1')
      zoomTo(previewPixelWidth() / $('#viewport').clientWidth);
    else zoomTo(zoom * (e.key === '-' ? 0.8 : 1.25));
    return;
  }
  const active = document.activeElement;
  const typing =
    active.isContentEditable ||
    ['TEXTAREA', 'SELECT'].includes(active.tagName) ||
    (active.tagName === 'INPUT' &&
      !['range', 'checkbox', 'button'].includes(active.type));
  if (typing) return;
  if (e.code === 'Space') {
    e.preventDefault();
    spaceDown = true;
    canvas.style.cursor = 'grab';
    cx.clearRect(0, 0, cursor.width, cursor.height);
    return;
  }
  if (e.key.toLowerCase() === 'z' && command) {
    e.preventDefault();
    $('#undo-button').click();
    return;
  }
  if (command || e.altKey) return;
  if (
    e.key === '[' ||
    e.key === ']' ||
    e.code === 'BracketLeft' ||
    e.code === 'BracketRight'
  ) {
    e.preventDefault();
    const up = e.code === 'BracketRight' || e.key === ']';
    if (e.shiftKey)
      $('#brush-hardness').value = Math.max(
        0,
        Math.min(100, +$('#brush-hardness').value + (up ? 10 : -10)),
      );
    else
      $('#brush-size').value = Math.max(
        6,
        Math.min(
          +$('#brush-size').max,
          +$('#brush-size').value + (up ? 6 : -6),
        ),
      );
    labels();
    if (lastPointer) drawCursor(lastPointer);
  }
  if (e.key.toLowerCase() === 'x')
    setTool(tool === 'restore' ? 'protect' : 'restore');
  if (e.key.toLowerCase() === 'b')
    setTool(tool === 'protect' ? 'protect' : 'restore');
  if (e.key.toLowerCase() === 'e') setTool('protect');
  if (e.key.toLowerCase() === 'h') setTool('hand');
  if (e.key.toLowerCase() === 'z') setTool('zoom');
  if (/^(Digit|Numpad)[0-9]$/.test(e.code)) {
    const digit = Number(e.code.slice(-1));
    $(e.shiftKey ? '#brush-flow' : '#brush-opacity').value =
      digit === 0 ? 100 : digit * 10;
    labels();
  }
  if (e.key === 'ArrowLeft') $('#previous-frame').click();
  if (e.key === 'ArrowRight') $('#next-frame').click();
});
window.addEventListener('keyup', (e) => {
  if (e.code === 'Space') {
    spaceDown = false;
    canvas.style.cursor =
      gesture?.kind === 'pan'
        ? 'grabbing'
        : tool === 'hand'
          ? 'grab'
          : tool === 'zoom'
            ? 'zoom-in'
            : 'none';
  }
});
window.addEventListener('blur', () => {
  spaceDown = false;
  drawing = false;
  points = [];
  strokeConfig = null;
  selectionStart = null;
  finishGesture();
});
$('#video-dialog').addEventListener('close', () => {
  const player = $('#export-player');
  player.pause();
  player.removeAttribute('src');
  player.load();
});
$('#select-image').onclick = () => selectLayer('image');
$('#select-mask').onclick = () => selectLayer('mask');
$('#select-video').onclick = () => selectLayer('video');
$('#hand-tool').onclick = () => setTool('hand');
$('#zoom-tool').onclick = () => setTool('zoom');
$('#swap-brush').onclick = () =>
  setTool(tool === 'protect' ? 'restore' : 'protect');
$('#face-reselect').onclick = () => setTool('face');
document
  .querySelectorAll('summary input')
  .forEach((input) =>
    input.addEventListener('click', (e) => e.stopPropagation()),
  );
$('#preview-resolution').value = previewResolution;
$('#preview-resolution').onchange = () => {
  pause();
  previewResolution = $('#preview-resolution').value;
  try {
    localStorage.setItem('retouch-preview-quality', previewResolution);
  } catch {}
  refresh();
  if (previewResolution === 'original')
    toast('原图尺寸适合暂停检查细节，刷新会更慢；不会改变导出设置。');
};
labels();
poll();
setInterval(poll, 1200);
