content = open('static/js/app.js', encoding='utf-8').read()

start = content.find('// Инициализация видео кружочков\ndocument.addEventListener')
end = content.find('\n// Открыть рекордер видео', start)

NEW = '''// Инициализация видео кружочков
document.addEventListener('DOMContentLoaded', function() {

    // Пустое поле: кружок (video) или гс (voice), ПКМ переключает между ними
    // Есть текст — только кнопка отправки; зажатие — запись

    const _sendBtn = document.getElementById('send-btn');
    const _videoBtnCycle = document.getElementById('video-circle-btn');
    const _voiceBtn = document.getElementById('voice-btn');
    const _msgInput = document.getElementById('message-input');

    let _mediaMode = 'video'; // 'video' | 'voice'
    let _recording = false;
    let _locked = false;
    let _pressTimer = null;
    let _pressStartY = 0;
    let _pressStartX = 0;

    function _updateBtns() {
        const hasText = _msgInput && _msgInput.value.trim();
        if (hasText) {
            if (_sendBtn) _sendBtn.style.display = '';
            if (_videoBtnCycle) _videoBtnCycle.style.display = 'none';
            if (_voiceBtn) _voiceBtn.style.display = 'none';
        } else {
            if (_sendBtn) _sendBtn.style.display = 'none';
            if (_videoBtnCycle) _videoBtnCycle.style.display = _mediaMode === 'video' ? '' : 'none';
            if (_voiceBtn) _voiceBtn.style.display = _mediaMode === 'voice' ? '' : 'none';
        }
    }

    [_videoBtnCycle, _voiceBtn].forEach(btn => {
        if (!btn) return;
        btn.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            _mediaMode = _mediaMode === 'video' ? 'voice' : 'video';
            _updateBtns();
        });
    });

    if (_msgInput) _msgInput.addEventListener('input', _updateBtns);

    function _cancelRecording() {
        if (!_recording) return;
        _recording = false; _locked = false;
        cancelVoiceRecord();
        _hideLockHint();
        _updateBtns();
    }

    function _finishRecording() {
        if (!_recording) return;
        _recording = false; _locked = false;
        if (_mediaMode === 'voice') stopVoiceRecord();
        else stopVideoCircleRecord();
        _hideLockHint();
        _updateBtns();
    }

    function _showLockHint() {
        let hint = document.getElementById('_rec-hint');
        if (!hint) {
            hint = document.createElement('div');
            hint.id = '_rec-hint';
            hint.style.cssText = 'position:absolute;right:56px;bottom:8px;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:20px;padding:6px 12px;font-size:12px;color:var(--text-secondary);display:flex;align-items:center;gap:8px;pointer-events:none;z-index:10;white-space:nowrap;';
            hint.innerHTML = '<span>\\u2190 \\u041e\\u0442\\u043c\\u0435\\u043d\\u0430</span><span style="margin:0 4px;">|</span><span>\\u2191 \\u0417\\u0430\\u043a\\u0440\\u0435\\u043f\\u0438\\u0442\\u044c</span>';
            const form = document.getElementById('message-form');
            if (form) { form.style.position = 'relative'; form.appendChild(hint); }
        }
        hint.style.display = 'flex';
    }

    function _hideLockHint() {
        const hint = document.getElementById('_rec-hint');
        if (hint) hint.style.display = 'none';
        const lockSend = document.getElementById('_lock-send-btn');
        if (lockSend) lockSend.remove();
    }

    function _showLockedUI() {
        _hideLockHint(); _locked = true;
        let lockSend = document.getElementById('_lock-send-btn');
        if (!lockSend) {
            lockSend = document.createElement('button');
            lockSend.id = '_lock-send-btn'; lockSend.type = 'button';
            lockSend.className = 'send-btn';
            lockSend.style.cssText = 'background:#38a169;';
            lockSend.innerHTML = '<i class="fas fa-paper-plane"></i>';
            lockSend.addEventListener('click', _finishRecording);
            lockSend.addEventListener('touchend', _finishRecording);
            const form = document.getElementById('message-form');
            if (form) form.appendChild(lockSend);
        }
        const activeBtn = _mediaMode === 'video' ? _videoBtnCycle : _voiceBtn;
        if (activeBtn) activeBtn.style.display = 'none';
    }

    function _onPressStart(e) {
        if (_locked) return;
        const touch = e.touches ? e.touches[0] : e;
        _pressStartY = touch.clientY; _pressStartX = touch.clientX;
        _pressTimer = setTimeout(() => {
            _pressTimer = null; _recording = true;
            _showLockHint();
            if (_mediaMode === 'voice') startVoiceRecord();
            else openVideoRecorder();
        }, 300);
    }

    function _onPressMove(e) {
        if (!_recording || _locked) return;
        const touch = e.touches ? e.touches[0] : e;
        const dx = touch.clientX - _pressStartX;
        const dy = touch.clientY - _pressStartY;
        if (dx < -60) _cancelRecording();
        else if (dy < -60) _showLockedUI();
    }

    function _onPressEnd(e) {
        if (_pressTimer) { clearTimeout(_pressTimer); _pressTimer = null; return; }
        if (_locked || !_recording) return;
        _finishRecording();
    }

    [_videoBtnCycle, _voiceBtn].forEach(btn => {
        if (!btn) return;
        btn.addEventListener('mousedown', _onPressStart);
        btn.addEventListener('mousemove', _onPressMove);
        btn.addEventListener('mouseup', _onPressEnd);
        btn.addEventListener('mouseleave', (e) => {
            if (_recording && !_locked) {
                if (e.clientX - _pressStartX < -40) _cancelRecording();
                else _finishRecording();
            }
        });
        btn.addEventListener('touchstart', _onPressStart, {passive: true});
        btn.addEventListener('touchmove', _onPressMove, {passive: true});
        btn.addEventListener('touchend', _onPressEnd);
    });

    _updateBtns();
});'''

result = content[:start] + NEW + content[end:]
open('static/js/app.js', 'w', encoding='utf-8').write(result)
print(f'Done. Replaced {end - start} chars with {len(NEW)} chars')
