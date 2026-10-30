/* Read-only, explicitly requested historical records with separated series. */
(() => {
    'use strict';
    const kindLabels = {questionnaire:'健康问卷',gait:'归档步态',dr:'DR 影像',rgb:'体表 RGB',clinical:'人工记录',checkin:'状态打卡'};
    const metrics = {LL:['LL','°'],SS:['SS','°'],SSA:['SSA','°'],LCA:['LCA','°'],HARRISON_A:['Harrison A','°'],LCI:['LCI','mm'],cobb_angle:['Cobb 角','°'],atr_angle:['ATR','°'],pain_score:['疼痛','/10'],activity_limit:['活动受限','/10'],negative:['阴性类模型分数','%'],neutral:['中间类模型分数','%'],positive:['阳性类模型分数','%']};
    const text = (tag, value, className) => {const el=document.createElement(tag);el.textContent=value;if(className)el.className=className;return el;};
    const valueText = (value) => Number(value).toLocaleString('zh-CN',{maximumFractionDigits:2});
    const dateText = (date) => typeof date==='string' && date ? date.slice(0,10) : '日期未记录';
    function groupRecords(records) {
        const groups=new Map();
        records.forEach(record=>{
            const quality=record.quality||{};
            const scores=record.kind==='gait';
            const values=scores ? record.model_scores||{} : record.measurements||{};
            Object.entries(values).forEach(([metric,raw])=>{
                if(!metrics[metric]||typeof raw!=='number'||!Number.isFinite(raw))return;
                if(scores && (quality.passed!==true||record.research_demo))return;
                const qualityKey=scores?JSON.stringify({passed:quality.passed,issues:[...(quality.issue_codes||[])].sort()}):'';
                const key=JSON.stringify([record.kind,metric,record.protocol_version||null,record.engine_version||null,record.model_version||null,record.checkpoint_sha256||null,qualityKey,record.association||'linked']);
                if(!groups.has(key))groups.set(key,{kind:record.kind,metric,unit:metrics[metric][1],protocol:record.protocol_version,engine:record.engine_version,model:record.model_version,checkpoint:record.checkpoint_sha256,quality:qualityKey,association:record.association,points:[]});
                groups.get(key).points.push({record,value:scores?raw*100:raw,timestamp:Date.parse(record.date||'')});
            });
        });
        return [...groups.values()].map(group=>({...group,points:group.points.sort((a,b)=>(Number.isFinite(a.timestamp)?a.timestamp:Infinity)-(Number.isFinite(b.timestamp)?b.timestamp:Infinity))}));
    }
    function canCompare(group) {
        if(group.association==='legacy_unassigned')return false;
        if(group.kind==='gait')return Boolean(group.engine&&(group.model||group.checkpoint));
        if(['dr','rgb'].includes(group.kind))return Boolean(group.protocol&&(group.engine||group.model||group.checkpoint));
        return Boolean(group.protocol);
    }
    function chart(group) {
        const points=group.points.filter(point=>Number.isFinite(point.timestamp));
        if(points.length<2||!canCompare(group))return null;
        const namespace='http://www.w3.org/2000/svg';
        const svg=document.createElementNS(namespace,'svg');svg.setAttribute('viewBox','0 0 520 160');svg.setAttribute('role','img');svg.classList.add('history-chart');
        const title=document.createElementNS(namespace,'title');title.textContent=`${kindLabels[group.kind]||group.kind} · ${metrics[group.metric][0]}按日期记录的数值`;svg.append(title);
        const low=Math.min(...points.map(point=>point.value)),high=Math.max(...points.map(point=>point.value));
        const pad=Math.max((high-low)*.2,.5),min=low-pad,max=high+pad;
        const first=points[0].timestamp,last=points.at(-1).timestamp;
        const x=point=>first===last?278:52+(point.timestamp-first)/(last-first)*446;
        const y=point=>124-(point.value-min)/(max-min)*100;
        [0,.5,1].forEach(ratio=>{const line=document.createElementNS(namespace,'line');const yy=24+ratio*100;line.setAttribute('x1','52');line.setAttribute('x2','498');line.setAttribute('y1',yy);line.setAttribute('y2',yy);line.setAttribute('class','history-grid');svg.append(line);const label=document.createElementNS(namespace,'text');label.setAttribute('x','43');label.setAttribute('y',yy+3);label.setAttribute('text-anchor','end');label.textContent=valueText(max-ratio*(max-min));svg.append(label);});
        const path=document.createElementNS(namespace,'polyline');path.setAttribute('points',points.map(point=>`${x(point)},${y(point)}`).join(' '));path.setAttribute('class','history-line');svg.append(path);
        points.forEach(point=>{const circle=document.createElementNS(namespace,'circle');circle.setAttribute('cx',x(point));circle.setAttribute('cy',y(point));circle.setAttribute('r','3.5');circle.setAttribute('class','history-point');const tip=document.createElementNS(namespace,'title');tip.textContent=`${dateText(point.record.date)} · ${valueText(point.value)}${group.unit}`;circle.append(tip);svg.append(circle);});
        [points[0],points.at(-1)].forEach((point,index)=>{const label=document.createElementNS(namespace,'text');label.setAttribute('x',index?'498':'52');label.setAttribute('y','148');label.setAttribute('text-anchor',index?'end':'start');label.textContent=dateText(point.record.date);svg.append(label);});
        return svg;
    }
    function recordLink(record) {
        const link=document.createElement('a');
        if(record.kind==='gait'&&record.record_id){link.href=`gait-workspace.html?job_id=${encodeURIComponent(record.record_id)}&case_id=${encodeURIComponent(record.case_id||'')}`;link.textContent='查看评估 ↗';}
        else {link.href=`/api/cases/${encodeURIComponent(record.case_id||'')}/report.pdf`;link.textContent='检查报告 ↗';link.target='_blank';link.rel='noopener';}
        return link;
    }
    function render(card) {
        const shell=document.createElement('section');shell.className='history-card';
        const records=Array.isArray(card.records)?card.records:[];
        const header=document.createElement('div');header.className='ms-row between';header.append(text('h3',card.title||'历次检查记录'),text('span',`${records.length} 条 · ${card.case_count||new Set(records.map(row=>row.case_id)).size} 次检查`,'ms-chip'));shell.append(header);
        if(card.notice)shell.append(text('p',card.notice,'history-notice'));
        const groups=groupRecords(records);
        groups.forEach((group,index)=>{
            const section=document.createElement('details');section.className='history-series';section.open=index<2;
            const summary=document.createElement('summary');summary.append(text('strong',`${kindLabels[group.kind]||group.kind} · ${metrics[group.metric][0]}`),text('span',`${group.points.length} 次记录 · ${group.unit}`));section.append(summary);
            const body=document.createElement('div');body.className='history-series-body';
            const version=[group.protocol,group.engine&&`引擎 ${group.engine}`,group.model].filter(Boolean).join(' · ');
            if(version)body.append(text('p',version,'history-meta'));
            const graph=chart(group);if(graph)body.append(graph);else if(group.points.length>=2)body.append(text('p','比较条件未完整记录，以下按次列示数值。','history-meta'));
            const table=document.createElement('table');table.className='history-table';
            const head=document.createElement('thead');const titles=document.createElement('tr');['检查日期','记录数值','来源'].forEach(label=>titles.append(text('th',label)));head.append(titles);table.append(head);
            const tbody=document.createElement('tbody');group.points.forEach(point=>{const row=document.createElement('tr');const date=text('td',dateText(point.record.date));if(point.record.association==='legacy_unassigned')date.append(text('small','旧未关联记录'));const value=text('td',`${valueText(point.value)} ${group.unit}`);const source=document.createElement('td');if(point.record.case_id)source.append(recordLink(point.record));else source.textContent='未关联检查';row.append(date,value,source);tbody.append(row);});table.append(tbody);body.append(table);section.append(body);shell.append(section);
        });
        records.filter(record=>record.kind==='gait'&&!Object.keys(record.model_scores||{}).length).forEach(record=>{const row=document.createElement('div');row.className='history-unscored';row.append(text('span',`${dateText(record.date)} · 步态：暂无可比较的有效分数`));if(record.case_id)row.append(recordLink(record));shell.append(row);});
        if(!records.length)shell.append(text('p','尚无可展示的历次记录。','history-notice'));
        if(card.truncated)shell.append(text('p','当前展示有数量上限的历史摘要，部分较早记录未列出。','history-notice'));
        if(Array.isArray(card.unavailable_sources)&&card.unavailable_sources.length)shell.append(text('p','部分来源暂未读取，可稍后重新查询。','history-notice'));
        return shell;
    }
    window.MindSpineHistoryCards=Object.freeze({render});
})();
