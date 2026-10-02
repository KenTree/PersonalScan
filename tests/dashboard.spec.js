import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';

let server, dashboardUrl;
test.beforeAll(async () => {
  server = spawn('.venv/bin/python', ['-u', 'app.py', '--demo', '--no-browser', '--port', '0']);
  dashboardUrl = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Demo server did not start.')), 10000);
    let output = '';
    server.stdout.on('data', chunk => {
      output += chunk.toString();
      const match = output.match(/http:\/\/127\.0\.0\.1:\d+\/#token=[\w-]+/);
      if (match) { clearTimeout(timeout); resolve(match[0]); }
    });
    server.once('error', error => { clearTimeout(timeout); reject(error); });
    server.once('exit', code => { clearTimeout(timeout); reject(new Error(`Demo server exited: ${code}`)); });
  });
});
test.afterAll(async () => {
  if (server && server.exitCode === null) {
    const exited = new Promise(resolve => server.once('exit', resolve));
    server.kill(); await exited;
  }
});

test('React dashboard scans, filters, and preserves input during polling', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(dashboardUrl);
  const scan = page.getByRole('button', { name: 'Scan demo messages' });
  await expect(scan).toBeEnabled();
  const limit = page.getByLabel('Thread limit (1–150)');
  await limit.fill('3');
  await page.waitForTimeout(1700);
  await expect(limit).toHaveValue('3');
  await scan.click();
  await expect(page.locator('#status')).toContainText('Scan finished.');
  await expect(page.locator('#stats .stat').first().locator('strong')).toHaveText('3');
  await expect(page.locator('.card')).toHaveCount(3);
  await page.getByRole('button', { name: 'Applications', exact: true }).click();
  await expect(page.locator('.card')).toHaveCount(2);
  await page.getByRole('button', { name: 'All notable', exact: true }).click();
  await page.locator('.card details').first().locator('summary').click();
  await page.waitForTimeout(1700);
  await expect(page.locator('.card details').first()).toHaveAttribute('open', '');
  await limit.fill('151');
  await scan.click();
  expect(await limit.evaluate(input => input.validity.valid)).toBe(false);
  expect(errors).toEqual([]);
});

test('header hover brightens text without a background and retains the token', async ({ page }) => {
  await page.goto(dashboardUrl);
  await expect(page.getByRole('button', { name: 'Scan demo messages' })).toBeEnabled();
  const digest = page.getByRole('button', { name: 'Digest', exact: true });
  await digest.hover();
  await expect(digest).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  await expect(digest).toHaveCSS('color', 'rgb(255, 255, 255)');
  await digest.click();
  expect(page.url()).toBe(dashboardUrl);
  await page.reload();
  await expect(page.getByRole('button', { name: 'Scan demo messages' })).toBeEnabled();
  await page.getByRole('button', { name: 'Excluded', exact: true }).click();
  await expect(page.locator('#excluded-section')).toHaveAttribute('open', '');
});

test('mobile dashboard fits the viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(dashboardUrl);
  await expect(page.getByRole('button', { name: 'Scan demo messages' })).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('Gmail selector clears the digest before scanning the next account', async ({ page }) => {
  await page.goto(dashboardUrl);
  const scan = page.getByRole('button', { name: 'Scan demo messages' });
  await expect(scan).toBeEnabled();
  const account = page.getByLabel('Gmail account');
  await account.selectOption('demo-personal');
  await expect(scan).toBeEnabled();
  await page.getByLabel('Thread limit (1–150)').fill('3');
  await scan.click();
  await expect(page.locator('.card')).toHaveCount(3);
  await account.selectOption('demo-work');
  await expect(account).toHaveValue('demo-work');
  await expect(page.locator('.card')).toHaveCount(0);
  await expect(page.locator('#last')).toHaveText('No completed scan yet.');
  await expect(scan).toBeEnabled();
  await scan.click();
  await expect(page.locator('.card')).toHaveCount(3);
  const state = await page.evaluate(async () => {
    const token = new URLSearchParams(location.hash.slice(1)).get('token');
    return (await fetch('/api/state', { headers: { 'X-Scanner-Token': token } })).json();
  });
  expect(state.selected_account).toBe('demo-work');
});
