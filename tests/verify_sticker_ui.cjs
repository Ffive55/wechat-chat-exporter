// Run against dev_server.py with a fixture containing the target GIF message.
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {createHash} = require('node:crypto');
const assert = require('node:assert/strict');

(async () => {
  const [urlFile, output, messageId] = process.argv.slice(2);
  fs.mkdirSync(output, {recursive: true});
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  const context = await browser.newContext({viewport: {width: 1320, height: 880}});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(fs.readFileSync(urlFile, 'utf8'));
    await page.locator('.session').first().click();
    const selector = `article[data-message-id="${Number(messageId)}"] img.emoimg`;
    await page.locator(`article[data-message-id="${Number(messageId)}"]`).scrollIntoViewIfNeeded();
    await page.waitForFunction(selector => {
      const image = document.querySelector(selector);
      return image?.complete && image.naturalWidth > 0 && image.src.startsWith('data:image/gif;base64,');
    }, selector);
    const image = page.locator(selector);
    const source = await image.getAttribute('src');
    const sha256 = createHash('sha256').update(Buffer.from(source.split(',')[1], 'base64')).digest('hex');
    assert.equal(await page.locator('.media-wait.error').count(), 0);
    assert.equal(await page.getByText('sticker_gif', {exact: true}).count(), 0);
    await page.screenshot({path: path.join(output, 'gif-preview.png')});
    await page.locator('#online').uncheck();
    await page.locator('#export-format').selectOption('html');
    await page.locator('#export').click();
    await page.locator('#open-export').waitFor({state: 'visible'});
    const result = await page.evaluate(async () => (await window.pywebview.api.state()).result);
    assert.equal(result.missing.length, 0);
    const exported = await context.newPage();
    exported.on('pageerror', error => errors.push(error.message));
    const network = [];
    await exported.route(/^https?:/, route => {network.push(route.request().url());return route.abort();});
    await exported.goto(pathToFileURL(result.path).href);
    const gif = exported.locator('img.emoimg').filter({visible: true});
    const index = await gif.evaluateAll((images, source) => images.findIndex(image => image.src === source), source);
    assert.ok(index >= 0, 'HTML must embed the exact preview GIF bytes');
    const embedded = gif.nth(index);
    await embedded.scrollIntoViewIfNeeded();
    await embedded.evaluate(image => image.decode());
    const metadata = await exported.locator('#export-metadata').textContent();
    const media = JSON.parse(metadata).media.find(item => item.message_id === Number(messageId));
    assert.equal(media.sha256, sha256);
    assert.ok(media.frames > 1);
    const frames = new Set();
    for (let i = 0; i < 3; i++) {
      const png = await embedded.screenshot({path: path.join(output, `gif-frame-${i + 1}.png`)});
      frames.add(createHash('sha256').update(png).digest('hex'));
      await exported.waitForTimeout(350);
    }
    assert.ok(frames.size > 1, 'Exported GIF must visibly animate');
    await exported.screenshot({path: path.join(output, 'gif-html.png')});
    assert.deepEqual(network, []);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ok: true, messages: result.messages, embedded: result.embedded,
      frames: media.frames, sha256, visuallyDistinctFrames: frames.size, externalRequests: network.length}));
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error);process.exitCode = 1});
