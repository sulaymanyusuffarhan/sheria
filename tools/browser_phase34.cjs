/* Specialist and oversight walkthrough, against a disposable synthetic database only. */
const assert = require("node:assert/strict");
const { mkdirSync } = require("node:fs");
const { dirname, resolve } = require("node:path");
const { createRequire } = require("node:module");
const runtime = createRequire(
  resolve(__dirname, "../.local/browser/package.json"),
);
const { chromium } = runtime("playwright-core");
const {
  default: sparticuz,
  inflate,
  setupLambdaEnvironment,
} = runtime("@sparticuz/chromium");
const axe = runtime("axe-core");
const base = new URL(process.env.LSMS_BROWSER_BASE || "http://localhost:8010/");
assert.equal(
  base.hostname,
  "localhost",
  "Use a disposable local demo database.",
);
const desktop = { width: 1440, height: 960 };
const mobile = { width: 390, height: 844 };
const at = (page, path) => page.goto(new URL(path, base).href);
const main = (page) => page.getByRole("main");
const link = (page, name) =>
  main(page).getByRole("link", { name, exact: true });
const button = (page, name) => page.getByRole("button", { name, exact: true });
const head = (page, name) =>
  page.getByRole("heading", { level: 1, name, exact: true });
const field = (page, name) => main(page).locator(`[name="${name}"]`);
const evidence = [];
const screenshots = process.env.LSMS_SCREENSHOTS === "1";
const artifact = resolve(__dirname, "../.local/phase34-browser-artifacts");
if (screenshots) mkdirSync(artifact, { recursive: true });
const localDay = (delta) =>
  new Date(Date.now() + (3 * 3600 + delta * 86400) * 1000)
    .toISOString()
    .slice(0, 10);

async function inspect(page, name) {
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(axe.source);
  const { violations } = await page.evaluate(() =>
    axe.run({ runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] }),
  );
  assert.deepEqual(
    violations.map(({ id, nodes }) => ({
      id,
      nodes: nodes.map(({ target, failureSummary }) => [
        target,
        failureSummary,
      ]),
    })),
    [],
    name,
  );
  const [view, width] = await page.evaluate(() => [
    window.innerWidth,
    document.documentElement.scrollWidth,
  ]);
  assert(width <= view + 1, `${name} overflows by ${width - view}px`);
  if (screenshots)
    await page.screenshot({
      path: resolve(artifact, `${name}.png`),
      fullPage: true,
    });
  evidence.push(name);
}

async function lookup(page, register, title) {
  await at(
    page,
    `/admin/registers/${register}/?q=${encodeURIComponent(title)}`,
  );
  await link(page, title).click();
  await head(page, title).waitFor();
  return page.url();
}

async function action(page, label, fill) {
  await link(page, label).click();
  await head(page, label).waitFor();
  if (await field(page, "reason").count())
    await field(page, "reason").fill(
      "Verified synthetic browser walkthrough; no legal authority is asserted.",
    );
  if (fill) await fill();
  await button(page, label).click();
  await page.waitForURL((url) => !url.pathname.includes("/actions/"));
}

async function run() {
  let executablePath = process.env.LSMS_BROWSER_EXECUTABLE;
  if (!executablePath) {
    const bundled = dirname(runtime.resolve("@sparticuz/chromium"));
    const libs = await inflate(resolve(bundled, "../bin/al2023.tar.br"));
    setupLambdaEnvironment(resolve(libs, "lib"));
    executablePath = await sparticuz.executablePath();
  }
  const browser = await chromium.launch({
    executablePath,
    headless: true,
    args: ["--disable-gpu"],
  });
  const errors = [];
  async function user(role, viewport = desktop) {
    const ctx = await browser.newContext({ viewport, acceptDownloads: true });
    ctx.setDefaultTimeout(8500);
    const page = await ctx.newPage();
    page.on("pageerror", (err) => errors.push(err.message));
    page.on("response", (res) => {
      if (res.status() >= 500) errors.push(res.url());
    });
    await at(page, "/login/");
    await page
      .getByText("Development demonstration", { exact: true })
      .waitFor();
    const pick = page.locator(`button[name="role"][value="${role}"]`);
    if (!(await pick.isVisible()))
      await page.getByText("More demonstration roles", { exact: true }).click();
    await pick.click();
    await page.waitForURL((url) => url.pathname !== "/login/");
    await page.waitForLoadState("domcontentloaded");
    if ((await page.locator("html").getAttribute("lang")) !== "en")
      await button(page, "EN — English").click();
    return page;
  }
  try {
    const officer = await user("officer");
    const senior = await user("senior");
    const headUser = await user("head");
    const staff = await user("staff", mobile);
    const records = await user("records");
    const manager = await user("management", mobile);
    const title = "Studio transmission dispute (demo)";
    const docket = await lookup(officer, "litigation", title);
    await inspect(officer, "litigation-docket");
    await at(senior, docket);
    assert(
      !(await main(senior).innerText()).includes("privileged strategy marker"),
    );
    assert.equal((await at(staff, docket)).status(), 403);
    const hearingDay = localDay(6);
    await action(officer, "Adjourn and reschedule", async () => {
      await field(officer, "hearing").selectOption({ index: 1 });
      await field(officer, "starts_at").fill(`${hearingDay}T11:45`);
      await field(officer, "room").fill("Fictional hearing court B");
      await field(officer, "purpose").fill("Browser adjournment replay");
    });
    const calendar = `/admin/calendar/?month=${hearingDay.slice(0, 7)}`;
    await at(officer, calendar);
    await main(officer)
      .getByText(/Browser adjournment replay/)
      .waitFor();
    await inspect(officer, "court-calendar");
    const calendarDay = main(officer)
      .getByRole("link", { name: /hearings? on / })
      .first();
    await calendarDay.focus();
    await officer.keyboard.press("Enter");
    await officer.waitForURL((url) => url.searchParams.has("day"));
    await officer.setViewportSize(mobile);
    await inspect(officer, "court-calendar-mobile");
    await button(officer, "SW — Kiswahili").click();
    await head(officer, "Kalenda ya mahakama").waitFor();
    await button(officer, "EN — English").click();
    await officer.setViewportSize(desktop);

    await at(officer, docket);
    await link(officer, "Documents").click();
    const docs = officer.url();
    await link(officer, "Sealed witness notes (demo)").click();
    const sealed = officer.url();
    const href = await link(officer, "Download version 1").getAttribute("href");
    const sample = await officer.request.get(new URL(href, base).href);
    assert.equal(sample.status(), 200);
    assert.equal((await sample.body()).subarray(0, 5).toString(), "%PDF-");
    assert.equal((await at(records, sealed)).status(), 404);
    assert.equal((await at(senior, sealed)).status(), 404);
    assert.equal(
      (await records.request.get(new URL(href, base).href)).status(),
      403,
    );
    await action(officer, "Upload new version", async () => {
      await field(officer, "provenance").fill(
        "Synthetic sealed addendum, retained separately.",
      );
      await field(officer, "document").setInputFiles({
        name: "sealed-addendum.pdf",
        mimeType: "application/pdf",
        buffer: await sample.body(),
      });
    });
    await link(officer, "Download version 2").waitFor();
    await inspect(officer, "sealed-library-record");
    await at(officer, docs);
    await link(officer, "Directions notice (demo)").click();
    await at(records, officer.url());
    await head(records, "Repository metadata").waitFor();
    assert(
      !(await main(records).innerText()).includes("directions-notice-demo.pdf"),
    );
    await action(records, "Place legal hold");
    assert.equal(await link(records, "Archive record").count(), 0);
    await inspect(records, "records-custody-metadata");

    await lookup(officer, "rights", "Weekend music package (demo)");
    await main(officer)
      .locator("summary")
      .filter({ hasText: "Harbour Session" })
      .first()
      .click();
    await main(officer)
      .getByText(/No OTT or archive reuse/)
      .filter({ visible: true })
      .waitFor();
    await inspect(officer, "rights-portfolio");
    await lookup(senior, "compliance", "Programme archive controls (demo)");
    await action(senior, "Verify corrective action", async () => {
      await field(senior, "remediation").selectOption({ index: 1 });
      await field(senior, "verdict").selectOption("verify");
    });
    await main(senior)
      .getByText("Verified and resolved", { exact: true })
      .waitFor();
    await inspect(senior, "compliance-verification");

    await at(staff, "/portal/requests/?q=Programme+quotation+opinion");
    await main(staff)
      .getByRole("link", { name: /Programme quotation opinion/ })
      .click();
    await link(staff, "Download issued opinion PDF").waitFor();
    const viewPdf = staff.waitForEvent("download");
    await link(staff, "Download issued opinion PDF").click();
    assert.equal((await viewPdf).suggestedFilename(), "opinion-v1.pdf");
    await inspect(staff, "issued-opinion-mobile");
    await lookup(headUser, "opinions", "Programme quotation opinion (demo)");
    await link(headUser, "Open latest frozen review").click();
    await main(headUser)
      .getByRole("heading", { name: "Exact frozen opinion" })
      .waitFor();
    await inspect(headUser, "frozen-opinion");

    await head(manager, "Management overview").waitFor();
    const csvHref = await link(manager, "Export CSV").getAttribute("href");
    const csv = await manager.request.get(new URL(csvHref, base).href);
    assert.equal(csv.status(), 200);
    assert.match(await csv.text(), /Operational indicators/);
    assert.doesNotMatch(
      await csv.text(),
      /DEMO\/DLS|Studio transmission dispute/,
    );
    assert.equal(
      (await staff.request.get(new URL(csvHref, base).href)).status(),
      403,
    );
    const spreadsheet = manager.waitForEvent("download");
    await link(manager, "Export Excel").click();
    assert.match(
      (await spreadsheet).suggestedFilename(),
      /^lsms-management-\d{4}\.xlsx$/,
    );
    await inspect(manager, "management-mobile");
    await manager.setViewportSize(desktop);
    await inspect(manager, "management-report");
    assert.deepEqual(errors, []);
    console.log(
      `Chromium ${browser.version()} Phase 3/4 flow and accessibility checks passed: ${evidence.join(", ")}`,
    );
  } finally {
    await browser.close();
  }
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
