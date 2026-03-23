// WebRTC Voice + Video Calls
let callPeerConnection = null;
let localStream = null;
let screenStream = null;
let isScreenSharing = false;
let callTargetUserId = null;
let callIsIncoming = false;
let callIsMuted = false;
let callIsVideoOff = false;
let callIsVideo = false;
let pendingIceCandidates = [];
let localIceBuffer = [];
let remoteDescSet = false;

const ICE_SERVERS = {
    iceServers: [
        { urls: 'stun:stun.l.google.com:19302' },
        { urls: 'stun:stun1.l.google.com:19302' },
        { urls: 'stun:stun2.l.google.com:19302' },
        { urls: 'stun:stun.relay.metered.ca:80' },
        { urls: 'turn:global.relay.metered.ca:80',               username: 'openrelayproject', credential: 'openrelayproject' },
        { urls: 'turn:global.relay.metered.ca:443',              username: 'openrelayproject', credential: 'openrelayproject' },
        { urls: 'turn:global.relay.metered.ca:443?transport=tcp', username: 'openrelayproject', credential: 'openrelayproject' }
    ],
    iceCandidatePoolSize: 10
};

// ── PC factory ────────────────────────────────────────────────────────────────

function _createPC() {
    const pc = new RTCPeerConnection(ICE_SERVERS);

    pc.onicecandidate = ({ candidate }) => {
        if (!candidate) return;
        if (remoteDescSet) {
            socket.emit('call_ice', { to_user_id: callTargetUserId, candidate: candidate.toJSON() });
        } else {
            localIceBuffer.push(candidate.toJSON());
        }
    };

    pc.onconnectionstatechange = () => {
        const s = pc.connectionState;
        if (s === 'connected')                              _setCallStatus('Podklyucheno');
        if (['disconnected', 'failed', 'closed'].includes(s)) _cleanupCall();
    };

    pc.ontrack = ({ streams, track }) => {
        if (track.kind === 'audio') {
            document.getElementById('remote-audio').srcObject = streams[0];
        } else if (track.kind === 'video') {
            const rv = document.getElementById('remote-video');
            if (rv) rv.srcObject = streams[0];
        }
    };

    return pc;
}

// ── UI helpers ────────────────────────────────────────────────────────────────

function showCallModal(name, avatarLetter, avatarColor, incoming, isVideo) {
    const modal = document.getElementById('call-modal');
    modal.style.display = 'flex';
    document.getElementById('call-name').textContent = name;
    document.getElementById('call-status').textContent = incoming
        ? (isVideo ? 'Vkhodyashchiy videovyzov...' : 'Vkhodyashchiy zvonok...')
        : (isVideo ? 'Videovyzov...' : 'Vyzov...');
    const av = document.getElementById('call-avatar');
    av.textContent = avatarLetter || name[0].toUpperCase();
    av.style.background = avatarColor || 'var(--primary)';
    document.getElementById('call-video-area').style.display = isVideo ? 'flex' : 'none';
    av.style.display = isVideo ? 'none' : 'flex';
    const incomingBtns = document.getElementById('call-incoming-btns');
    const activeBtns   = document.getElementById('call-active-btns');
    if (incomingBtns) incomingBtns.style.display = incoming ? 'flex' : 'none';
    if (activeBtns)   activeBtns.style.display   = incoming ? 'none' : 'flex';
    const videoBtnWrap = document.getElementById('call-video-btn-wrap');
    if (videoBtnWrap) videoBtnWrap.style.display = isVideo ? 'flex' : 'none';
    const muteBtn = document.getElementById('call-mute-btn');
    if (muteBtn) { muteBtn.querySelector('i').className = 'fas fa-microphone'; muteBtn.style.background = '#4a5568'; }
    const videoBtn = document.getElementById('call-toggle-video-btn');
    if (videoBtn) { videoBtn.querySelector('i').className = 'fas fa-video'; videoBtn.style.background = '#4a5568'; }
}

function hideCallModal() {
    document.getElementById('call-modal').style.display = 'none';
    const rv = document.getElementById('remote-video');
    const lv = document.getElementById('local-video');
    if (rv) rv.srcObject = null;
    if (lv) lv.srcObject = null;
    document.getElementById('remote-audio').srcObject = null;
}

function _setCallStatus(text) { document.getElementById('call-status').textContent = text; }

function showCallTypeModal() {
    if (!currentChatUserId) return;
    const chatItem = document.querySelector('.chat-item[data-user-id="' + currentChatUserId + '"]');
    if (chatItem && chatItem.dataset.isBot === 'true') { showError('Nelzya pozvonit botu'); return; }
    document.getElementById('call-type-modal').style.display = 'flex';
}

// ── Outgoing call ─────────────────────────────────────────────────────────────

async function startCall()      { if (!currentChatUserId) return; await _initiateCall(false); }
async function startVideoCall() { if (!currentChatUserId) return; await _initiateCall(true); }

async function _initiateCall(isVideo) {
    callTargetUserId = currentChatUserId;
    callIsIncoming   = false;
    callIsVideo      = isVideo;
    remoteDescSet    = false;
    localIceBuffer   = [];
    try {
        localStream = await navigator.mediaDevices.getUserMedia(
            isVideo ? { audio: true, video: { facingMode: 'user' } } : { audio: true }
        );
    } catch (e) { showError(isVideo ? 'Net dostupa k kamere/mikrofonu' : 'Net dostupa k mikrofonu'); return; }
    if (isVideo) document.getElementById('local-video').srcObject = localStream;
    callPeerConnection = _createPC();
    localStream.getTracks().forEach(t => callPeerConnection.addTrack(t, localStream));
    const offer = await callPeerConnection.createOffer();
    await callPeerConnection.setLocalDescription(offer);
    const chatName = document.getElementById('chat-username').textContent;
    const chatAv   = document.getElementById('chat-header-avatar');
    showCallModal(chatName, chatAv ? chatAv.textContent.trim() : '', chatAv ? chatAv.style.background : '', false, isVideo);
    socket.emit('call_offer', { to_user_id: callTargetUserId, sdp: offer, is_video: isVideo });
}

// ── Incoming call ─────────────────────────────────────────────────────────────

async function acceptCall() {
    callIsIncoming = false;
    remoteDescSet  = false;
    localIceBuffer = [];
    _setCallStatus('Soedinenie...');
    const incomingBtns = document.getElementById('call-incoming-btns');
    const activeBtns   = document.getElementById('call-active-btns');
    if (incomingBtns) incomingBtns.style.display = 'none';
    if (activeBtns)   activeBtns.style.display   = 'flex';
    try {
        localStream = await navigator.mediaDevices.getUserMedia(
            callIsVideo ? { audio: true, video: { facingMode: 'user' } } : { audio: true }
        );
    } catch (e) { showError(callIsVideo ? 'Net dostupa k kamere/mikrofonu' : 'Net dostupa k mikrofonu'); endCall(); return; }
    if (callIsVideo) {
        document.getElementById('local-video').srcObject = localStream;
        document.getElementById('call-video-area').style.display = 'flex';
        document.getElementById('call-avatar').style.display = 'none';
        const vw = document.getElementById('call-video-btn-wrap');
        if (vw) vw.style.display = 'flex';
    }
    localStream.getTracks().forEach(t => callPeerConnection.addTrack(t, localStream));
    const answer = await callPeerConnection.createAnswer();
    await callPeerConnection.setLocalDescription(answer);
    socket.emit('call_answer', { to_user_id: callTargetUserId, sdp: answer });
    for (const c of pendingIceCandidates) {
        try { await callPeerConnection.addIceCandidate(new RTCIceCandidate(c)); } catch (_) {}
    }
    pendingIceCandidates = [];
}

function endCall() {
    if (callTargetUserId) socket.emit('call_end', { to_user_id: callTargetUserId });
    _cleanupCall();
}

function rejectCall() {
    if (callTargetUserId) socket.emit('call_reject', { to_user_id: callTargetUserId });
    _cleanupCall();
}

function _cleanupCall() {
    if (callPeerConnection) { callPeerConnection.close(); callPeerConnection = null; }
    if (localStream)  { localStream.getTracks().forEach(t => t.stop());  localStream  = null; }
    if (screenStream) { screenStream.getTracks().forEach(t => t.stop()); screenStream = null; }
    isScreenSharing  = false;
    remoteDescSet    = false;
    localIceBuffer   = [];
    callTargetUserId = null; callIsIncoming = false; callIsMuted = false;
    callIsVideoOff   = false; callIsVideo = false; pendingIceCandidates = [];
    hideCallModal();
}

// ── In-call controls ──────────────────────────────────────────────────────────

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
    localStream.getVideoTracks().forEach(t => { t.enabled = !callIsVideoOff; });
    const btn = document.getElementById('call-toggle-video-btn');
    btn.querySelector('i').className = callIsVideoOff ? 'fas fa-video-slash' : 'fas fa-video';
    btn.style.background = callIsVideoOff ? '#e53e3e' : '#4a5568';
    const lv = document.getElementById('local-video');
    if (lv) lv.style.visibility = callIsVideoOff ? 'hidden' : 'visible';
}

async function toggleScreenShare() {
    if (!callPeerConnection || !localStream) return;
    const btn = document.getElementById('call-screen-btn');
    if (!isScreenSharing) {
        try {
            screenStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
            const screenTrack = screenStream.getVideoTracks()[0];
            const sender = callPeerConnection.getSenders().find(s => s.track && s.track.kind === 'video');
            if (sender) await sender.replaceTrack(screenTrack);
            else callPeerConnection.addTrack(screenTrack, localStream);
            const lv = document.getElementById('local-video');
            if (lv) { lv.srcObject = screenStream; lv.style.visibility = 'visible'; }
            isScreenSharing = true;
            if (btn) { btn.querySelector('i').className = 'fas fa-desktop'; btn.style.background = '#667eea'; }
            screenTrack.onended = () => { if (isScreenSharing) toggleScreenShare(); };
        } catch (e) { if (e.name !== 'NotAllowedError') console.error('Screen share error:', e); }
    } else {
        const camTrack = localStream.getVideoTracks()[0];
        const sender = callPeerConnection.getSenders().find(s => s.track && s.track.kind === 'video');
        if (sender && camTrack) await sender.replaceTrack(camTrack);
        if (screenStream) { screenStream.getTracks().forEach(t => t.stop()); screenStream = null; }
        const lv = document.getElementById('local-video');
        if (lv) lv.srcObject = localStream;
        isScreenSharing = false;
        if (btn) { btn.querySelector('i').className = 'fas fa-desktop'; btn.style.background = '#4a5568'; }
    }
}

// ── Socket events ─────────────────────────────────────────────────────────────

function initCallSocketHandlers(socket) {

socket.on('call_offer', async ({ from_user_id, from_username, sdp, is_video, avatar_letter, avatar_color }) => {
    if (callPeerConnection) { socket.emit('call_reject', { to_user_id: from_user_id }); return; }
    callTargetUserId = from_user_id;
    callIsIncoming   = true;
    callIsVideo      = is_video;
    remoteDescSet    = false;
    localIceBuffer   = [];
    pendingIceCandidates = [];
    callPeerConnection = _createPC();
    await callPeerConnection.setRemoteDescription(new RTCSessionDescription(sdp));
    remoteDescSet = true;
    showCallModal(from_username, avatar_letter, avatar_color, true, is_video);
});

socket.on('call_answer', async ({ sdp }) => {
    if (!callPeerConnection) return;
    await callPeerConnection.setRemoteDescription(new RTCSessionDescription(sdp));
    remoteDescSet = true;
    _setCallStatus('Podklyucheno...');
    for (const c of localIceBuffer) {
        socket.emit('call_ice', { to_user_id: callTargetUserId, candidate: c });
    }
    localIceBuffer = [];
});

socket.on('call_ice', async ({ candidate }) => {
    if (!callPeerConnection) return;
    if (remoteDescSet) {
        try { await callPeerConnection.addIceCandidate(new RTCIceCandidate(candidate)); } catch (_) {}
    } else {
        pendingIceCandidates.push(candidate);
    }
});

socket.on('call_end',    () => { _cleanupCall(); });
socket.on('call_reject', () => { _setCallStatus('Otkloneno'); setTimeout(_cleanupCall, 1500); });

}
