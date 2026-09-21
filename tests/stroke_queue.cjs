const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(
  require('node:path').join(__dirname, '../static/app.js'),
  'utf8',
);
const coords = source.slice(
  source.indexOf('function coords('),
  source.indexOf('function drawCursor('),
);
const finishStart = source.indexOf('async function finishStroke(');
const finish = source.slice(
  finishStart,
  source.indexOf('canvas.addEventListener', finishStart),
);
const calls = [];
const errors = [];
let renders = 0;
const rect = { left: 10, top: 20, width: 400, height: 200 };
const context = {
  drawing: false,
  strokeRect: null,
  gesture: null,
  points: [],
  selectionStart: null,
  strokeConfig: null,
  image: { getBoundingClientRect: () => rect },
  strokeQueue: Promise.resolve(),
  pendingStrokes: 0,
  project: {},
  api: (route, body) =>
    new Promise((resolve) =>
      calls.push({ route, body: JSON.parse(JSON.stringify(body)), resolve }),
    ),
  poll: async () => {},
  refresh: async () => {
    renders++;
  },
  toast: (error) => errors.push(error),
  drawOverlay: () => {},
  finishGesture: () => {},
  setTool: () => {},
};
vm.createContext(context);
vm.runInContext(coords + '\n' + finish, context);
const tick = () => new Promise((resolve) => setImmediate(resolve));
function start(x, y, frame) {
  context.drawing = true;
  context.points = [[x, y]];
  context.selectionStart = [x, y];
  context.strokeRect = { ...rect };
  context.strokeConfig = {
    mode: 'restore',
    frame,
    space: 'current',
    radius: 0.01,
    hardness: 0.5,
    opacity: 0.5,
    flow: 0.1,
  };
}
const event = (x, y) => ({
  clientX: rect.left + x * rect.width,
  clientY: rect.top + y * rect.height,
});
(async () => {
  assert.deepEqual(Array.from(context.coords(event(0.5, 0.5))), [0.5, 0.5]);
  for (const [width, height, left, top] of [
    [196, 84, 20, 50],
    [784, 336, -120, 90],
    [1568, 672, -500, -300],
    [60, 26, 100, 200],
  ]) {
    Object.assign(rect, { width, height, left, top });
    const point = context.coords(event(0.37, 0.63));
    assert.ok(
      Math.abs(point[0] - 0.37) < 1e-10 && Math.abs(point[1] - 0.63) < 1e-10,
      'Zoom/pan must not shift normalized coordinates',
    );
  }
  Object.assign(rect, { left: 10, top: 20, width: 400, height: 200 });
  start(0.1, 0.1, 0);
  const one = context.finishStroke(event(0.1, 0.2));
  await tick();
  start(0.7, 0.7, 60);
  const two = context.finishStroke(event(0.8, 0.8));
  await tick();
  assert.equal(calls.length, 1, 'Requests must preserve stroke order');
  start(0.3, 0.3, 80); // Third stroke is still held while earlier responses arrive.
  calls[0].resolve({ revision: 1 });
  await tick();
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[0].body.points, [
    [0.1, 0.1],
    [0.1, 0.2],
  ]);
  assert.deepEqual(calls[1].body.points, [
    [0.7, 0.7],
    [0.8, 0.8],
  ]);
  calls[1].resolve({ revision: 2 });
  await Promise.all([one, two]);
  assert.deepEqual(
    context.points,
    [[0.3, 0.3]],
    'Old responses must not erase the in-progress stroke',
  );
  assert.equal(
    renders,
    0,
    'Do not paint stale results while a new stroke is being drawn',
  );
  const three = context.finishStroke(event(0.4, 0.3));
  await tick();
  calls[2].resolve({ revision: 3 });
  await three;
  assert.deepEqual(
    calls.map((c) => c.body.frame),
    [0, 60, 80],
  );
  assert.equal(renders, 1);
  assert.equal(context.pendingStrokes, 0);
  assert.equal(errors.length, 0);
  console.log(
    'PASS: image-relative coordinates, ordered submissions, rapid strokes retained, no stale redraw.',
  );
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
