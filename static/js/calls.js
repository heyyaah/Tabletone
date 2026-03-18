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
        { urls: "stun:stun.l.google.com:19302" },
        { urls: "stun:stun1.l.google.com:19302" },
        { urls: "stun:stun.relay.metered.ca:80" },
        { urls: "turn:global.relay.metered.ca:80",               username: "openrelayproject", credential: "openrelayproject" },
        { urls: "turn:global.relay.metered.ca:443",              username: "openrelayproject", credential: "openrelayproject" },
        { urls: "turn:global.relay.metered.ca:443?transport=tcp", username: "openrelayproject", credential: "openrelayproject" }
    ],
    iceCandidatePoolSize: 10
};

function _createPC() {
    const pc = new RTCPeerConnection(ICE_SERVERS);
    pc.onicecandidate = function(e) {
        if (!e.candidate) return;
        if (remoteDescSet) {
            socket.emit("call_ice", { to_user_id: callTargetUserId, candidate: e.candidate.toJSON() });
        } else {
            localIceBuffer.push(e.candidate.toJSON());
        }
    };
    pc.onconnectionstatechange = function() {
        var s = pc.connectionState;
        if (s === "connected") _setCallStatus("Podklyucheno");
        if (s === "disconnected" || s === "failed" || s === "closed") _cleanupCall();
    };
    pc.ontrack = function(e) {
        if (e.track.kind === "audio") {
            document.getElementById("remote-audio").srcObject = e.streams[0];
        } else if (e.track.kind === "video") {
            var rv = document.getElementById("remote-video");
            if (rv) rv.srcObject = e.streams[0];
        }
    };
    return pc;
}
function showCallModal(name, avatarLetter, avatarColor, incoming, isVideo) {
    var modal = document.getElementById("call-modal");
    modal.style.display = "flex";
    document.getElementById("call-name").textContent = name;
    document.getElementById("call-status").textContent = incoming
        ? (isVideo ? "Vkhodyashchiy videovyzov..." : "Vkhodyashchiy zvonok...")
        : (isVideo ? "Videovyzov..." : "Vyzov...");
    var av = document.getElementById("call-avatar");
    av.textContent = avatarLetter || name[0].toUpperCase();
    av.style.background = avatarColor || "var(--primary)";
    document.getElementById("call-video-area").style.display = isVideo ? "flex" : "none";
    av.style.display = isVideo ? "none" : "flex";
    var incomingBtns = document.getElementById("call-incoming-btns");
    var activeBtns   = document.getElementById("call-active-btns");
    if (incomingBtns) incomingBtns.style.display = incoming ? "flex" : "none";
    if (activeBtns)   activeBtns.style.display   = incoming ? "none" : "flex";
    var videoBtnWrap = document.getElementById("call-video-btn-wrap");
    if (videoBtnWrap) videoBtnWrap.style.display = isVideo ? "flex" : "none";
    var muteBtn = document.getElementById("call-mute-btn");
    if (muteBtn) { muteBtn.querySelector("i").className = "fas fa-microphone"; muteBtn.style.background = "#4a5568"; }
    var videoBtn = document.getElementById("call-toggle-video-btn");
    if (videoBtn) { videoBtn.querySelector("i").className = "fas fa-video"; videoBtn.style.background = "#4a5568"; }
}

function hideCallModal() {
    document.getElementById("call-modal").style.display = "none";
    var rv = document.getElementById("remote-video");
    var lv = document.getElementById("local-video");
    if (rv) rv.srcObject = null;
    if (lv) lv.srcObject = null;
    document.getElementById("remote-audio").srcObject = null;
}

function _setCallStatus(text) { document.getElementById("call-status").textContent = text; }

function showCallTypeModal() {
    if (!currentChatUserId) return;
    var chatItem = document.querySelector(".chat-item[data-user-id=\"" + currentChatUserId + "\"]");
    if (chatItem && chatItem.dataset.isBot === "true") { showError("Nelzya pozvonit botu"); return; }
    document.getElementById("call-type-modal").style.display = "flex";
}

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
            isVideo ? { audio: true, video: { facingMode: "user" } } : { audio: true }
        );
    } catch (e) { showError(isVideo ? "Net dostupa k kamere/mikrofonu" : "Net dostupa k mikrofonu"); return; }
    if (isVideo) document.getElementById("local-video").srcObject = localStream;
    callPeerConnection = _createPC();
    localStream.getTracks().forEach(function(t) { callPeerConnection.addTrack(t, localStream); });
    var offer = await callPeerConnection.createOffer();
    await callPeerConnection.setLocalDescription(offer);
    var chatName = document.getElementById("chat-username").textContent;
    var chatAv   = document.getElementById("chat-header-avatar");
    showCallModal(chatName, chatAv ? chatAv.textContent.trim() : "", chatAv ? chatAv.style.background : "", false, isVideo);
    socket.emit("call_offer", { to_user_id: callTargetUserId, sdp: offer, is_video: isVideo });
}
async function acceptCall() {
    callIsIncoming = false;
    remoteDescSet  = false;
    localIceBuffer = [];
    _setCallStatus("Soedinenie...");
    var incomingBtns = document.getElementById("call-incoming-btns");
    var activeBtns   = document.getElementById("call-active-btns");
    if (incomingBtns) incomingBtns.style.display = "none";
    if (activeBtns)   activeBtns.style.display   = "flex";
    try {
        localStream = await navigator.mediaDevices.getUserMedia(
            callIsVideo ? { audio: true, video: { facingMode: "user" } } : { audio: true }
        );
    } catch (e) { showError(callIsVideo ? "Net dostupa k kamere/mikrofonu" : "Net dostupa k mikrofonu"); endCall(); return; }
    if (callIsVideo) {
        document.getElementById("local-video").srcObject = localStream;
        document.getElementById("call-video-area").style.display = "flex";
        document.getElementById("call-avatar").style.display = "none";
        var vw = document.getElementById("call-video-btn-wrap");
        if (vw) vw.style.display = "flex";
    }
    localStream.getTracks().forEach(function(t) { callPeerConnection.addTrack(t, localStream); });
    var answer = await callPeerConnection.createAnswer();
    await callPeerConnection.setLocalDescription(answer);
    socket.emit("call_answer", { to_user_id: callTargetUserId, sdp: answer });
    for (var i = 0; i < pendingIceCandidates.length; i++) {
        try { await callPeerConnection.addIceCandidate(new RTCIceCandidate(pendingIceCandidates[i])); } catch (_) {}
    }
    pendingIceCandidates = [];
}

function endCall() {
    if (callTargetUserId) socket.emit("call_end", { to_user_id: callTargetUserId });
    _cleanupCall();
}

function rejectCall() {
    if (callTargetUserId) socket.emit("call_reject", { to_user_id: callTargetUserId });
    _cleanupCall();
}

function _cleanupCall() {
    if (callPeerConnection) { callPeerConnection.close(); callPeerConnection = null; }
    if (localStream)  { localStream.getTracks().forEach(function(t) { t.stop(); });  localStream  = null; }
    if (screenStream) { screenStream.getTracks().forEach(function(t) { t.stop(); }); screenStream = null; }
    isScreenSharing  = false;
    remoteDescSet    = false;
    localIceBuffer   = [];
    callTargetUserId = null; callIsIncoming = false; callIsMuted = false;
    callIsVideoOff   = false; callIsVideo = false; pendingIceCandidates = [];
    hideCallModal();
}

function toggleMute() {
    if (!localStream) return;
    callIsMuted = !callIsMuted;
    localStream.getAudioTracks().forEach(function(t) { t.enabled = !callIsMuted; });
    var btn = document.getElementById("call-mute-btn");
    btn.querySelector("i").className = callIsMuted ? "fas fa-microphone-slash" : "fas fa-microphone";
    btn.style.background = callIsMuted ? "#e53e3e" : "#4a5568";
}

function toggleVideo() {
    if (!localStream) return;
    callIsVideoOff = !callIsVideoOff;
    localStream.getVideoTracks().forEach(function(t) { t.enabled = !callIsVideoOff; });
    var btn = document.getElementById("call-toggle-video-btn");
    btn.querySelector("i").className = callIsVideoOff ? "fas fa-video-slash" : "fas fa-video";
    btn.style.background = callIsVideoOff ? "#e53e3e" : "#4a5568";
    var lv = document.getElementById("local-video");
    if (lv) lv.style.visibility = callIsVideoOff ? "hidden" : "visible";
}

async function toggleScreenShare() {
    if (!callPeerConnection || !localStream) return;
    var btn = document.getElementById("call-screen-btn");
    if (!isScreenSharing) {
        try {
            screenStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
            var screenTrack = screenStream.getVideoTracks()[0];
            var sender = callPeerConnection.getSenders().find(function(s) { return s.track && s.track.kind === "video"; });
            if (sender) await sender.replaceTrack(screenTrack);
            else callPeerConnection.addTrack(screenTrack, localStream);
            var lv = document.getElementById("local-video");
            if (lv) { lv.srcObject = screenStream; lv.style.visibility = "visible"; }
            isScreenSharing = true;
            if (btn) { btn.querySelector("i").className = "fas fa-desktop"; btn.style.background = "#667eea"; }
            screenTrack.onended = function() { if (isScreenSharing) toggleScreenShare(); };
        } catch (e) { if (e.name !== "NotAllowedError") console.error("Screen share error:", e); }
    } else {
        var camTrack = localStream.getVideoTracks()[0];
        var sender2 = callPeerConnection.getSenders().find(function(s) { return s.track && s.track.kind === "video"; });
        if (sender2 && camTrack) await sender2.replaceTrack(camTrack);
        if (screenStream) { screenStream.getTracks().forEach(function(t) { t.stop(); }); screenStream = null; }
        var lv2 = document.getElementById("local-video");
        if (lv2) lv2.srcObject = localStream;
        isScreenSharing = false;
        if (btn) { btn.querySelector("i").className = "fas fa-desktop"; btn.style.background = "#4a5568"; }
    }
}

socket.on("call_offer", async function(data) {
    if (callPeerConnection) { socket.emit("call_reject", { to_user_id: data.from_user_id }); return; }
    callTargetUserId = data.from_user_id;
    callIsIncoming   = true;
    callIsVideo      = data.is_video;
    remoteDescSet    = false;
    localIceBuffer   = [];
    pendingIceCandidates = [];
    callPeerConnection = _createPC();
    await callPeerConnection.setRemoteDescription(new RTCSessionDescription(data.sdp));
    remoteDescSet = true;
    showCallModal(data.from_username, data.avatar_letter, data.avatar_color, true, data.is_video);
});

socket.on("call_answer", async function(data) {
    if (!callPeerConnection) return;
    await callPeerConnection.setRemoteDescription(new RTCSessionDescription(data.sdp));
    remoteDescSet = true;
    _setCallStatus("Podklyucheno...");
    for (var i = 0; i < localIceBuffer.length; i++) {
        socket.emit("call_ice", { to_user_id: callTargetUserId, candidate: localIceBuffer[i] });
    }
    localIceBuffer = [];
});

socket.on("call_ice", async function(data) {
    if (!callPeerConnection) return;
    if (remoteDescSet) {
        try { await callPeerConnection.addIceCandidate(new RTCIceCandidate(data.candidate)); } catch (_) {}
    } else {
        pendingIceCandidates.push(data.candidate);
    }
});

socket.on("call_end",    function() { _cleanupCall(); });
socket.on("call_reject", function() { _setCallStatus("Otkloneno"); setTimeout(_cleanupCall, 1500); });