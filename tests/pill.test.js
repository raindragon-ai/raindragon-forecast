// node --test tests/  (Node 18+, no dependencies)
// Where the pill goes: above the message box, inside the viewport, and never
// over the page's own menus (the DOM part is checked by hand in a browser).
const test = require("node:test");
const assert = require("node:assert/strict");
const P = require("../extension/lib/pill.js").RainDragonEtaPill;

const rect = (left, top, width, height) => ({ left, top, width, height, right: left + width, bottom: top + height });

test("just above the box, near its left edge", () => {
  assert.deepEqual(P.place(rect(300, 430, 720, 100), 390, 28, 1100, 560), { left: 312, top: 396 });
});

test("kept inside the viewport on the right", () => {
  const at = P.place(rect(300, 430, 100, 100), 500, 28, 600, 560);
  assert.equal(at.left, 600 - 500 - 8);
});

test("below the box when there is no room above, never off the bottom", () => {
  assert.deepEqual(P.place(rect(10, 2, 300, 40), 200, 28, 800, 600), { left: 22, top: 48 });
  const at = P.place(rect(10, 0, 300, 590), 200, 28, 800, 600);
  assert.ok(at.top >= 4 && at.top + 28 <= 600);
});

test("no place for a box that is hidden or off screen", () => {
  assert.equal(P.place(null, 10, 10, 800, 600), null);
  assert.equal(P.place(rect(0, 0, 0, 0), 10, 10, 800, 600), null);
  assert.equal(P.place(rect(0, 700, 100, 50), 10, 10, 800, 600), null);
});

test("overlap", () => {
  assert.ok(P.intersects(rect(0, 0, 10, 10), rect(5, 5, 10, 10)));
  assert.ok(!P.intersects(rect(0, 0, 10, 10), rect(10, 0, 10, 10)));
});

test("below the page's popover layers, and watching their usual markers", () => {
  assert.ok(P.Z_INDEX < 50);
  for (const s of ['[role="menu"]', '[role="listbox"]', '[role="dialog"]', "[data-radix-popper-content-wrapper]",
                   ".cdk-overlay-pane"]) assert.ok(P.OVERLAYS.includes(s), s);
});
