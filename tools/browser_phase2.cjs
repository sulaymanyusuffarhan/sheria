/* Synthetic end-to-end workflow replay; uses the same local browser tools as browser_smoke.cjs. */
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
  "Use a disposable local synthetic demo server.",
);
const at = (page, path) => page.goto(new URL(path, base).href);
const link = (page, name) => page.getByRole("link", { name, exact: true });
const button = (page, name) => page.getByRole("button", { name, exact: true });
const heading = (page, name) =>
  page.getByRole("heading", { name, exact: true, level: 1 });
const due = new Date(Date.now() + 14 * 864e5).toISOString().slice(0, 10);
const evidence = [];
const artifactDir = resolve(__dirname, "../.local/phase2-browser-artifacts");
const screenshot = process.env.LSMS_SCREENSHOTS === "1";
if (screenshot) mkdirSync(artifactDir, { recursive: true });

function testPdf(name) {
  const objs = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R >>",
    "<< /Length 0 >>\nstream\n\nendstream",
  ];
  let data = "%PDF-1.4\n";
  const entries = ["0000000000 65535 f \n"];
  for (const [index, obj] of objs.entries()) {
    entries.push(`${String(data.length).padStart(10, "0")} 00000 n \n`);
    data += `${index + 1} 0 obj\n${obj}\nendobj\n`;
  }
  const pos = data.length;
  data += `xref\n0 5\n${entries.join("")}trailer\n<< /Root 1 0 R /Size 5 >>\nstartxref\n${pos}\n%%EOF\n`;
  return { name, mimeType: "application/pdf", buffer: Buffer.from(data) };
}

async function login(page, role) {
  await at(page, "/login/");
  await page.getByText("Development demonstration", { exact: true }).waitFor();
  if (!(await button(page, role).isVisible())) {
    await page.getByText("More demonstration roles", { exact: true }).click();
  }
  await button(page, role).click();
  await page.waitForURL(
    (url) => url.pathname === "/portal/" || url.pathname === "/admin/",
  );
  if ((await page.locator("html").getAttribute("lang")) !== "en")
    await button(page, "EN — English").click();
}

async function inspect(page, label) {
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(axe.source);
  const { violations } = await page.evaluate(async () =>
    axe.run({
      runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"],
    }),
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
    label,
  );
  const [view, width] = await page.evaluate(() => [
    window.innerWidth,
    document.documentElement.scrollWidth,
  ]);
  assert(width <= view + 1, `${label}: page overflows by ${width - view}px`);
  if (screenshot)
    await page.screenshot({
      path: resolve(artifactDir, `${label}.png`),
      fullPage: true,
    });
  evidence.push(label);
}

const field = (page, name) =>
  page.getByRole("main").locator(`[name="${name}"]`);
const mainLink = (page, name) =>
  page.getByRole("main").getByRole("link", { name, exact: true });

async function action(page, name, fill = null) {
  await mainLink(page, name).click();
  await heading(page, name).waitFor();
  if (await field(page, "reason").count())
    await field(page, "reason").fill(`DLS internal browser note: ${name}`);
  if (fill) await fill();
  await button(page, name).click();
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
  async function user(role, viewport = { width: 1440, height: 980 }) {
    const ctx = await browser.newContext({ viewport, acceptDownloads: true });
    ctx.setDefaultTimeout(10000);
    const page = await ctx.newPage();
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("response", (response) => {
      if (response.status() >= 500) errors.push(response.url());
    });
    await login(page, role);
    return page;
  }
  try {
    const title = `Browser contract ${Date.now().toString(36)}`;
    const staff = await user("Staff portal", { width: 390, height: 844 });
    await mainLink(staff, "New legal request").click();
    await heading(staff, "New legal request").waitFor();
    await button(staff, "SW — Kiswahili").click();
    await heading(staff, "Ombi jipya la kisheria").waitFor();
    await button(staff, "EN — English").click();
    await heading(staff, "New legal request").waitFor();
    assert.equal(await field(staff, "draft").isVisible(), false);
    await field(staff, "title").fill(title);
    await field(staff, "category").selectOption("contract");
    await field(staff, "office").fill("Broadcast scheduling");
    await field(staff, "summary").fill(
      "Synthetic contract request for full browser verification.",
    );
    await field(staff, "required_on").fill(due);
    await field(staff, "counterparty").fill("Fictional Broadcast Services");
    await field(staff, "contract_type").selectOption("content");
    await field(staff, "amount").fill("4200000");
    await field(staff, "currency").fill("TZS");
    await field(staff, "duration").fill("12 months");
    await field(staff, "draft").setInputFiles(testPdf("browser-draft.pdf"));
    await field(staff, "declaration").check();
    await inspect(staff, "mobile-intake");
    await button(staff, "Submit request").click();
    await heading(staff, title).waitFor();
    const staffURL = new URL(staff.url()).pathname;
    const id = staffURL.split("/").at(-2);
    const legalURL = `/admin/matters/${id}/`;
    assert.match(
      await staff.locator(".page-head .reference").textContent(),
      /^DEMO\/DLS\/REQ\//,
    );
    assert.equal((await at(staff, legalURL)).status(), 403);

    const senior = await user("Senior Legal Officer");
    await at(senior, "/notifications/");
    await senior
      .getByText("New request for preliminary review", { exact: true })
      .first()
      .waitFor();
    await senior.locator(`.inbox-list a[href="${legalURL}"]`).first().click();
    await action(senior, "Start preliminary review");
    await action(senior, "Request additional information", async () => {
      await field(senior, "public_message").fill(
        "Please confirm the project purpose.",
      );
      await field(senior, "due_on").fill(due);
    });
    await at(staff, staffURL);
    await mainLink(staff, "Send information").click();
    await field(staff, "reply").fill(
      "This pilot tests access to a fictional audio archive.",
    );
    await button(staff, "Send response").click();
    await heading(staff, title).waitFor();
    await at(senior, legalURL);
    await action(senior, "Verify staff response");
    await action(senior, "Accept request", () =>
      field(senior, "verified").check(),
    );
    await action(senior, "Assign legal officer", async () => {
      await field(senior, "officer").selectOption({ label: "Samira Mwinyi" });
      await field(senior, "due_on").fill(due);
    });
    await inspect(senior, "legal-assigned");

    const officer = await user("Legal Officer");
    await at(officer, "/notifications/");
    await officer
      .getByText("Legal work assigned to you", { exact: true })
      .first()
      .waitFor();
    await officer.locator(`.inbox-list a[href="${legalURL}"]`).first().click();
    await action(officer, "Begin legal review");
    await action(officer, "Upload legal document", async () => {
      await field(officer, "kind").selectOption("response");
      await field(officer, "document").setInputFiles(
        testPdf("browser-officer-response.pdf"),
      );
    });
    await officer
      .getByRole("link", { name: /^Open contract and checklist/ })
      .click();
    await heading(officer, title).waitFor();
    const contractURL = new URL(officer.url()).pathname;
    await inspect(officer, "checklist-overview");
    const parts = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"];
    let count = 0;
    for (const part of parts) {
      await officer.locator(`#part-${part}`).getByRole("link").click();
      const items = await officer.locator("[data-check]").all();
      for (const item of items) {
        await item.getByLabel("Answer:", { exact: true }).selectOption("yes");
        count++;
      }
      if (part === "I") {
        await field(officer, "I-08_choice").selectOption("na");
        await field(officer, "I-08_remarks").fill(
          "No prior correspondence in this browser scenario.",
        );
        await inspect(officer, "checklist-edit");
      }
      await button(officer, "Save section").click();
      await heading(officer, title).waitFor();
    }
    assert.equal(count, 79);
    await officer.locator("#part-XI").getByRole("link").click();
    for (const [name, value] of Object.entries({
      risk_class: "medium",
      risk_0_likelihood: "low",
      risk_0_impact: "medium",
    })) {
      await field(officer, name).selectOption(value);
    }
    for (const [name, value] of Object.entries({
      risk_0_category: "Licensing",
      risk_0_description: "Fictional rights limitation",
      risk_0_mitigation: "Verify the permissions schedule",
      risk_0_responsible: "Content office",
    })) {
      await field(officer, name).fill(value);
    }
    await button(officer, "Save section").click();
    await heading(officer, title).waitFor();
    await officer.getByText("79 / 79", { exact: true }).waitFor();
    await mainLink(officer, "Send for Head review").click();
    await field(officer, "recommendation").selectOption("approve");
    await field(officer, "reason").fill(
      "DLS internal browser note: assess the exact version and records.",
    );
    const published =
      "The fictional contract review is complete. See the response approved for release.";
    await field(officer, "response").fill(published);
    await officer
      .getByRole("checkbox", { name: "browser-officer-response.pdf" })
      .check();
    await field(officer, "confirmed").check();
    await button(officer, "Send to Head of Legal Services").click();
    await heading(officer, title).waitFor();
    assert.equal(
      (await at(officer, `${legalURL}actions/decide/`)).status(),
      403,
    );

    const head = await user("Legal dashboard");
    await at(head, "/notifications/");
    await head
      .getByText("A frozen review is awaiting a Head decision", { exact: true })
      .first()
      .waitFor();
    await head.locator(`.inbox-list a[href="${legalURL}"]`).first().click();
    await mainLink(head, "Record Head decision").click();
    await heading(head, "Frozen review package").waitFor();
    await head
      .locator("details summary")
      .filter({ hasText: "Preliminary Review" })
      .click();
    const frozen = new URL(head.url()).pathname;
    const download = head.waitForEvent("download");
    await mainLink(head, "Download checklist PDF").click();
    assert.match((await download).suggestedFilename(), /review-1\.pdf$/);
    await inspect(head, "frozen-head-review");
    await mainLink(head, "Record Head decision").click();
    await field(head, "outcome").selectOption("clear");
    await field(head, "reason").fill(
      "Confirmed the frozen synthetic review and all applicable gates.",
    );
    await field(head, "no_conflict").check();
    await field(head, "gates_verified").check();
    await field(head, "demo_confirmation").check();
    await button(head, "Record Head decision").click();
    await head.waitForURL((url) => url.pathname === frozen);
    await head.getByText("Head decision recorded.", { exact: true }).waitFor();
    await at(head, contractURL);
    await head
      .getByText(
        "No execution recorded. Legal clearance is not contract execution.",
        { exact: true },
      )
      .waitFor();
    await at(staff, staffURL);
    assert.equal(await staff.getByText(published, { exact: true }).count(), 0);
    assert.equal(
      await mainLink(staff, "browser-officer-response.pdf").count(),
      0,
    );
    await at(head, legalURL);
    await action(head, "Issue approved response", () =>
      field(head, "confirmed").check(),
    );
    await at(staff, staffURL);
    await staff.getByText(published, { exact: true }).waitFor();
    assert.equal(
      await staff
        .getByText("DLS internal browser note", { exact: false })
        .count(),
      0,
    );
    const released = staff.waitForEvent("download");
    await mainLink(staff, "browser-officer-response.pdf").click();
    assert.equal(
      (await released).suggestedFilename(),
      "browser-officer-response.pdf",
    );
    await inspect(staff, "mobile-issued-response");
    await at(staff, "/notifications/");
    await staff
      .getByText("Your approved legal response is available", { exact: true })
      .first()
      .waitFor();
    assert.deepEqual(errors, []);
    console.log(
      `PASS staff → verification → assignment → all 79 pre-clearance checks → Head decision → controlled issue (${browser.version()}).`,
    );
    console.log(
      "PASS PDF and approved file downloads, private-data denial, separated execution, and scoped in-app links.",
    );
    console.log(
      `PASS width and axe WCAG 2.0/2.1 AA checks: ${evidence.join(", ")}.`,
    );
  } finally {
    await browser.close();
  }
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
