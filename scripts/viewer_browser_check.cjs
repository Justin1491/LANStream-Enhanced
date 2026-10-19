// Uses the installed Playwright/Chrome; no project dependencies are added.
const {chromium} = require(process.env.LANSTREAM_PLAYWRIGHT || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const manifest = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const output = path.resolve(process.argv[3] || 'benchmark-results/viewer-browser');
const url = (route, key) => manifest.url + '/' + route + '/0/' + manifest.files[key].split('/').map(encodeURIComponent).join('/');
let browser;

function waitForViewer(page, route, key) {
  const expected = new URL(url(route,key));
  return page.waitForURL(actual => actual.origin === expected.origin && decodeURIComponent(actual.pathname) === decodeURIComponent(expected.pathname));
}

async function imageReady(page, selector, channel, landscape) {
  await page.waitForFunction(selector => {
    const image = document.querySelector(selector);
    return image && image.complete && image.naturalWidth > 0;
  }, selector);
  const details = await page.locator(selector).evaluate(image => {
    const canvas = document.createElement('canvas');
    canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0);
    const rect = image.getBoundingClientRect();
    return {width:image.naturalWidth, height:image.naturalHeight,
      color:[...ctx.getImageData(canvas.width/2, canvas.height/2, 1, 1).data],
      rect:{width:rect.width,height:rect.height},
      fits:rect.width <= innerWidth && rect.height <= innerHeight};
  });
  if (channel !== undefined) assert.ok(details.color[channel] > Math.max(...details.color.slice(0,3).filter((_,i) => i !== channel)), JSON.stringify(details));
  if (landscape !== undefined) assert.equal(details.width > details.height, landscape);
  if (!details.fits) await page.screenshot({path:path.join(output,'overflow.png')});
  assert.ok(details.fits, 'image exceeds viewport: ' + JSON.stringify(details));
  assert.ok(await page.locator('#viewerStage').evaluate(stage => stage.getBoundingClientRect().bottom <= innerHeight), 'viewer controls extend below viewport');
}

async function swipe(page, selector) {
  await page.locator(selector).evaluate(stage => {
    for (const [type, x] of [['touchstart',260], ['touchend',100]]) {
      const touch = new Touch({identifier:1,target:stage,clientX:x,clientY:250});
      stage.dispatchEvent(new TouchEvent(type,{changedTouches:[touch],bubbles:true}));
    }
  });
}

(async () => {
  fs.mkdirSync(output, {recursive:true});
  browser = await chromium.launch({headless:true, chromiumSandbox:true,
    ...(process.env.LANSTREAM_BROWSER ? {executablePath:process.env.LANSTREAM_BROWSER} : {})});
  const report = {browser:browser.version(), fixture:'Fictional local files only', checks:[], expectedErrors:[], errors:[]};
  for (const [label, viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]) {
    const context = await browser.newContext({viewport, hasTouch:label === 'mobile'});
    context.setDefaultTimeout(10000);
    context.setDefaultNavigationTimeout(15000);
    const page = await context.newPage();
    let checkingError = false;
    page.on('pageerror', error => report.errors.push(error.message));
    page.on('console', message => {
      if (message.type() === 'error' && !(checkingError && message.text().includes('the server responded with a status of 415'))) report.errors.push(message.text());
    });
    page.on('response', response => {
      if (response.status() >= 400) {
        if (checkingError && response.status() === 415) report.expectedErrors.push(label + ': HTTP 415 ' + new URL(response.url()).pathname);
        else report.errors.push('HTTP ' + response.status() + ' ' + response.url());
      }
    });
    page.on('dialog', async dialog => { report.errors.push('Unexpected dialog: ' + dialog.message()); await dialog.dismiss(); });
    await page.goto(manifest.url + '/login');
    await page.locator('input[name=password]').fill('viewer-test');
    await page.getByRole('button',{name:'Log in',exact:true}).click();
    await page.waitForURL(manifest.url + '/');

    console.log(label + ': images');
    await page.goto(url('image','image_first'));
    await imageReady(page,'#viewerImage',0,false);
    await page.getByRole('link',{name:'Next image',exact:true}).click();
    await waitForViewer(page,'image','image_next');
    await imageReady(page,'#viewerImage',1,true);
    await page.keyboard.press('ArrowLeft');
    await waitForViewer(page,'image','image_first');
    await imageReady(page,'#viewerImage',0,false);
    if (label === 'desktop') {
      await page.getByRole('button',{name:'Fullscreen',exact:true}).click();
      await page.waitForFunction(() => !!document.fullscreenElement);
      await page.keyboard.press('ArrowRight');
      await waitForViewer(page,'image','image_next');
      await imageReady(page,'#viewerImage',1,true);
      assert.ok(await page.evaluate(() => !!document.fullscreenElement));
      await page.locator('#fullscreenExit').click();
      await page.waitForFunction(() => !document.fullscreenElement);
    } else {
      await swipe(page,'#viewerStage');
      await waitForViewer(page,'image','image_next');
      await imageReady(page,'#viewerImage',1,true);
    }
    await page.getByRole('link',{name:'Next image',exact:true}).click();
    await waitForViewer(page,'image','image_tiff');
    await imageReady(page,'#viewerImage',2,false);
    await page.screenshot({path:path.join(output,label+'-image.png')});
    await page.getByRole('link',{name:'Back to folder',exact:false}).click();
    await page.waitForURL(/\/browse\/0\/Images/);
    report.checks.push(label + ': Unicode image links, next/previous, TIFF, folder return, ' + (label === 'desktop' ? 'fullscreen' : 'swipe'));

    for (const key of ['pdf','cbz','cbr']) {
      console.log(label + ': ' + key);
      await page.goto(url('document',key));
      await imageReady(page,'#pageImage',0,false);
      assert.ok(await page.locator('#prevPage').isDisabled());
      if (label === 'mobile') await swipe(page,'#viewerStage');
      else await page.getByRole('button',{name:'Next page',exact:true}).click();
      await page.waitForFunction(() => document.querySelector('#pageNumber').textContent === '2');
      await imageReady(page,'#pageImage',1,true);
      await page.keyboard.press('PageDown');
      await page.waitForFunction(() => document.querySelector('#pageNumber').textContent === '3');
      await imageReady(page,'#pageImage',2,false);
      assert.ok(await page.locator('#nextPage').isDisabled());
      await page.keyboard.press('PageUp');
      await page.waitForFunction(() => document.querySelector('#pageNumber').textContent === '2');
      await imageReady(page,'#pageImage',1,true);
      if (label === 'desktop') {
        await page.getByRole('button',{name:'Fullscreen',exact:true}).click();
        await page.waitForFunction(() => !!document.fullscreenElement);
        assert.equal(await page.locator('#fullscreenExit').count(),1,'document fullscreen needs an exit control');
        await page.locator('#fullscreenExit').click();
        await page.waitForFunction(() => !document.fullscreenElement);
      }
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),'horizontal overflow');
      await page.screenshot({path:path.join(output,label+'-'+key+'.png')});
      await page.getByRole('link',{name:'Back to folder',exact:false}).click();
      await page.waitForURL(/\/browse\/0\//);
      report.checks.push(label + ': ' + key + ' natural page order, aspect, boundaries, next/previous, keyboard, folder return');
    }
    for (const key of ['text','markdown']) {
      await page.goto(url('text',key));
      const text = await page.locator('.plain-text-reader').textContent();
      assert.ok(text.includes('Fictional Café notes') && text.includes('<script>alert("fixture")</script>') && text.includes('A & B <example>'));
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),'text overflow');
      await page.screenshot({path:path.join(output,label+'-'+key+'.png')});
      report.checks.push(label + ': ' + key + ' UTF-8 and literal HTML escaping');
    }
    checkingError = true;
    for (const [route,key] of [['image','corrupt_image'],['document','corrupt_page_cbz']]) {
      await page.goto(url(route,key));
      await page.locator('#viewerStatus').filter({hasText:'Unable to display'}).waitFor({state:'visible'});
      assert.equal(await page.locator(route === 'image' ? '#viewerImage' : '#pageImage').evaluate(image => getComputedStyle(image).visibility),'hidden');
      await page.screenshot({path:path.join(output,label+'-'+route+'-error.png')});
    }
    report.checks.push(label + ': corrupt image/page display readable errors');
    await context.close();
  }
  assert.deepEqual(report.errors,[]);
  assert.equal(report.expectedErrors.length,4);
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify(report,null,2));
})().catch(error => { console.error(error); process.exitCode=1; }).finally(async () => { if (browser) await browser.close(); });
