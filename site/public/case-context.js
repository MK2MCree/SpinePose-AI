/* Keep an explicitly selected examination across the existing native pages. */
(() => {
    'use strict';
    const pages = new Set(['health-home.html', 'personal-workspace.html', 'personal-case.html', 'health-agent.html', 'gait-workspace.html', 'workspace.html']);
    const current = () => new URL(window.location.href);
    const isDemo = () => { const q = current().searchParams; return q.get('demo') === '1' || q.get('mode') === 'demo'; };
    function decorate(root = document) {
        const source = current();
        const caseId = source.searchParams.get('case_id');
        const nodes = root.matches?.('a[href]') ? [root] : root.querySelectorAll?.('a[href]') || [];
        nodes.forEach((link) => {
            let target;
            try { target = new URL(link.getAttribute('href'), source); } catch { return; }
            const page = target.pathname.split('/').pop();
            if (target.origin !== source.origin || !pages.has(page)) return;
            const explicitDemo = target.searchParams.get('demo') === '1' || target.searchParams.get('mode') === 'demo';
            const explicitReal = target.searchParams.get('mode') === 'real';
            if (explicitDemo && !isDemo()) return;
            if (isDemo() && !explicitReal && page !== 'gait-workspace.html' && page !== 'workspace.html') {
                target.searchParams.set('demo', '1'); target.searchParams.delete('mode'); target.searchParams.delete('case_id');
            } else {
                target.searchParams.set('mode', 'real'); target.searchParams.delete('demo');
                if (caseId && (!target.searchParams.has('case_id') || link.dataset.msCaseInherited === 'true')) {
                    target.searchParams.set('case_id', caseId);
                    link.dataset.msCaseInherited = 'true';
                }
            }
            const href = target.pathname + target.search + target.hash;
            if (link.getAttribute('href') !== href) link.setAttribute('href', href);
        });
    }
    function setCaseId(value) {
        if (!value || isDemo()) return;
        const url = current(); url.searchParams.set('case_id', String(value));
        window.history.replaceState({}, '', url.pathname + url.search + url.hash);
        decorate();
    }
    window.MindSpineCaseContext = { id: () => current().searchParams.get('case_id'), decorate, setCaseId };
    document.addEventListener('DOMContentLoaded', () => {
        decorate();
        new MutationObserver((records) => records.forEach((record) => {
            if (record.type === 'attributes') decorate(record.target);
            else record.addedNodes.forEach((node) => { if (node.nodeType === 1) decorate(node); });
        })).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['href'] });
    });
})();
