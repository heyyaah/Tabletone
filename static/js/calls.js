// WebRTC Voice + Video Calls
let callPeerConnection = null;
let localStream = null;
let callTargetUserId = null;
let callIsIncoming = false;
let callIsMuted = false;
let callIsVideoOff = false;
let callIsVideo = false;
let pendingIceCandidates = [];

const ICE_SERVERS = { iceServers: [
    { urls: 'stun:stun.l.google.com:19302' },
    { urls: 'stun:stun1.l.google.com:19302' }
]};

// ── UI helpers ────────────────────────────────────────────────────────────────
function showCallModal(name, avatarLetter, avatarColor, incoming, isVideo) {
    const modal = document.getElementById('call-modal');
    modal.style.display = 'flex';
    document.getElementById('call-name').textContent = name;
    document.getElementById('call-status').textContent = incoming
        ? (isVideo ? 'Входящий видеозвонок...' : 'Входящий звонок...')
        : (isVideo ? 'Видеовызов...' : 'Вызов...');

    const av = document.getElementById('call-avatar');
    av.textContent = avatarLetter || name[0].toUpperCase();
    av.style.background = avatarColor || 'var(--primary)';

    // Show/hide video elements
    const videoArea = document.getElementById('call-video-area');
    videoArea.style.display = isVideo ? 'flex' : 'none';
    av.style.display = isVideo ? 'none' : 'flex';

    document.getElementById('call-accept-btn').style.display = incoming ? 'flex' : 'none';
    document.getElementById('call-toggle-video-btn').style.display = isVideo ? 'flex' : 'none';

    // Сбрасываем UI кнопок управления в начальное состояние
    const muteBtn = document.getElementById('call-mute-btn');
    if (muteBtn) {
        muteBtn.querySelector('i').className = 'fas fa-microphone';
        muteBtn.style.background = '#4a5568';
    }
    const videoBtn = document.getElementById('call-toggle-video-btn');
    if (videoBtn) {
        videoBtn.querySelector('i').className = 'fas fa-video';
        videoBtn.style.background = '#4a5568';
    }
}

function hideCallModal() {
    document.getElementById('call-modal').style.display = 'none';
    const rv = document.getElementById('remote-video');
    const lv = document.getElementById('local-video');
    if (rv) rv.srcObject = null;
    if (lv) lv.srcObject = null;
    document.getElementById('remote-audio').srcObject = null;
}

function _setCallStatus(text) {
    document.getElementById('call-status').textContent = text;
}

// ── Start audio call ──────────────────────────────────────────────────────────
async function startCall() {
    if (!currentChatUserId) return;
    await _initiateCall(false);
}

// ── Start video call ──────────────────────────────────────────────────────────
async function startVideoCall() {
    if (!currentChatUserId) return;
    await _initiateCall(true);
}

async function _initiateCall(isVideo) {
    callTargetUserId = currentChatUserId;
    callIsIncoming = false;
    callIsVideo = isVideo;

    try {
        localStream = await navigator.mediaDevices.getUserMedia(
            isVideo ? { audio: true, video: { facingMode: 'user' } } : { audio: true }
        );
    } catch (e) {
        showError(isVideo ? 'Нет доступа к камере/микрофону' : 'Нет доступа к микрофону');
        return;
    }

    if (isVideo) {
        document.getElementById('local-video').srcObject = localStream;
    }

    callPeerConnection = _createPC();
    localStream.getTracks().forEach(t => callPeerConnection.addTrack(t, localStream));

    const offer = await callPeerConnection.createOffer();
    await callPeerConnection.setLocalDescription(offer);

    const chatName = document.getElementById('chat-username').textContent;
    const chatAv = document.getElementById('chat-header-avatar');
    showCallModal(chatName, chatAv.textContent.trim(), chatAv.style.background, false, isVideo);

    socket.emit('call_offer', { to_user_id: callTargetUserId, sdp: offer, is_video: isVideo });
}

// ── Accept incoming call ──────────────────────────────────────────────────────
async function acceptCall() {
    callIsIncoming = false;
    document.getElementById('call-accept-btn').style.display = 'none';
    _setCallStatus('Соединение...');

    try {
        localStream = await navigator.mediaDevices.getUserMedia(
            callIsVideo ? { audio: true, video: { facingMode: 'user' } } : { audio: true }
        );
    } catch (e) {
        showError(callIsVideo ? 'Нет доступа к камере/микрофону' : 'Нет доступа к микрофону');
        endCall();
        return;
    }

    if (callIsVideo) {
        document.getElementById('local-video').srcObject = localStream;
        document.getElementById('call-video-area').style.display = 'flex';
        document.getElementById('call-avatar').style.display = 'none';
        document.getElementById('call-toggle-video-btn').style.display = 'flex';
    }

    localStream.getTracks().forEach(t => callPeerConnection.addTrack(t, localStream));

    const answer = await callPeerConnection.createAnswer();
    await callPeerConnection.setLocalDescription(answer);
    socket.emit('call_answer', { to_user_id: callTargetUserId, sdp: answer });

    for (const c of pendingIceCandidates) {
        await callPeerConnection.addIceCandidate(new RTCIceCandidate(c));
    }
    pendingIceCandidates = [];
}

// ── End call ──────────────────────────────────────────────────────────────────
function endCall() {
    if (callTargetUserId) socket.emit('call_end', { to_user_id: callTargetUserId });
    _cleanupCall();
}

function _cleanupCall() {
    if (callPeerConnection) { callPeerConnection.close(); callPeerConnection = null; }
    if (localStream) { localStream.getTracks().forEach(t => t.stop()); localStream = null; }
    callTargetUserId = null;
    callIsIncoming = false;
    callIsMuted = false;
    callIsVideoOff = false;
    callIsVideo = false;
    pendingIceCandidates = [];
    hideCallModal();
}

// ── Controls ──────────────────────────────────────────────────────────────────
function toggleMute() {
    if (!localStream) return;
    callIsMuted = !callIsMuted;
    localStream.getAudioTracks().forEach(t => t.enabled = !callIsMuted);
    const btn = document.getElementById('call-mute-btn');
    btn.querySelector('i').className = callIsMuted ? 'fas fa-microphone-slash' : 'fas fa-microphone';
    btn.style.background = callIsMuted ? '#e53e3e' : '#4a5568';
}

function toggleVideo() {
    if (!localStream) return;
    callIsVideoOff = !callIsVideoOff;
    // Только отключаем трек, не останавливаем — остановка рвёт соединение
    localStream.getVideoTracks().forEach(t => { t.enabled = !callIsVideoOff; });
    const btn = document.getElementById('call-toggle-video-btn');
    btn.querySelector('i').className = callIsVideoOff ? 'fas fa-video-slash' : 'fas fa-video';
    btn.style.background = callIsVideoOff ? '#e53e3e' : '#4a5568';
    // Скрываем локальное видео, но не трогаем соединение
    const lv = document.getElementById('local-video');
    if (lv) lv.style.visibility = callIsVideoOff ? 'hidden' : 'visible';
}

// ── RTCPeerConnection factory ─────────────────────────────────────────────────
function _createPC() {
    const pc = new RTCPeerConnection(ICE_SERVERS);
    pc.onicecandidate = e => {
        if (e.candidate) socket.emit('call_ice', { to_user_id: callTargetUserId, candidate: e.candidate });
    };
    pc.ontrack = e => {
        const stream = e.streams[0];
        if (callIsVideo) {
            const rv = document.getElementById('remote-video');
            if (rv) rv.srcObject = stream;
        } else {
            document.getElementById('remote-audio').srcObject = stream;
        }
    };
    pc.onconnectionstatechange = () => {
        if (pc.connectionState === 'connected') _setCallStatus('Идёт звонок');
        // Только 'failed' завершает звонок — 'disconnected' может быть временным (смена сети, выкл. камеры)
        if (pc.connectionState === 'failed') _cleanupCall();
    };
    return pc;
}

// ── Socket.IO events ──────────────────────────────────────────────────────────
function initCallSocketHandlers(sock) {
    sock.on('call_incoming', async data => {
        callTargetUserId = data.from_user_id;
        callIsIncoming = true;
        callIsVideo = !!data.is_video;

        callPeerConnection = _createPC();
        await callPeerConnection.setRemoteDescription(new RTCSessionDescription(data.sdp));

        showCallModal(data.from_name, data.from_avatar_letter, data.from_avatar_color, true, callIsVideo);
    });

    sock.on('call_answered', async data => {
        if (!callPeerConnection) return;
        await callPeerConnection.setRemoteDescription(new RTCSessionDescription(data.sdp));
        _setCallStatus('Идёт звонок');
        for (const c of pendingIceCandidates) {
            await callPeerConnection.addIceCandidate(new RTCIceCandidate(c));
        }
        pendingIceCandidates = [];
    });

    sock.on('call_ice', async data => {
        if (!callPeerConnection) return;
        if (callPeerConnection.remoteDescription) {
            await callPeerConnection.addIceCandidate(new RTCIceCandidate(data.candidate));
        } else {
            pendingIceCandidates.push(data.candidate);
        }
    });

    sock.on('call_ended', () => { _cleanupCall(); showError('Звонок завершён', 'info'); });
    sock.on('call_rejected', () => { _cleanupCall(); showError('Звонок отклонён', 'info'); });
}
