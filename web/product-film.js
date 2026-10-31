(() => {
    const section = document.getElementById('product-film');
    if (!section) return;
    const video = section.querySelector('[data-product-video]');
    const stage = section.querySelector('[data-film-stage]');
    const button = section.querySelector('[data-film-play]');
    const label = section.querySelector('[data-film-play-label]');
    const status = section.querySelector('[data-film-status]');
    if (!video || !stage || !button || !label || !status || typeof video.play !== 'function') return;

    const setState = (state) => {
        stage.dataset.state = state;
        window.dispatchEvent(new CustomEvent('product-film:statechange', { detail: { state } }));
    };
    const resetButton = (text) => {
        label.textContent = text;
        button.disabled = false;
        button.removeAttribute('aria-busy');
        button.hidden = false;
    };
    const showError = () => {
        setState('error');
        video.controls = true;
        resetButton('重新尝试播放');
        status.textContent = '暂时无法播放，请重试，或使用“在新窗口播放”打开影片。';
    };

    // Native controls remain available when JavaScript is disabled. Enhancement
    // does not load media until the visitor explicitly starts playback.
    video.controls = false;
    button.hidden = false;
    button.addEventListener('click', async () => {
        if (button.disabled) return;
        const previousState = stage.dataset.state;
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        label.textContent = '正在加载';
        status.textContent = '正在加载影片…';
        video.controls = true;
        if (previousState === 'error') video.load();
        if (previousState === 'ended' || video.ended) video.currentTime = 0;
        setState('loading');
        try {
            await video.play();
        } catch {
            showError();
        }
    });
    video.addEventListener('playing', () => {
        const moveFocus = document.activeElement === button;
        setState('playing');
        button.hidden = true;
        button.disabled = false;
        button.removeAttribute('aria-busy');
        status.textContent = '';
        if (moveFocus) video.focus({ preventScroll: true });
        video.dispatchEvent(new CustomEvent('filmplaying', { bubbles: true }));
    });
    video.addEventListener('pause', () => {
        if (!video.ended && stage.dataset.state !== 'error') setState('paused');
    });
    video.addEventListener('ended', () => {
        setState('ended');
        status.textContent = '影片已播放完毕。';
        resetButton('重新播放');
    });
    video.addEventListener('waiting', () => {
        if (!video.paused) {
            setState('loading');
            status.textContent = '正在缓冲影片…';
        }
    });
    video.addEventListener('error', showError);
    video.querySelectorAll('source').forEach(source => source.addEventListener('error', showError));
})();
