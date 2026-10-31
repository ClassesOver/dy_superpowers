// 隔离测试库的只读浏览器验收；不保存会话、HTML、网络响应或凭据。
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

async function main() {
    const inputPath = path.resolve(process.argv[2]);
    const input = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
    const origin = new URL(input.url);
    assert(['localhost', '127.0.0.1'].includes(origin.hostname) && origin.port === '18069');
    assert(input.database.startsWith('dy_mcp_e2e_'));
    assert(input.url === process.env.APP_URL && input.database === process.env.DB_NAME);
    assert(input.data_readback_status === 'passed');
    assert(Array.isArray(input.records) && input.records.length > 0);
    assert(input.records.every(row => Number.isInteger(row.record_id) && row.record_id > 0));
    assert(process.env.APP_USER && process.env.APP_PASSWORD);
    const {chromium} = require(process.env.DY_PLAYWRIGHT_MODULE || 'playwright');
    const browser = await chromium.launch({headless: true,
        executablePath: process.env.DY_CHROMIUM_EXECUTABLE || chromium.executablePath()});
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
    let page = await context.newPage();
    const directory = path.dirname(inputPath);
    const result = {database: input.database, company_id: input.company_id,
        model: input.model, records: [],
        data_readback_status: 'passed', browser_acceptance_status: 'failed', accepted: false};
    if (input.research_context) result.research_context = input.research_context;
    if (input.report_id) result.report_id = input.report_id;
    let stage = 'login';
    try {
        const login = await page.goto(input.url + '/web/login?db=' + encodeURIComponent(input.database));
        assert(login.status() === 200);
        if (await page.getByText('程序授权已截止', {exact: false}).isVisible()) {
            stage = 'application_authorization';
            result.browser_acceptance_status = 'blocked';
            result.blocked_reason = 'HRP 程序授权已截止，需有效授权环境';
            throw new Error('应用授权阻塞');
        }
        await page.locator('input[name=login]').fill(process.env.APP_USER);
        await page.locator('input[name=password]').fill(process.env.APP_PASSWORD);
        await page.locator('button[type=submit]').click();
        await page.waitForURL(url => url.pathname === '/web', {timeout: 60000});
        for (const row of input.records) {
            stage = 'record-' + row.record_id;
            const hash = new URLSearchParams({id: String(row.record_id), model: input.model, view_type: 'form'});
            // 独立页面共享登录身份，每条记录重新初始化客户端及 action 状态。
            const previousPage = page;
            page = await context.newPage();
            await previousPage.close();
            await page.goto(input.url + '/web?db=' + encodeURIComponent(input.database) + '#' + hash);
            await page.locator('.o_form_view').waitFor({timeout: 60000});
            stage = 'record-' + row.record_id + '-route';
            const actualHash = new URLSearchParams(new URL(page.url()).hash.slice(1));
            assert(actualHash.get('id') === String(row.record_id) && actualHash.get('model') === input.model);
            stage = 'record-' + row.record_id + '-fields';
            // Odoo 的 hash 导航先变 URL，随后才替换表单；不能检查上一条的残留界面。
            const expectedFields = {name: row.expected_name};
            if (row.expected_code !== undefined) expectedFields.code = row.expected_code;
            await page.waitForFunction(expected => Object.entries(expected).every(([name, value]) => {
                const el = document.querySelector('.o_form_view [name=' + name + ']');
                return el && (el.value === undefined ? el.textContent.trim() : el.value) === value;
            }), expectedFields, {timeout: 60000});
            for (const [name, expected] of Object.entries(expectedFields)) {
                const field = page.locator('.o_form_view [name=' + name + ']').first();
                const actual = await field.evaluate(el => el.value === undefined ? el.textContent.trim() : el.value);
                assert(actual === expected);
            }
            const company = await page.locator('.o_switch_company_menu').innerText({timeout: 10000});
            stage = 'record-' + row.record_id + '-company';
            assert(company.includes(input.company_name));
            const recordCompany = await page.locator('.o_form_view [name=company_id]').innerText();
            assert(recordCompany.includes(input.company_name));
            const screenshot = 'configuration-browser-' + row.record_id + '.png';
            await page.screenshot({path: path.join(directory, screenshot), fullPage: true});
            result.records.push({record_id: row.record_id, browser_acceptance_status: 'passed', screenshot});
        }
        result.browser_acceptance_status = 'passed';
        result.accepted = true;
    } catch (_) {
        // 不把 Playwright 错误正文（可能包含表单参数）写入验收报告。
        result.failed_stage = stage;
        result.screenshot = 'configuration-browser-failed.png';
        await page.screenshot({path: path.join(directory, result.screenshot),
            mask: [page.locator('input[name=login]'), page.locator('input[name=password]'),
                   page.locator('textarea')]}).catch(() => {});
        process.exitCode = 1;
    } finally {
        await browser.close();
        fs.writeFileSync(path.join(directory, 'configuration-browser-result.json'), JSON.stringify(result, null, 2) + '\n');
        console.log(JSON.stringify(result));
    }
}

main().catch(() => { console.error('浏览器验收启动失败，请检查隔离环境和 Playwright。'); process.exitCode = 1; });
