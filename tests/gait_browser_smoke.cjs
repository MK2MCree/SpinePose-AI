// Isolated browser QA using public-sample reports; no production requests or mutations.
const {chromium}=require('C:/Users/MK2SG/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');

(async()=>{
  const base=path.resolve(process.argv[2]||'.analysis_tmp/gait-validation-b6691283ea/gait/validation-owner/public-sample');
  const fixtures=fs.readdirSync(base).flatMap(id=>{const output=path.join(base,id,'output'),file=path.join(output,'report.json');return fs.existsSync(file)?[{id,output,report:JSON.parse(fs.readFileSync(file,'utf8'))}]:[];});
  const multi=fixtures.find(f=>f.report.multi_subject),auto=fixtures.find(f=>f.report.requires_target_selection);
  assert(multi&&auto,'Need actual all-person and auto-target validation reports');
  const output=path.resolve('.analysis_tmp/gait-ui-qa');fs.mkdirSync(output,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  const results=[];
  try {
    for(const [name,fixture] of [['multi',multi],['auto',auto]]){
      const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];
      page.on('pageerror',error=>errors.push(error.message));
      const job={id:fixture.id,case_id:'visual-validation',filename:'public-sample.mp4',status:fixture.report.status,options:{device:'auto',analysis_mode:name==='multi'?'all':'single'},progress:100};
      const report={...fixture.report,file_urls:{}};
      for(const asset of ['input_preview.mp4','overlay.mp4','masks.mp4','contact_sheet.jpg'])if(fs.existsSync(path.join(fixture.output,asset)))report.file_urls[asset]='/fixture/'+asset;
      await page.route('**/*',async route=>{
        const url=new URL(route.request().url());if(url.origin!=='http://localhost:8000')return route.abort();
        if(url.pathname.startsWith('/api/')){const data=url.pathname.includes('/health')?{health:{available:true}}:url.pathname.includes('workspace')?{workspace:{case:{id:'visual-validation',title:'公开样例 · 界面验证'}}}:url.pathname.endsWith('/report')?{report}:url.pathname.endsWith('/jobs')?{jobs:[job]}:{job};return route.fulfill({json:data});}
        const file=url.pathname.startsWith('/fixture/')?path.join(fixture.output,path.basename(url.pathname)):path.join(process.cwd(),'web',path.basename(url.pathname));
        return fs.existsSync(file)?route.fulfill({path:file}):route.fulfill({status:404,body:''});
      });
      await page.goto('http://localhost:8000/gait-workspace.html?job_id='+fixture.id);
      await page.locator(name==='multi'?'.gait-subject-card':'#gait-target-prompt').first().waitFor();
      for(const width of [1440,390]){
        await page.setViewportSize({width,height:1000});
        await page.locator(name==='multi'?'#gait-result-label':'#gait-target-prompt').scrollIntoViewIfNeeded();
        const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),cards=await page.locator('.gait-subject-card').count();
        const overflowElements=overflow?await page.locator('body *').evaluateAll(nodes=>nodes.filter(n=>n.getBoundingClientRect().right>innerWidth+1).map(n=>n.id||n.className).slice(0,15)):[];
        await page.screenshot({path:path.join(output,`${name}-real-${width}.png`)});
        const result={name,width,cards,archiveDisabled:await page.locator('#gait-archive').isDisabled(),overflow,overflowElements,errors:[...errors]};results.push(result);console.log(JSON.stringify(result));
        assert.equal(overflow,false,'Unexpected horizontal overflow');assert.equal(errors.length,0);assert.equal(result.archiveDisabled,true);
        if(name==='multi')assert.equal(cards,4);else assert.equal(await page.locator('#gait-analyze-all').isEnabled(),true);
      }
      await context.close();
    }
  } finally {await browser.close();}
  console.log('Gait browser smoke passed: '+results.length+' viewport/flow combinations');
})().catch(error=>{console.error(error);process.exit(1);});
