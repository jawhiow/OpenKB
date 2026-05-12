(async () => {
  const { chromium } = await import('playwright');
  const browser = await chromium.launch();
  const page = await browser.newPage();

  try {
    console.log('Navigating to http://127.0.0.1:3000...');
    await page.goto('http://127.0.0.1:3000');

    // Wait for the main elements to load, indicating successful render
    await page.waitForSelector('text=Knowledge Bases', { timeout: 10000 });

    console.log('Page loaded successfully! Taking screenshot...');
    await page.screenshot({ path: 'screenshot.png' });
    console.log('Screenshot saved as screenshot.png');

    // Get the HTML content to verify what was rendered
    const html = await page.content();
    if (html.includes('Overview of the current knowledge base')) {
      console.log('Verification: Found main content text.');
    }

  } catch (error) {
    console.error('Error loading page:', error);
    await page.screenshot({ path: 'error_screenshot.png' });
    console.log('Error screenshot saved as error_screenshot.png');
  } finally {
    await browser.close();
  }
})();
