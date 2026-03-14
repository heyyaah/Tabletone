// Медиа функции для голосовых сообщений и видео кружочков

let mediaRecorder = null;
let recordedChunks = [];
let recordingType = null; // 'voice' или 'video_note'
let recordingStartTime = null;
let recordingTimer = null;
let videoStream = null;

// Инициализация кнопок медиа
function initMediaButtons() {
    const voiceBtn = document.getElementById('voice-btn');
    const videoNoteBtn = document.getElementById('video-note-btn');
    
    if (voiceBtn) {
        voiceBtn.addEventListener('click', startVoiceRecording);
    }
    
    if (videoNoteBtn) {
        videoNoteBtn.addEventListener('click', startVideoNoteRecording);
    }
}

// Запись голосового сообщения
async function startVoiceRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        recordingType = 'voice';
        startRecording(stream);
        showRecordingUI('voice');
    } catch (error) {
        console.error('Ошибка доступа к микрофону:', error);
        showError('Не удалось получить доступ к микрофону');
    }
}

// Запись видео кружочка
async function startVideoNoteRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ 
            video: { 
                width: 384, 
                height: 384,
                facingMode: 'user'
            }, 
            audio: true 
        });
        videoStream = stream;
        recordingType = 'video_note';
        startRecording(stream);
        showRecordingUI('video_note');
    } catch (error) {
        console.error('Ошибка доступа к камере:', error);
        showError('Не удалось получить доступ к камере');
    }
}

// Начало записи
function startRecording(stream) {
    recordedChunks = [];
    recordingStartTime = Date.now();
    
    const options = { mimeType: 'video/webm;codecs=vp9,opus' };
    if (!MediaRecorder.isTypeSupported(options.mimeType)) {
        options.mimeType = 'video/webm';
    }
    
    mediaRecorder = new MediaRecorder(stream, options);
    
    mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
            recordedChunks.push(event.data);
        }
    };
    
    mediaRecorder.onstop = () => {
        const duration = Math.floor((Date.now() - recordingStartTime) / 1000);
        sendMediaMessage(duration);
        stream.getTracks().forEach(track => track.stop());
        if (videoStream) {
            videoStream.getTracks().forEach(track => track.stop());
            videoStream = null;
        }
    };
    
    mediaRecorder.start();
    
    // Таймер записи
    updateRecordingTimer();
}

// Обновление таймера
function updateRecordingTimer() {
    const timerElement = document.getElementById('recording-timer');
    if (!timerElement) return;
    
    recordingTimer = setInterval(() => {
        const elapsed = Math.floor((Date.now() - recordingStartTime) / 1000);
        const minutes = Math.floor(elapsed / 60);
        const seconds = elapsed % 60;
        timerElement.textContent = `${minutes}:${seconds.toString().padStart(2, '0')}`;
        
        // Максимум 5 минут
        if (elapsed >= 300) {
            stopRecording();
        }
    }, 1000);
}

// Остановка записи
function stopRecording() {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop();
    }
    if (recordingTimer) {
        clearInterval(recordingTimer);
        recordingTimer = null;
    }
    hideRecordingUI();
}

// Отмена записи
function cancelRecording() {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop();
    }
    if (recordingTimer) {
        clearInterval(recordingTimer);
        recordingTimer = null;
    }
    recordedChunks = [];
    if (videoStream) {
        videoStream.getTracks().forEach(track => track.stop());
        videoStream = null;
    }
    hideRecordingUI();
}

// Отправка медиа сообщения
async function sendMediaMessage(duration) {
    if (recordedChunks.length === 0 || !currentChatUserI