// Инициализация
document.addEventListener('DOMContentLoaded', function() {
    setupColorPicker();
    setupBioCounter();
    setupProfileForm();
    loadSessions();
    setupAvatarUpload();
});

function showPremiumModal(message) {
    let modal = document.getElementById('premium-required-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'premium-required-modal';
        modal.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:99999;align-items:center;justify-content:center;';
        modal.innerHTML = `
            <div style="background:var(--bg-primary,#fff);color:var(--text-primary,#000);border-radius:16px;padding:28px 24px;max-width:360px;width:90%;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,0.3);">
                <div style="font-size:40px;margin-bottom:12px;">👑</div>
                <h3 style="font-size:18px;font-weight:700;margin-bottom:8px;">Требуется Premium</h3>
                <p id="premium-modal-msg" style="color:#718096;font-size:14px;margin-bottom:20px;"></p>
                <button onclick="document.getElementById('premium-required-modal').style.display='none';" style="background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;border:none;border-radius:10px;padding:10px 28px;font-size:15px;font-weight:600;cursor:pointer;">Понятно</button>
            </div>`;
        modal.addEventListener('click', e => { if (e.target === modal) modal.style.display = 'none'; });
        document.body.appendChild(modal);
    }
    document.getElementById('premium-modal-msg').textContent = message || 'Эта функция доступна только для Premium пользователей.';
    modal.style.display = 'flex';
}

// Настройка выбора цвета
function setupColorPicker() {
    const colorOptions = document.querySelectorAll('.color-option');
    const avatarColorInput = document.getElementById('avatar_color');
    const profileAvatar = document.getElementById('profile-avatar');
    
    // Выделяем текущий цвет
    const currentColor = avatarColorInput.value;
    colorOptions.forEach(option => {
        if (option.dataset.color === currentColor) {
            option.classList.add('selected');
        }
        
        option.addEventListener('click', function() {
            // Убираем выделение со всех
            colorOptions.forEach(opt => opt.classList.remove('selected'));
            
            // Выделяем выбранный
            this.classList.add('selected');
            
            // Обновляем значение и аватар
            const color = this.dataset.color;
            avatarColorInput.value = color;
            profileAvatar.style.background = color;
        });
    });
}

// Счетчик символов для био
function setupBioCounter() {
    const bioTextarea = document.getElementById('bio');
    const bioCount = document.getElementById('bio-count');
    
    bioTextarea.addEventListener('input', function() {
        bioCount.textContent = this.value.length;
    });
}

// Обработка формы профиля
function setupProfileForm() {
    const form = document.getElementById('profile-form');
    
    form.addEventListener('submit', async function(e) {
        e.preventDefault();
        
        const displayName = document.getElementById('display_name').value.trim();
        const bio = document.getElementById('bio').value.trim();
        const avatarColor = document.getElementById('avatar_color').value;
        const chatWallpaper = document.getElementById('chat_wallpaper').value;
        const timezone = document.getElementById('timezone').value;
        
        try {
            const response = await fetch('/profile/update', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    display_name: displayName,
                    bio: bio,
                    avatar_color: avatarColor,
                    chat_wallpaper: chatWallpaper,
                    timezone: timezone
                })
            });
            
            const data = await response.json();

            if (!response.ok) {
                if (data.error === 'premium_required') {
                    showPremiumModal(data.message);
                    return;
                }
                throw new Error('Ошибка обновления профиля');
            }
            
            if (data.success) {
                showSuccessMessage();
            }
        } catch (error) {
            console.error('Ошибка:', error);
            alert('Не удалось обновить профиль. Попробуйте еще раз.');
        }
    });
}

// Показать сообщение об успехе
function showSuccessMessage() {
    const message = document.getElementById('success-message');
    message.style.display = 'flex';
    
    // Обновляем обои в главном окне если оно открыто
    const wallpaper = document.getElementById('chat_wallpaper').value;
    if (window.opener && window.opener.updateWallpaper) {
        window.opener.updateWallpaper(wallpaper);
    }
    
    setTimeout(() => {
        message.style.display = 'none';
    }, 3000);
}


// Выбор темы
function selectTheme(theme) {
    document.querySelectorAll('.theme-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    document.querySelector(`[data-theme="${theme}"]`).classList.add('active');
    setTheme(theme);
}

// Установка темы
function setTheme(theme) {
    document.body.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
    
    fetch('/theme/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme: theme })
    })
    .catch(e => console.error('Ошибка изменения темы:', e));
}

// Загрузка сохраненной темы при загрузке страницы
document.addEventListener('DOMContentLoaded', function() {
    const savedTheme = localStorage.getItem('theme') || document.body.getAttribute('data-theme') || 'light';
    const themeBtn = document.querySelector(`[data-theme="${savedTheme}"]`);
    if (themeBtn) {
        themeBtn.classList.add('active');
    }
});


// Выбор обоев
function selectWallpaper(wallpaper) {
    document.querySelectorAll('.wallpaper-option').forEach(option => {
        option.classList.remove('selected');
    });
    document.querySelector(`[data-wallpaper="${wallpaper}"]`).classList.add('selected');
    document.getElementById('chat_wallpaper').value = wallpaper;
}

// Экспорт функций в глобальную область
window.selectTheme = selectTheme;
window.selectWallpaper = selectWallpaper;

// ============================================
// УПРАВЛЕНИЕ СЕССИЯМИ
// ============================================

// Загрузка активных сессий
async function loadSessions() {
    try {
        const response = await fetch('/user/sessions');
        if (!response.ok) throw new Error('Ошибка загрузки сессий');
        
        const data = await response.json();
        displaySessions(data.sessions);
    } catch (error) {
        console.error('Ошибка загрузки сессий:', error);
        document.getElementById('sessions-list').innerHTML = '<div class="loading-sessions">Ошибка загрузки сессий</div>';
    }
}

// Отображение сессий
function displaySessions(sessions) {
    const container = document.getElementById('sessions-list');
    
    if (!sessions || sessions.length === 0) {
        container.innerHTML = '<div class="loading-sessions">Нет активных сессий</div>';
        return;
    }
    
    let html = '';
    sessions.forEach(session => {
        const deviceIcon = getDeviceIcon(session.device_name);
        html += `
            <div class="session-item ${session.is_current ? 'current' : ''}">
                <div class="session-info">
                    <div class="session-device">
                        <i class="${deviceIcon}"></i>
                        ${escapeHtml(session.device_name)}
                        ${session.is_current ? '<span class="badge">Текущая</span>' : ''}
                    </div>
                    <div class="session-details">
                        IP: ${escapeHtml(session.ip_address)} • 
                        Создана: ${session.created_at} • 
                        Активность: ${session.last_activity}
                    </div>
                </div>
                ${!session.is_current ? `
                    <div class="session-actions">
                        <button class="btn btn-danger" onclick="terminateSession(${session.id})">
                            <i class="fas fa-times"></i> Завершить
                        </button>
                    </div>
                ` : ''}
            </div>
        `;
    });
    
    container.innerHTML = html;
}

// Получение иконки устройства
function getDeviceIcon(deviceName) {
    if (deviceName.includes('Windows')) return 'fab fa-windows';
    if (deviceName.includes('Mac')) return 'fab fa-apple';
    if (deviceName.includes('Linux')) return 'fab fa-linux';
    if (deviceName.includes('iPhone') || deviceName.includes('iPad')) return 'fas fa-mobile-alt';
    if (deviceName.includes('Android')) return 'fab fa-android';
    return 'fas fa-desktop';
}

// Завершение сессии
async function terminateSession(sessionId) {
    if (!confirm('Вы уверены, что хотите завершить эту сессию?')) {
        return;
    }
    
    try {
        const response = await fetch(`/user/sessions/${sessionId}/terminate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (!response.ok) throw new Error('Ошибка завершения сессии');
        
        const data = await response.json();
        
        if (data.success) {
            showSuccessMessage('Сессия завершена');
            loadSessions();
        }
    } catch (error) {
        console.error('Ошибка завершения сессии:', error);
        alert('Не удалось завершить сессию');
    }
}

// Завершение всех сессий кроме текущей
async function terminateAllSessions() {
    if (!confirm('Вы уверены, что хотите завершить все сессии кроме текущей? Вы будете выведены из системы на всех других устройствах.')) {
        return;
    }
    
    try {
        const response = await fetch('/user/sessions/terminate_all', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (!response.ok) throw new Error('Ошибка завершения сессий');
        
        const data = await response.json();
        
        if (data.success) {
            showSuccessMessage('Все сессии завершены');
            loadSessions();
        }
    } catch (error) {
        console.error('Ошибка завершения сессий:', error);
        alert('Не удалось завершить сессии');
    }
}

// Экранирование HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Обновленная функция показа сообщения об успехе
function showSuccessMessage(message = 'Профиль успешно обновлен!') {
    const messageEl = document.getElementById('success-message');
    messageEl.innerHTML = `<i class="fas fa-check-circle"></i> ${message}`;
    messageEl.style.display = 'flex';
    
    // Обновляем обои в главном окне если оно открыто
    const wallpaper = document.getElementById('chat_wallpaper')?.value;
    if (wallpaper && window.opener && window.opener.updateWallpaper) {
        window.opener.updateWallpaper(wallpaper);
    }
    
    setTimeout(() => {
        messageEl.style.display = 'none';
    }, 3000);
}

window.terminateSession = terminateSession;
window.terminateAllSessions = terminateAllSessions;

// ============================================
// ЗАГРУЗКА АВАТАРКИ
// ============================================

// Настройка загрузки аватарки
function setupAvatarUpload() {
    const uploadInput = document.getElementById('avatar-upload');
    if (!uploadInput) return;
    
    uploadInput.addEventListener('change', async function(e) {
        const file = e.target.files[0];
        if (!file) return;
        
        // Проверка типа файла
        if (!file.type.startsWith('image/')) {
            alert('Пожалуйста, выберите изображение');
            return;
        }
        
        // Проверка размера (макс 5MB)
        if (file.size > 5 * 1024 * 1024) {
            alert('Размер файла не должен превышать 5MB');
            return;
        }
        
        // Загружаем аватарку
        const formData = new FormData();
        formData.append('avatar', file);
        
        try {
            const response = await fetch('/profile/upload_avatar', {
                method: 'POST',
                body: formData
            });
            
            const data = await response.json();

            if (!response.ok) {
                if (data.error === 'premium_required') {
                    showPremiumModal(data.message);
                    return;
                }
                throw new Error(data.error || 'Ошибка загрузки');
            }
            
            if (data.success) {
                // Обновляем аватарку на странице
                const avatar = document.getElementById('profile-avatar');
                avatar.style.backgroundImage = `url('${data.avatar_url}')`;
                avatar.style.backgroundSize = 'cover';
                avatar.style.backgroundPosition = 'center';
                avatar.textContent = '';
                
                showSuccessMessage('Аватарка успешно загружена!');
                
                // Перезагружаем страницу через 1 секунду чтобы показать кнопку удаления
                setTimeout(() => {
                    window.location.reload();
                }, 1000);
            }
        } catch (error) {
            console.error('Ошибка загрузки аватарки:', error);
            alert('Не удалось загрузить аватарку: ' + error.message);
        }
        
        // Сбрасываем input
        e.target.value = '';
    });
}

// Удаление аватарки
async function deleteAvatar() {
    if (!confirm('Вы уверены, что хотите удалить аватарку?')) {
        return;
    }
    
    try {
        const response = await fetch('/profile/delete_avatar', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (!response.ok) {
            throw new Error('Ошибка удаления');
        }
        
        const data = await response.json();
        
        if (data.success) {
            showSuccessMessage('Аватарка удалена');
            
            // Перезагружаем страницу
            setTimeout(() => {
                window.location.reload();
            }, 1000);
        }
    } catch (error) {
        console.error('Ошибка удаления аватарки:', error);
        alert('Не удалось удалить аватарку');
    }
}

window.deleteAvatar = deleteAvatar;
