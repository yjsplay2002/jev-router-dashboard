// SPDX-License-Identifier: Apache-2.0
// Run this function with a Playwright page against the local dashboard on port 8787.
async (page) => {
  const errors = [];
  page.on("console", message => {
    if (message.type() === "error" && /Content Security Policy|inline style/i.test(message.text())) errors.push(message.text());
  });
  let generation = 1;
  const run = () => ({
    run_id: "probability-browser-regression", status: "completed", task_count: 1,
    source_mtime: String(generation), tasks: [{
      id: "gauge", title: "Probability regression fixture", status: "completed",
      probabilities: generation === 1
        ? {a: 0.73, b: 0.12, c: 0.02, zero: 0, full: 1}
        : {a: 73, b: 12, c: 2, zero: 0, full: 100}
    }]
  });
  await page.route("**/api/runs?*", route => route.fulfill({
    contentType: "application/json", body: JSON.stringify({runs: [run()]})
  }));
  await page.goto("http://127.0.0.1:8787/");
  await page.waitForSelector(".prob-fill");
  const results = [];
  for (const viewport of [{width: 1440, height: 900}, {width: 390, height: 844}]) {
    await page.setViewportSize(viewport);
    const rows = await page.locator(".prob").evaluateAll(nodes => nodes.map(row => ({
      name: row.querySelector("span").textContent,
      label: row.querySelector("em").textContent,
      fraction: row.querySelector("rect").getBoundingClientRect().width / row.querySelector("svg").getBoundingClientRect().width
    })));
    for (const row of rows) {
      const expected = {a: .73, b: .12, c: .02, zero: 0, full: 1}[row.name];
      if (Math.abs(row.fraction - expected) > .001 || row.label !== Math.round(expected * 100) + "%") {
        throw new Error("Gauge mismatch: " + JSON.stringify(row));
      }
    }
    results.push({viewport, rows});
  }
  generation = 2;
  await page.waitForFunction(() => document.querySelector(".run-card")?.dataset.signature === "2");
  const updated = await page.locator(".prob").evaluateAll(nodes => nodes.map(row => ({
    name: row.querySelector("span").textContent,
    fraction: row.querySelector("rect").getBoundingClientRect().width / row.querySelector("svg").getBoundingClientRect().width
  })));
  for (const row of updated) {
    const expected = {a: .73, b: .12, c: .02, zero: 0, full: 1}[row.name];
    if (Math.abs(row.fraction - expected) > .001) throw new Error("Live update mismatch");
  }
  if (errors.length) throw new Error(errors.join("\n"));
  await page.unroute("**/api/runs?*");
  await page.goto("http://127.0.0.1:8787/");
  return {passed: true, results, liveUpdatePassed: true, cspErrors: errors};
}
