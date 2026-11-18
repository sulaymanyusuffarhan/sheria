/* Disposable synthetic-only browser regression. Introduced 2026-11-18; revisions: Git. */
const assert = require("node:assert/strict");
const { mkdirSync } = require("node:fs");
const { dirname, resolve } = require("node:path");
const { createRequire } = require("node:module");
const runtime = resolve(__dirname, "../.local/browser/node_modules");
const requireRuntime = createRequire(resolve(runtime, "../package.json"));
const { chromium } = requireRuntime("playwright-core");
const {
  default: sparticuz,
  inflate,
  setupLambdaEnvironment,
} = requireRuntime("@sparticuz/chromium");
const axe = requireRuntime("axe-core");
const base = new URL(process.env.LSMS_BROWSER_BASE || "http://localhost:8010/");
// Do not run mutating demo tests against services on the network.
assert.equal(
  base.hostname,
  "localhost",
  "Use a dedicated local demo database/server",
);
const artifacts = resolve(__dirname, "../.local/browser-artifacts");
const screenshot = process.env.LSMS_SCREENSHOTS === "1";
if (screenshot) mkdirSync(artifacts, { recursive: true });
const head = (page, name) =>
  page.getByRole("heading", { name, level: 1, exact: true });
const link = (page, name) => page.getByRole("link", { name, exact: true });
const button = (page, name) => page.getByRole("button", { name, exact: true });
const at = (page, path) => page.goto(new URL(path, base).href);
const checkHeading = (page, name) =>
  head(page, name).waitFor({ state: "visible" });
const save = (page, name) =>
  screenshot &&
  page.screenshot({ path: resolve(artifacts, `${name}.png`), fullPage: true });
const pagesWithAxe = [];

async function checkAxe(page, name) {
  await page.evaluate(axe.source);
  const { violations } = await page.evaluate(async () =>
    axe.run({ runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] }),
  );
  assert.deepEqual(
    violations.map(({ id, nodes }) => ({
      id,
      elements: nodes.map(({ target, failureSummary }) => [
        target,
        failureSummary,
      ]),
    })),
    [],
    `axe ${name}`,
  );
  pagesWithAxe.push(name);
}

async function login(page, role, expected) {
  await at(page, "/login/");
  assert(
    await page
      .getByText("Development demonstration", { exact: true })
      .isVisible(),
    "No non-demo browser writes",
  );
  if (!(await button(page, role).isVisible())) {
    await page.getByText("More demonstration roles", { exact: true }).click();
  }
  await button(page, role).click();
  if ((await page.locator("html").getAttribute("lang")) !== "en") {
    await button(page, "EN — English").click();
  }
  await checkHeading(page, expected);
}

async function run() {
  let executablePath = process.env.LSMS_BROWSER_EXECUTABLE;
  if (!executablePath) {
    const bundled = dirname(requireRuntime.resolve("@sparticuz/chromium"));
    const libs = await inflate(resolve(bundled, "../bin/al2023.tar.br"));
    setupLambdaEnvironment(resolve(libs, "lib"));
    executablePath = await sparticuz.executablePath();
  }
  const browser = await chromium.launch({
    executablePath,
    headless: true,
    args: ["--disable-gpu"],
  });
  const serverErrors = [];
  const clientErrors = [];
  async function pageAt(viewport) {
    const ctx = await browser.newContext({ viewport });
    const page = await ctx.newPage();
    ctx.setDefaultTimeout(8500);
    page.on("pageerror", (error) => clientErrors.push(error.message));
    page.on("response", (response) => {
      if (response.status() >= 500) serverErrors.push(response.url());
    });
    return page;
  }
  try {
    const desktop = { width: 1440, height: 980 };
    const legal = await pageAt(desktop);
    await at(legal, "/login/");
    await legal.keyboard.press("Tab");
    assert.equal(
      await legal.locator(":focus").textContent(),
      "Skip to content",
    );
    await legal.keyboard.press("Enter");
    assert.equal(await legal.locator(":focus").getAttribute("id"), "main");
    await legal.locator("main").blur();
    await checkAxe(legal, "demo sign-in");
    await save(legal, "demo-login");
    await login(legal, "Legal dashboard", "Legal dashboard");
    await legal.evaluate(() => document.fonts.ready);
    assert(await legal.evaluate(() => document.fonts.check("14px Inter")));
    assert.equal(
      (await legal
        .locator(".metric-value")
        .first()
        .textContent()
        .then(Number)) > 30,
      true,
    );
    await checkAxe(legal, "legal dashboard");
    await save(legal, "legal-dashboard");
    await legal
      .getByLabel("Reporting period:", { exact: true })
      .selectOption("30");
    await legal
      .getByLabel("Directorate:", { exact: true })
      .selectOption({ index: 1 });
    await button(legal, "Apply").click();
    assert(legal.url().includes("period=30"));
    assert(await link(legal, "Reset").isVisible());
    const total = Number(
      await legal.locator(".metric-value").first().textContent(),
    );
    assert(total > 0 && total < 106);
    await legal.getByText("View chart data", { exact: true }).click();
    assert(
      await legal
        .getByRole("table", { name: "Matters opened by month" })
        .isVisible(),
    );
    await link(legal, "Reset").click();
    const reference = (
      await legal.locator(".recent-panel .row-sub").first().textContent()
    ).trim();
    await legal
      .getByRole("searchbox", { name: "Search this workspace" })
      .fill(reference);
    await legal
      .getByRole("searchbox", { name: "Search this workspace" })
      .press("Enter");
    await checkHeading(legal, "Matter register");
    assert.equal(await legal.locator(".row-title").count(), 1);
    await legal.locator(".row-title").click();
    await legal
      .getByRole("heading", { name: "Internal legal summary" })
      .waitFor();
    await button(legal, "SW — Kiswahili").click();
    assert.equal(await legal.locator("html").getAttribute("lang"), "sw");
    await legal
      .getByRole("heading", { name: "Muhtasari wa ndani wa kisheria" })
      .waitFor();
    await button(legal, "EN — English").click();
    console.log(
      "PASS legal charts, scoped filters, search, matter detail and EN/SW",
    );

    const staff = await pageAt(desktop);
    await login(staff, "Staff portal", "Welcome, Neema");
    await checkAxe(staff, "staff dashboard");
    await save(staff, "staff-dashboard");
    await link(staff, "View requests").click();
    await checkHeading(staff, "My requests");
    await checkAxe(staff, "staff register");
    if (await link(staff, "Next").isVisible()) {
      await link(staff, "Next").click();
      assert(staff.url().includes("page=2"));
      await link(staff, "Previous").click();
    }
    await staff.locator(".row-title").first().click();
    await staff.getByRole("heading", { name: "Published timeline" }).waitFor();
    assert.equal(
      await staff.getByText("Internal legal summary", { exact: true }).count(),
      0,
    );
    assert.equal((await at(staff, "/admin/matters/")).status(), 403);
    await checkHeading(staff, "Access restricted");
    await link(staff, "Return to workspace").click();
    await link(staff, "View all deadlines").click();
    await checkHeading(staff, "Upcoming deadlines");
    assert(staff.url().includes("due=upcoming"));
    await at(staff, "/notifications/");
    await button(staff, "Mark as read").first().click();
    await button(staff, "Mark all as read").click();
    await staff.getByText("0 unread notifications", { exact: true }).waitFor();
    await at(staff, "/profile/");
    await staff.getByLabel("Preferred language").selectOption("sw");
    await staff.getByLabel("Show unread notification count").uncheck();
    await button(staff, "Save preferences").click();
    await checkHeading(staff, "Wasifu wangu");
    assert.equal(
      await staff
        .getByLabel("Onyesha idadi ya arifa ambazo hazijasomwa")
        .isChecked(),
      false,
    );
    await staff.reload();
    assert.equal(await staff.locator("html").getAttribute("lang"), "sw");
    await button(staff, "Ondoka").click();
    await staff.getByText("Karibu kwenye LSMS", { exact: true }).waitFor();
    console.log(
      "PASS staff request timeline, deadlines, role denial, inbox, preferences and sign-out",
    );

    const ict = await pageAt(desktop);
    await login(ict, "ICT console", "System overview");
    await save(ict, "ict-dashboard");
    const key = Date.now().toString(36).slice(-6);
    const username = `browser-${key}`;
    await link(ict, "Organisation directory").click();
    await link(ict, "Add directorate").click();
    await ict.getByLabel("Unit code").fill(`DIR-${key.toUpperCase()}`);
    await ict.getByLabel("Name (English)").fill(`Browser ${key}`);
    await ict.getByLabel("Name (Kiswahili)").fill(`Jaribio ${key}`);
    await button(ict, "Save unit").click();
    await ict.getByRole("heading", { name: `Browser ${key}` }).waitFor();
    await link(ict, "Add department").click();
    await ict.getByLabel("Unit code").fill(`DEP-${key.toUpperCase()}`);
    await ict.getByLabel("Name (English)").fill(`Broadcast QA ${key}`);
    await ict
      .getByLabel("Directorate:", { exact: true })
      .selectOption({ label: `Browser ${key}` });
    await button(ict, "Save unit").click();
    await ict.getByText(`Broadcast QA ${key}`, { exact: true }).waitFor();
    await link(ict, "User accounts").click();
    await link(ict, "Add employee account").click();
    await checkAxe(ict, "employee form");
    await ict.getByLabel("Username").fill(username);
    await ict.getByLabel("Given name").fill("Browser");
    await ict.getByLabel("Family name").fill(`Fixture ${key}`);
    await ict.getByLabel("Work email").fill(`${username}@example.invalid`);
    await ict
      .getByLabel("Department:", { exact: true })
      .selectOption({ label: `Broadcast QA ${key}` });
    await button(ict, "Save account").click();
    await checkHeading(ict, `Browser Fixture ${key}`);
    const account = ict.url();
    await link(ict, "Request scoped access").click();
    await ict
      .getByLabel("Role:", { exact: true })
      .selectOption({ label: "Legal Officer" });
    await ict
      .getByLabel("Business justification")
      .fill("Synthetic assignment for the Phase 1 browser regression.");
    await button(ict, "Send for approval").click();
    await ict.getByText("Pending decision", { exact: true }).waitFor();
    assert.equal(await button(ict, "Apply approved access").count(), 0);
    const access = ict.url();
    await legal.goto(access);
    await legal
      .getByLabel("Decision:", { exact: true })
      .selectOption("approved");
    await legal
      .getByLabel("Decision reason")
      .fill("Independent synthetic reviewer confirmed role scope.");
    await button(legal, "Record decision").click();
    await legal.getByText("Approved for activation", { exact: true }).waitFor();
    assert.equal(await button(legal, "Apply approved access").count(), 0);
    await ict.reload();
    await button(ict, "Apply approved access").click();
    await ict.getByText("Applied", { exact: true }).waitFor();
    await ict.goto(account);
    await ict.getByText("Legal Officer", { exact: true }).waitFor();
    await button(ict, "Revoke").click();
    assert.equal(
      await ict.getByText("Legal Officer", { exact: true }).count(),
      0,
    );
    await link(ict, "Edit account").click();
    await ict.getByLabel("Active account").uncheck();
    await button(ict, "Save account").click();
    await ict.getByText("Inactive", { exact: true }).waitFor();
    await link(ict, "Audit log").click();
    await ict.getByText("Scoped access applied", { exact: true }).waitFor();
    assert.equal(
      await ict.getByText("Matter opened", { exact: true }).count(),
      0,
    );
    console.log(
      "PASS ICT directories/accounts, independent access decision, application, revocation, offboarding and technical audit",
    );

    const phone = await pageAt({ width: 390, height: 844 });
    await login(phone, "Staff portal", "Welcome, Neema");
    // Shared demo account preferences changed earlier; force EN for this viewport.
    await button(phone, "EN — English").click();
    const toggle = button(phone, "Toggle navigation");
    assert.equal(await toggle.getAttribute("aria-expanded"), "false");
    await toggle.focus();
    await phone.keyboard.press("Enter");
    assert.equal(await toggle.getAttribute("aria-expanded"), "true");
    await link(phone, "My requests").focus();
    await phone.keyboard.press("Escape");
    assert.equal(await toggle.getAttribute("aria-expanded"), "false");
    assert.equal(await phone.locator(":focus").getAttribute("data-menu"), "");
    assert(
      await phone.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth,
      ),
    );
    await checkAxe(phone, "390px staff dashboard");
    await save(phone, "staff-mobile");
    assert.deepEqual(serverErrors, []);
    assert.deepEqual(clientErrors, []);
    console.log(
      `PASS mobile keyboard navigation and width; no server errors. axe checked ${pagesWithAxe.join(", ")}.`,
    );
    console.log(`Chromium ${browser.version()}`);
  } finally {
    await browser.close();
  }
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
