// WebRTC Voice Calls
let callPeerConnection = null;
let localStream = null;
let callTargetUserId = null;
let callIsIncoming = false;
let callIsMuted = false;
let pendingIceCandidates = [];

const ICE_SERVERS = { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }, { urls: 'stun:stun1.l.google.com:19302' }] };

function showCallModal(name, avatarLetter, avatarColor, incoming) {
    const modal = document.getElementById('call-modal');
    modal.style.display = 'flex';
    document.getElementById('call-name').textContent = name;
    document.getElementById('call-status').textContent = incoming ? 'Входящий звонок...' : 'Вызов...';
    const av = document.getElementById('call-avatar');
    av.textContent = avatarLetter || name[0].toUpperCase();
    av.style.background = avatarColor || 'var(--primary)';
    document.getElementById('call-accept-btn').style.display = incoming ? 'flex' : 'none';
    document.getElementById('call-mute-btn').style.display = 'flex';
}

function hideCallModal() {
    document.getElementById('call-modal').style.display = 'none';
}

// ── Инициатор ─────────────────────────────────────────────────────────────────
async function startCall() {
    if (!currentChatUserId) return;
    callTargetUserId = currentChatUserId;
    callIsIncoming = false;

    try {
        localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
        showError('Нет доступа к микрофону');
        return;
    }

    callPeerConnection = new RTCPeerConnection(ICE_SERVERS);
    localStream.getTracks().forEach(t => callPeerConnection.addTrack(t, localStream));

    callPeerConnection.onicecandidate = e => {
        if (e.candidate) {
            socket.emit('call_ice', { to_user_id: callTargetUserId, candidate: e.candidate });
        }
    };

    callPeerConnection.ontrack = e => {
        document.getElementById('remote-audio').srcObject = e.streams[0];
    };

    const offer = await callPeerConnection.createOffer();
    await callPeerConnection.setLocalDescription(offer);

    const chatName = document.getElementById('chat-username').textContent;
    const chatAv = document.getElementById('chat-header-avatar');
    showCallModal(chatName, chatAv.textContent.trim(), chatAv.style.background, false);

    socket.emit('call_offer', { to_user_id: callTargetUserId, sdp: offer });
}

// ── Получатель принимает ──────────────────────────────────────────────────────
async function acceptCall() {
    callIsIncoming = false;
    document.getElementById('call-accept-btn').style.display = 'none';
    document.getElementById('call-status').textContent = 'Соединение...';

    try {
        localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
        showError('Нет доступа к микрофону');
        endCall();
        return;
    }

    localStream.getTracks().forEach(t => callPeerConnection.addTrack(t, localStream));

    callPeerConnection.onicecandidate = e => {
        if (e.candidate) {
            socket.emit('call_ice', { to_user_id: callTargetUserId, candidate: e.candidate });
        }
    };

    callPeerConnection.ontrack = e => {
        document.getElementById('remote-audio').srcObject = e.streams[0];
    };

    const answer = await callPeerConnection.createAnswer();
    await callPeerConnection.setLocalDescription(answer);
    socket.emit('call_answer', { to_user_id: callTargetUserId, sdp: answer });

    // Flush pending ICE candidates
    for (const c of pendingIceCandidates) {
        await callPeerConnection.addIceCandidate(new RTCIceCandidate(c));
    }
    pendingIceCandidates = [];
}

// ── Завершить звонок ──────────────────────────────────────────────────────────
function endCall() {
    if (callTargetUserId) {
        socket.emit('call_end', { to_user_id: callTargetUserId });
    }
    _cleanupCall();
}

function _cleanupCall() {
    if (callPeerConnection) { callPeerConnection.close(); callPeerConnection = null; }
    if (localStream) { localStream.getTracks().forEach(t => t.stop()); localStream = null; }
    document.getElementById('remote-audio').srcObject = null;
    callTargetUserId = null;
    callIsIncoming = false;
    callIsMuted = false;
    pendingIceCandidates = [];
    hideCallModal();
}

function toggleMute() {
    if (!localStream) return;
    callIsMuted = !callIsMuted;
    localStream.getAudioTracks().forEach(t => t.enabled = !callIsMuted);
    const icon = document.querySelector('#call-mute-btn i');
    icon.className = callIsMuted ? 'fas fa-microphone-slash' : 'fas fa-microphone';
    document.getElementById('call-mute-btn').style.background = callIsMuted ? '#e53e3e' : '#4a5568';
}

// ── Socket.IO события ─────────────────────────────────────────────────────────
function initCallSocketHandlers(sock) {
    // Входящий звонок
    sock.on('call_incoming', async data => {
        callTargetUserId = data.from_user_id;
        callIsIncoming = true;

        callPeerConnection = new RTCPeerConnection(ICE_SERVERS);
        await callPeerConnection.setRemoteDescription(new RTCSessionDescription(data.sdp));

        callPeerConnection.onicecandidate = e => {
            if (e.candidate) {
                sock.emit('call_ice', { to_user_id: callTargetUserId, candidate: e.candidate });
            }
        };
        callPeerConnection.ontrack = e => {
            document.getElementById('remote-audio').srcObject = e.streams[0];
        };
        callPeerConnection.onconnectionstatechange = () => {
            if (callPeerConnection && callPeerConnection.connectionState === 'connected') {
                document.getElementById('call-status').textContent = 'Идёт звонок';
            }
        };

        showCallModal(data.from_name, data.from_avatar_letter, data.from_avatar_color, true);
    });

    // Ответ на наш звонок
    sock.on('call_answered', async data => {
        if (!callPeerConnection) return;
        await callPeerConnection.setRemoteDescription(new RTCSessionDescription(data.sdp));
        document.getElementById('call-status').textContent = 'Идёт звонок';
        for (const c of pendingIceCandidates) {
            await callPeerConnection.addIceCandidate(new RTCIceCandidate(c));
        }
        pendingIceCandidates = [];
    });

    // ICE candidate от собеседника
    sock.on('call_ice', async data => {
        if (!callPeerConnection) return;
        if (callPeerConnection.remoteDescription) {
            await callPeerConnection.addIceCandidate(new RTCIceCandidate(data.candidate));
        } else {
            pendingIceCandidates.push(data.candidate);
        }
    });

    // Звонок завершён собеседником
    sock.on('call_ended', () => {
        _cleanupCall();
        showError('Звонок завершён', 'info');
    });

    // Звонок отклонён
    sock.on('call_rejected', () => {
        _cleanupCall();
        showError('Звонок отклонён', 'info');
    });
}
