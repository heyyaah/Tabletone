// Обработка множественных файлов
let selectedFiles = [];

function handleMultipleFilesSelect(event) {
    const files = Array.from(event.target.files);
    
    if (files.length === 0) return;
    
    selectedFiles = files;
    showFilesPreviewModal();
    
    // Очищаем input
    event.target.value = '';
}

// Показать модальное окно с превью файлов (как в Telegram)
function showFilesPreviewModal() {
    if (selectedFiles.length === 0) return;
    
    const modal = document.createElement('div');
    modal.className = 'modal active';
    modal.id = 'files-preview-modal';
    modal.innerHTML = `
        <div class="modal-content" style="max-width: 600px; max-height: 90vh; display: flex; flex-direction: column;">
            <div class="modal-header">
                <h2><i class="fas fa-paperclip"></i> Выбрано ${selectedFiles.length} ${getFilesWord(selectedFiles.length)}</h2>
                <button class="close-btn" onclick="closeFilesPreviewModal()">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="modal-body" style="flex: 1; overflow-y: auto; padding: 15px;">
                <div id="files-preview-grid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 10px; margin-bottom: 15px;">
                </div>
                
                <div style="margin-top: 15px;">
                    <label style="display: block; margin-bottom: 8px; font-weight: 600; color: var(--text-primary);">
                        <i class="fas fa-comment"></i> Комментарий
                    </label>
                    <textarea id="files-caption-input" placeholder="Добавьте подпись..." style="width: 100%; padding: 10px; border: 1px solid var(--border-color); border-radius: 8px; background: var(--bg-primary); color: var(--text-primary); resize: vertical; min-height: 60px; font-family: inherit;"></textarea>
                </div>
                
                <div style="display: flex; gap: 10px; margin-top: 15px;">
                    <button class="btn" onclick="document.getElementById('add-more-files-input').click()" style="flex: 1; background: var(--bg-secondary); color: var(--text-primary);">
                        <i class="fas fa-plus"></i> Добавить еще
                        <input type="file" id="add-more-files-input" accept="image/*,video/*,.pdf,.doc,.docx,.txt" multiple style="display: none;" onchange="handleAddMoreFiles(event)">
                    </button>
                </div>
            </div>
            <div class="modal-footer" style="padding: 15px; border-top: 1px solid var(--border-color); display: flex; gap: 10px;">
                <button class="btn" onclick="closeFilesPreviewModal()" style="flex: 1; background: var(--bg-secondary); color: var(--text-primary);">
                    <i class="fas fa-times"></i> Отмена
                </button>
                <button class="btn btn-primary" id="files-send-btn" onclick="sendSelectedFiles()" style="flex: 2;">
                    <i class="fas fa-paper-plane"></i> Отправить
                </button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    
    renderFilesPreview();
    
    // Закрытие по клику вне
    modal.addEventListener('click', function(e) {
        if (e.target === modal) {
            closeFilesPreviewModal();
        }
    });
}

// Рендер превью файлов
function renderFilesPreview() {
    const grid = document.getElementById('files-preview-grid');
    if (!grid) return;
    
    let html = '';
    
    selectedFiles.forEach((file, index) => {
        const fileType = file.type.startsWith('image/') ? 'image' : 
                        file.type.startsWith('video/') ? 'video' : 'file';
        
        if (fileType === 'image') {
            // Превью изображения
            const url = URL.createObjectURL(file);
            html += `
                <div style="position: relative; aspect-ratio: 1; border-radius: 8px; overflow: hidden; background: var(--bg-secondary);">
                    <img src="${url}" style="width: 100%; height: 100%; object-fit: cover;">
                    <button onclick="removeFileFromPreview(${index})" style="position: absolute; top: 5px; right: 5px; background: rgba(0,0,0,0.7); border: none; color: white; width: 24px; height: 24px; border-radius: 50%; cursor: pointer; display: flex; align-items: center; justify-content: center;">
                        <i class="fas fa-times"></i>
                    </button>
                    <div style="position: absolute; bottom: 0; left: 0; right: 0; background: linear-gradient(transparent, rgba(0,0,0,0.7)); padding: 5px; font-size: 10px; color: white; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                        ${file.name}
                    </div>
                </div>
            `;
        } else if (fileType === 'video') {
            // Превью видео
            const url = URL.createObjectURL(file);
            html += `
                <div style="position: relative; aspect-ratio: 1; border-radius: 8px; overflow: hidden; background: var(--bg-secondary);">
                    <video src="${url}" style="width: 100%; height: 100%; object-fit: cover;"></video>
                    <div style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); background: rgba(0,0,0,0.7); width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center;">
                        <i class="fas fa-play" style="color: white; font-size: 16px; margin-left: 3px;"></i>
                    </div>
                    <button onclick="removeFileFromPreview(${index})" style="position: absolute; top: 5px; right: 5px; background: rgba(0,0,0,0.7); border: none; color: white; width: 24px; height: 24px; border-radius: 50%; cursor: pointer; display: flex; align-items: center; justify-content: center;">
                        <i class="fas fa-times"></i>
                    </button>
                    <div style="position: absolute; bottom: 0; left: 0; right: 0; background: linear-gradient(transparent, rgba(0,0,0,0.7)); padding: 5px; font-size: 10px; color: white; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                        ${file.name}
                    </div>
                </div>
            `;
        } else {
            // Превью файла
            const icon = getFileIcon(file.name);
            const size = formatFileSize(file.size);
            html += `
                <div style="position: relative; aspect-ratio: 1; border-radius: 8px; overflow: hidden; background: var(--bg-secondary); display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 10px;">
                    <i class="fas ${icon}" style="font-size: 32px; color: var(--primary-color); margin-bottom: 8px;"></i>
                    <div style="font-size: 10px; text-align: center; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; width: 100%;">
                        ${file.name}
                    </div>
                    <div style="font-size: 9px; color: #a0aec0; margin-top: 2px;">
                        ${size}
                    </div>
                    <button onclick="removeFileFromPreview(${index})" style="position: absolute; top: 5px; right: 5px; background: rgba(0,0,0,0.7); border: none; color: white; width: 24px; height: 24px; border-radius: 50%; cursor: pointer; display: flex; align-items: center; justify-content: center;">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
            `;
        }
    });
    
    grid.innerHTML = html;
    
    // Обновляем заголовок
    const header = document.querySelector('#files-preview-modal .modal-header h2');
    if (header) {
        header.innerHTML = `<i class="fas fa-paperclip"></i> Выбрано ${selectedFiles.length} ${getFilesWord(selectedFiles.length)}`;
    }
}

// Удалить файл из превью
function removeFileFromPreview(index) {
    selectedFiles.splice(index, 1);
    
    if (selectedFiles.length === 0) {
        closeFilesPreviewModal();
    } else {
        renderFilesPreview();
    }
}

// Добавить еще файлы
function handleAddMoreFiles(event) {
    const newFiles = Array.from(event.target.files);
    selectedFiles = [...selectedFiles, ...newFiles];
    renderFilesPreview();
    event.target.value = '';
}

// Закрыть модальное окно
function closeFilesPreviewModal() {
    const modal = document.getElementById('files-preview-modal');
    if (modal) {
        modal.remove();
    }
    selectedFiles = [];
}

// Отправить выбранные файлы
async function sendSelectedFiles() {
    if (selectedFiles.length === 0) return;
    if (!currentChatUserId && !currentGroupId) return;

    const sendBtn = document.getElementById('files-send-btn');
    if (sendBtn) {
        if (sendBtn.disabled) return; // уже отправляется
        sendBtn.disabled = true;
        sendBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Отправка...';
    }
    // Блокируем кнопку прикрепления
    const attachBtn = document.getElementById('image-btn');
    if (attachBtn) attachBtn.disabled = true;

    const caption = document.getElementById('files-caption-input').value.trim();
    
    try {
        if (currentGroupId) {
            await sendMultipleFilesToGroup(currentGroupId, caption);
        } else {
            await sendMultipleFiles(currentChatUserId, caption);
        }
        
        closeFilesPreviewModal();
        showError('Файлы отправлены!', 'success');
        
    } catch (error) {
        showError('Не удалось отправить файлы');
    } finally {
        if (sendBtn) {
            sendBtn.disabled = false;
            sendBtn.innerHTML = '<i class="fas fa-paper-plane"></i> Отправить';
        }
        if (attachBtn) attachBtn.disabled = false;
    }
}

// Вспомогательные функции
function getFilesWord(count) {
    if (count === 1) return 'файл';
    if (count >= 2 && count <= 4) return 'файла';
    return 'файлов';
}

function getFileIcon(filename) {
    const ext = filename.split('.').pop().toLowerCase();
    if (['pdf'].includes(ext)) return 'fa-file-pdf';
    if (['doc', 'docx'].includes(ext)) return 'fa-file-word';
    if (['txt'].includes(ext)) return 'fa-file-alt';
    return 'fa-file';
}

function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// Отправка множественных файлов
async function sendMultipleFiles(receiverId, caption = '') {
    if (selectedFiles.length === 0) return null;
    
    const formData = new FormData();
    formData.append('receiver_id', receiverId);
    formData.append('caption', caption);
    
    selectedFiles.forEach((file, index) => {
        formData.append('files', file);
    });
    
    try {
        const response = await fetch('/send_multiple_files', {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) throw new Error('Ошибка отправки файлов');
        
        const data = await response.json();
        selectedFiles = [];
        return data;
    } catch (error) {
        console.error('Ошибка отправки файлов:', error);
        throw error;
    }
}

// Отправка файлов в группу
async function sendMultipleFilesToGroup(groupId, caption = '') {
    if (selectedFiles.length === 0) return null;
    
    const formData = new FormData();
    formData.append('group_id', groupId);
    formData.append('caption', caption);
    
    selectedFiles.forEach((file, index) => {
        formData.append('files', file);
    });
    
    try {
        const response = await fetch('/send_multiple_files_group', {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) throw new Error('Ошибка отправки файлов');
        
        const data = await response.json();
        
        // Добавляем сообщение локально для отправителя сразу
        if (data.success && data.message) {
            const currentUserId = parseInt(document.body.getAttribute('data-user-id'));
            data.message.is_mine = data.message.sender_id === currentUserId;
            
            // Добавляем сообщение в чат немедленно для отправителя
            if (typeof addGroupMessageToChat === 'function') {
                addGroupMessageToChat(data.message);
            }
            
            console.log('Files sent to group:', data.message);
        }
        
        selectedFiles = [];
        return data;
    } catch (error) {
        console.error('Ошибка отправки файлов в группу:', error);
        throw error;
    }
}

window.handleMultipleFilesSelect = handleMultipleFilesSelect;
window.removeFileFromPreview = removeFileFromPreview;
window.handleAddMoreFiles = handleAddMoreFiles;
window.closeFilesPreviewModal = closeFilesPreviewModal;
window.sendSelectedFiles = sendSelectedFiles;
window.sendMultipleFiles = sendMultipleFiles;

