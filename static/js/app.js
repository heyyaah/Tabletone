// Основные переменные
let currentChatUserId = null;
let searchTimeout = null;
let socket = null;
const openedChats = new Set(); // чаты, которые были открыты — бейдж не показываем
const userTimezone = document.body.getAttribute('data-timezone') || 'Europe/Moscow';
const _isPremium = document.body.getAttribute('data-is-premium') === 'true';
const _isAdmin = document.body.getAttribute('data-is-admin') === 'true';

// Показать модалку "нужен Premium"
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

function formatMsgTime(isoString) {
    if (!isoString) return '';
    // Добавляем Z если нет суффикса timezone (сервер отдаёт UTC без Z)
    const normalized = isoString.endsWith('Z') || isoString.includes('+') ? isoString : isoString + 'Z';
    const date = new Date(normalized);
    const nowInTz = new Date(new Date().toLocaleString('en-US', { timeZone: userTimezone }));
    const dateInTz = new Date(date.toLocaleString('en-US', { timeZone: userTimezone }));

    const todayStr = nowInTz.toDateString();
    const yesterdayDate = new Date(nowInTz);
    yesterdayDate.setDate(yesterdayDate.getDate() - 1);

    if (dateInTz.toDateString() === todayStr) {
        return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: userTimezone });
    } else if (dateInTz.toDateString() === yesterdayDate.toDateString()) {
        return 'Вчера';
    } else {
        return date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', timeZone: userTimezone });
    }
}

// Инициализация при загрузке страницы
document.addEventListener('DOMContentLoaded', function() {
    loadAllChats();  // Загружаем объединенный список
    setupEventListeners();
    setupMobileNavigation();
    connectSocketIO();

    // Открыть чат если передан через localStorage (например, из /bots)
    const _pendingChat = localStorage.getItem('open_chat_on_load');
    if (_pendingChat) {
        localStorage.removeItem('open_chat_on_load');
        try {
            const _c = JSON.parse(_pendingChat);
            console.log('[open_chat_on_load]', _c);
            if (_c.byUsername) {
                const _tryOpen = (attempts) => {
                    console.log('[open_chat_on_load] trying openChatByUsername, attempts left:', attempts);
                    openChatByUsername(_c.id);
                };
                setTimeout(() => _tryOpen(10), 1200);
            } else {
                setTimeout(() => openChat(_c.id, _c.name), 800);
            }
        } catch(e) { console.error('open_chat_on_load error:', e); }
    }

    // Открыть группу если передан ?open_group= в URL (например, после вступления по инвайту)
    const _urlParams = new URLSearchParams(window.location.search);
    const _openGroupId = _urlParams.get('open_group');
    if (_openGroupId) {
        // Убираем параметр из URL без перезагрузки
        const _cleanUrl = window.location.pathname;
        window.history.replaceState({}, '', _cleanUrl);
        setTimeout(() => openGroup(parseInt(_openGroupId), ''), 900);
    }
});

// Подключение к Socket.IO
function connectSocketIO() {
    try {
        socket = io({
            transports: ['websocket', 'polling'],
            reconnection: true,
            reconnectionDelay: 1000,
            reconnectionAttempts: Infinity,
            reconnectionDelayMax: 5000
        });
        
        socket.on('connect', function() {
            console.log('Socket.IO connected');
            setupGroupSocketHandlers();
            initCallSocketHandlers(socket);
            setupSecretChatSocketHandlers();
            // Typing handlers
            socket.on('user_typing', function(data) {
                if (data.chat_type === 'private' && currentChatUserId) {
                    showTypingIndicator(data.name);
                } else if (data.chat_type === 'group' && currentGroupId === data.group_id) {
                    showTypingIndicator(data.name);
                }
            });
            socket.on('user_stop_typing', function(data) {
                if (data.chat_type === 'private' && currentChatUserId) hideTypingIndicator();
                else if (data.chat_type === 'group' && currentGroupId === data.group_id) hideTypingIndicator();
            });
            // Обновляем галочки прочтения
            socket.on('messages_read', function(data) {
                (data.message_ids || []).forEach(id => {
                    const el = document.querySelector(`[data-msg-status="${id}"]`);
                    if (el) el.innerHTML = '<i class="fas fa-check-double read"></i>';
                });
            });
            // Reload messages on reconnect to catch anything missed while disconnected
            if (currentChatUserId) loadMessages(currentChatUserId);
            else if (currentGroupId) loadGroupMessages(currentGroupId);
        });

        // Mobile: reconnect when tab becomes visible again
        document.addEventListener('visibilitychange', function() {
            if (!document.hidden) {
                if (!socket.connected) {
                    socket.connect();
                } else {
                    // Refresh messages to catch anything missed in background
                    if (currentChatUserId) loadMessages(currentChatUserId);
                    else if (currentGroupId) loadGroupMessages(currentGroupId);
                }
            }
        });
        
        socket.on('online_users_list', function(data) {
            console.log('Online users list:', data);
            if (data.online_users) {
                data.online_users.forEach(user => {
                    updateUserOnlineStatus(user.user_id, true);
                });
            }
        });
        
        socket.on('new_message', function(data) {
            console.log('New message received:', data);
            handleNewMessage(data);
        });
        
        socket.on('profile_updated', function(data) {
            console.log('Profile updated:', data);
            handleProfileUpdate(data);
        });
        
        socket.on('disconnect', function() {
            console.log('Socket.IO disconnected');
        });
        
        socket.on('connect_error', function(error) {
            console.error('Socket.IO connection error:', error);
        });
        
        socket.on('user_online', function(data) {
            console.log('User online:', data);
            updateUserOnlineStatus(data.user_id, true);
        });
        
        socket.on('user_offline', function(data) {
            console.log('User offline:', data);
            updateUserOnlineStatus(data.user_id, false, data.last_seen);
        });
        
        socket.on('message_deleted', function(data) {
            console.log('Message deleted:', data);
            updateMessageDeleted(data.message_id);
            // Обновляем превью в списке чатов
            if (data.other_user_id) loadAllChats();
        });

        socket.on('group_message_deleted', function(data) {
            const el = document.querySelector(`[data-message-id="${data.message_id}"][data-is-group="1"]`);
            if (el) el.remove();
        });
        
        socket.on('message_edited', function(data) {
            console.log('Message edited:', data);
            updateMessageContent(data.message_id, data.content, data.edited_at);
        });
        
        socket.on('theme_changed', function(data) {
            console.log('Theme changed:', data);
            if (data.user_id !== parseInt(document.body.getAttribute('data-user-id'))) {
                setTheme(data.theme);
            }
        });

        socket.on('reaction_updated', function(data) {
            const msgId = data.message_id || data.group_message_id;
            if (msgId && typeof renderReactions === 'function') renderReactions(msgId, data.reactions);
        });

        socket.on('message_pinned', function(data) {
            if ((data.chat_type === 'private' && currentChatUserId) || (data.chat_type === 'group' && currentGroupId === data.group_id)) {
                if (typeof loadPinnedMessage === 'function') loadPinnedMessage();
            }
        });

        socket.on('message_unpinned', function(data) {
            const bar = document.getElementById('pinned-bar');
            if (bar) bar.style.display = 'none';
        });
    } catch (error) {
        console.error('Failed to initialize Socket.IO:', error);
    }
}

// Обработка обновления профиля
function handleProfileUpdate(userInfo) {
    // Обновляем в списке чатов
    updateChatItemAvatar(userInfo);
    
    // Перезагружаем список чатов, чтобы обновить все данные
    loadChats();
    
    // Если это текущий открытый чат, обновляем заголовок
    if (currentChatUserId === userInfo.id) {
        const chatUsername = document.getElementById('chat-username');
        const chatAvatar = document.querySelector('.chat-header .chat-avatar');
        
        if (chatUsername) {
            const verifiedBadge = userInfo.is_verified ? ' <i class="fas fa-check-circle" style="color: #667eea;"></i>' : '';
            chatUsername.innerHTML = escapeHtml(userInfo.display_name || userInfo.username) + verifiedBadge;
        }
        
        if (chatAvatar) {
            if (userInfo.avatar_url) {
                chatAvatar.style.backgroundImage = `url('${userInfo.avatar_url}')`;
                chatAvatar.style.backgroundSize = 'cover';
                chatAvatar.style.backgroundPosition = 'center';
                chatAvatar.style.backgroundColor = userInfo.avatar_color;
                chatAvatar.textContent = '';
            } else {
                chatAvatar.style.backgroundImage = 'none';
                chatAvatar.style.backgroundColor = userInfo.avatar_color;
                chatAvatar.textContent = userInfo.avatar_letter;
            }
        }
    }
}

// Обработка нового сообщения через Socket.IO
function handleNewMessage(data) {
    const message = data.message;
    const otherUserId = data.other_user_id;
    
    console.log('handleNewMessage - currentChatUserId:', currentChatUserId, 'otherUserId:', otherUserId, 'message:', message);
    
    // Если сообщение для текущего открытого чата
    if (currentChatUserId === otherUserId) {
        console.log('Adding message to current chat');
        addMessageToChat(message);
        scrollToBottom();
        openedChats.add(otherUserId);
        markChatAsRead(otherUserId);
        // Обновляем только время/превью в chat-item без перерисовки бейджа
        updateChatItemPreview(otherUserId, message);
    } else {
        // Не вызываем loadAllChats() — только обновляем конкретный элемент
        openedChats.delete(otherUserId); // этот чат теперь имеет непрочитанные
        updateChatItemBadge(otherUserId, message);

        // Всплывающее уведомление (не для своих сообщений)
        const _myId = parseInt(document.body.getAttribute('data-user-id'));
        if (message.sender_id !== _myId) {
            const si = data.sender_info || {};
            showMessageNotification({
                name: si.display_name || si.username || 'Новое сообщение',
                text: message.content || '',
                avatarUrl: si.avatar_url || null,
                avatarColor: si.avatar_color || '#667eea',
                avatarLetter: si.avatar_letter || '?',
                onClick: () => openChat(otherUserId, si.display_name || si.username || ''),
            });
        }
    }
    
    // Обновляем аватарку отправителя в списке чатов
    if (data.sender_info) {
        updateChatItemAvatar(data.sender_info);
    }
}

// Обновить превью последнего сообщения в chat-item (без изменения бейджа)
function updateChatItemPreview(userId, message) {
    const chatItem = document.querySelector(`.chat-item[data-user-id="${userId}"]`);
    if (!chatItem) return;
    const lastMsgEl = chatItem.querySelector('.chat-last-message');
    if (lastMsgEl) lastMsgEl.textContent = message.content || '';
    const timeEl = chatItem.querySelector('.chat-time');
    if (timeEl) timeEl.textContent = message.timestamp_iso ? formatMsgTime(message.timestamp_iso) : (message.timestamp || '');
}

// Добавить/обновить бейдж непрочитанных для chat-item
function updateChatItemBadge(userId, message) {
    const chatItem = document.querySelector(`.chat-item[data-user-id="${userId}"]`);
    if (!chatItem) {
        // Чата нет в списке — перезагружаем полностью
        loadAllChats();
        return;
    }
    // Обновляем превью
    const lastMsgEl = chatItem.querySelector('.chat-last-message');
    if (lastMsgEl) lastMsgEl.textContent = message.content || '';
    const timeEl = chatItem.querySelector('.chat-time');
    if (timeEl) timeEl.textContent = message.timestamp_iso ? formatMsgTime(message.timestamp_iso) : (message.timestamp || '');
    // Обновляем бейдж
    let badge = chatItem.querySelector('.unread-badge');
    if (badge) {
        const current = parseInt(badge.textContent) || 0;
        badge.textContent = current + 1;
    } else {
        badge = document.createElement('div');
        badge.className = 'unread-badge';
        badge.textContent = '1';
        // Вставляем перед .chat-time
        const timeDiv = chatItem.querySelector('.chat-time');
        if (timeDiv) {
            chatItem.insertBefore(badge, timeDiv);
        } else {
            chatItem.appendChild(badge);
        }
    }
}

// Обновление аватарки в списке чатов
function updateChatItemAvatar(userInfo) {
    const chatItem = document.querySelector(`.chat-item[data-user-id="${userInfo.id}"]`);
    if (chatItem) {
        const avatar = chatItem.querySelector('.chat-avatar');
        const username = chatItem.querySelector('.chat-username');
        
        if (avatar) {
            if (userInfo.avatar_url) {
                avatar.style.backgroundImage = `url('${userInfo.avatar_url}')`;
                avatar.style.backgroundSize = 'cover';
                avatar.style.backgroundPosition = 'center';
                avatar.style.backgroundColor = userInfo.avatar_color;
                avatar.textContent = '';
            } else {
                avatar.style.backgroundImage = 'none';
                avatar.style.backgroundColor = userInfo.avatar_color;
                avatar.textContent = userInfo.avatar_letter;
            }
        }
        
        if (username) {
            const verifiedBadge = userInfo.is_verified ? '<i class="fas fa-check-circle" style="color: #667eea; margin-left: 5px; font-size: 14px;"></i>' : '';
            username.innerHTML = escapeHtml(userInfo.display_name || userInfo.username) + verifiedBadge;
        }
    }
}

// Настройка обработчиков событий
function setupEventListeners() {
    // Поиск пользователей
    const searchInput = document.getElementById('search-input');
    if (searchInput) {
        searchInput.addEventListener('input', handleSearch);
    }
    
    // Отправка сообщения
    const messageForm = document.getElementById('message-form');
    if (messageForm) {
        messageForm.addEventListener('submit', handleSendMessage);
    }
    
    // Автофокус на поле ввода сообщения
    const messageInput = document.getElementById('message-input');
    if (messageInput) {
        messageInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                if (e.shiftKey) {
                    // Shift+Enter — новая строка
                    e.preventDefault();
                    const start = this.selectionStart;
                    const end = this.selectionEnd;
                    this.value = this.value.substring(0, start) + '\n' + this.value.substring(end);
                    this.selectionStart = this.selectionEnd = start + 1;
                    this.dispatchEvent(new Event('input'));
                } else {
                    // Enter — отправить
                    e.preventDefault();
                    messageForm.dispatchEvent(new Event('submit'));
                }
            }
        });
        // Авто-ресайз textarea при переносах строк + счётчик символов
        messageInput.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 160) + 'px';
            // Счётчик символов — показываем при > 800 из 4096
            const MAX = 4096;
            const len = this.value.length;
            let counter = document.getElementById('_msg-char-counter');
            if (len > 800) {
                if (!counter) {
                    counter = document.createElement('span');
                    counter.id = '_msg-char-counter';
                    counter.style.cssText = 'position:absolute;right:56px;bottom:2px;font-size:11px;color:var(--text-muted);pointer-events:none;z-index:5;';
                    const form = document.getElementById('message-form');
                    if (form) { form.style.position = 'relative'; form.appendChild(counter); }
                }
                counter.textContent = `${len}/${MAX}`;
                counter.style.color = len > MAX * 0.95 ? '#e53e3e' : 'var(--text-muted)';
            } else if (counter) {
                counter.remove();
            }
        });
    }

    // Контекстное меню сообщений (long-press / правый клик)
    setupMessageContextMenu();
}

// ─── Контекстное меню сообщений ───────────────────────────────────────────────

let _ctxTimer = null;
let _ctxSuppressClick = false;

function setupMessageContextMenu() {
    // Создаём один глобальный элемент меню
    if (!document.getElementById('msg-ctx-menu')) {
        const menu = document.createElement('div');
        menu.id = 'msg-ctx-menu';
        menu.className = 'msg-ctx-menu';
        document.body.appendChild(menu);
    }

    const container = document.getElementById('messages-container');
    if (!container) return;

    // Делегирование — слушаем на контейнере
    container.addEventListener('touchstart', _onMsgTouchStart, { passive: true });
    container.addEventListener('touchend', _onMsgTouchEnd);
    container.addEventListener('touchmove', _onMsgTouchEnd, { passive: true });
    container.addEventListener('contextmenu', _onMsgContextMenu);

    // Закрытие по клику вне (вешаем только один раз)
    if (!window._ctxMenuGlobalInit) {
        window._ctxMenuGlobalInit = true;
        document.addEventListener('click', _closeCtxMenu);
        document.addEventListener('keydown', e => { if (e.key === 'Escape') _closeCtxMenu(); });
    }
}

function _getMsgEl(target) {
    return target.closest('.message[data-message-id]');
}

function _onMsgTouchStart(e) {
    const msg = _getMsgEl(e.target);
    if (!msg) return;
    const touch = e.touches[0];
    _ctxTimer = setTimeout(() => {
        _ctxSuppressClick = true;
        _showCtxMenu(msg, touch.clientX, touch.clientY);
    }, 600);
}

function _onMsgTouchEnd(e) {
    clearTimeout(_ctxTimer);
    _ctxTimer = null;
}

function _onMsgContextMenu(e) {
    const msg = _getMsgEl(e.target);
    if (!msg) return;
    e.preventDefault();
    e.stopPropagation();
    _showCtxMenu(msg, e.clientX, e.clientY);
}

function _showCtxMenu(msgEl, x, y) {
    const msgId = msgEl.dataset.messageId;
    const isMine = msgEl.dataset.isMine === '1';
    const isDeleted = msgEl.dataset.isDeleted === '1';
    const isGroup = msgEl.dataset.isGroup === '1';
    const isChannel = isGroup && _currentGroupData && _currentGroupData.is_channel;
    const isFav = !!msgEl.dataset.favId;

    const menu = document.getElementById('msg-ctx-menu');
    if (!menu) return;

    let items = [];

    // Копировать — всегда если есть текст (объявляем первым)
    const textContent = msgEl.querySelector('.message-content')?.textContent?.trim();

    // Ответить — всегда если не удалено и не избранное
    if (!isDeleted && !isFav) {
        const senderName = msgEl.querySelector('.group-sender-name')?.textContent?.trim()
            || (isMine ? 'Вы' : (document.getElementById('chat-name')?.textContent?.trim() || ''));
        items.push({ icon: 'fa-reply', label: 'Ответить', _fn: () => _startReply(msgId, textContent || '', senderName), color: '' });
    }

    if (textContent && !isDeleted) {
        items.push({ icon: 'fa-copy', label: 'Копировать', _fn: () => navigator.clipboard.writeText(textContent), color: '' });
    }

    if (!isDeleted && !isFav) {
        items.push({ icon: 'fa-bookmark', label: 'В избранное', _fn: () => addToFavorites(isGroup ? 'group_message' : 'message', msgId), color: '' });
    }
    if (!isDeleted && !isFav) {
        items.push({ icon: 'fa-smile', label: 'Реакция', _fn: () => showReactionPicker(msgId, isGroup), color: '' });
    }
    if (!isDeleted && !isFav && isGroup && _currentGroupData && _currentGroupData.is_channel) {
        items.push({ icon: 'fa-fire', label: '✨ Искра', _fn: () => showSparkReactModal(msgId), color: '' });
    }
    if (!isDeleted && !isFav) {
        items.push({ icon: 'fa-share', label: 'Переслать', _fn: () => showForwardModal(msgId, isGroup), color: '' });
    }
    if (!isDeleted && !isFav) {
        items.push({ icon: 'fa-thumbtack', label: 'Закрепить', _fn: () => pinCurrentMessage(msgId, isGroup), color: '' });
    }
    if (!isDeleted && !isFav && isMine) {
        items.push({ icon: 'fa-clock', label: 'Таймер удаления', _fn: () => showTimerModal(msgId, isGroup), color: '' });
    }
    if (!isDeleted && !isFav && !isGroup) {
        items.push({ icon: 'fa-eye-slash', label: 'Скрыть чат', _fn: () => hideCurrentChat(), color: '' });
    }
    if (isMine && !isDeleted && !isGroup && !isFav) {
        items.push({ icon: 'fa-edit', label: 'Изменить', _fn: () => editMessage(msgId), color: '' });
    }
    // Перевод — для любого непустого сообщения
    if (!isDeleted && !isFav) {
        items.push({ icon: 'fa-language', label: 'Перевести', _fn: () => translateMessage(msgId, isGroup), color: '' });
    }
    // Кто прочитал — только в группах (не каналах), для своих сообщений
    if (!isDeleted && !isFav && isGroup && !isChannel && isMine) {
        items.push({ icon: 'fa-eye', label: 'Кто прочитал', _fn: () => showMessageReaders(msgId, currentGroupId), color: '' });
    }
    // В канале удалять могут только админы; в группе — только свои
    const canDelete = isFav
        ? false
        : (isChannel
            ? (_currentGroupData && _currentGroupData.is_admin)
            : (isMine && !isDeleted));
    if (canDelete) {
        items.push({ icon: 'fa-trash', label: 'Удалить', _fn: () => isGroup ? deleteGroupMessage(msgId) : deleteMessage(msgId), color: '#e53e3e' });
    }
    if (isFav) {
        items.push({ icon: 'fa-trash', label: 'Удалить', _fn: () => deleteFavorite(msgEl.dataset.favId), color: '#e53e3e' });
    }

    if (items.length === 0) return;

    menu.innerHTML = items.map((item, i) => `
        <button class="msg-ctx-item${item.color ? ' msg-ctx-danger' : ''}" data-ctx-idx="${i}">
            <i class="fas ${item.icon}"></i>
            <span>${item.label}</span>
        </button>
    `).join('');

    // Сохраняем actions отдельно и вешаем обработчики
    menu.querySelectorAll('[data-ctx-idx]').forEach(btn => {
        const idx = parseInt(btn.dataset.ctxIdx);
        btn.addEventListener('click', () => {
            _closeCtxMenu();
            try { items[idx]._fn && items[idx]._fn(); } catch(e) { console.error(e); }
        });
    });

    // Backdrop
    let backdrop = document.getElementById('msg-ctx-backdrop');
    if (!backdrop) {
        backdrop = document.createElement('div');
        backdrop.id = 'msg-ctx-backdrop';
        backdrop.style.cssText = 'position:fixed;inset:0;z-index:9998;backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px);background:rgba(0,0,0,0.25);';
        backdrop.addEventListener('click', _closeCtxMenu);
        document.body.appendChild(backdrop);
    }

    // Подсветка сообщения
    document.querySelectorAll('.message.ctx-active').forEach(m => m.classList.remove('ctx-active'));
    msgEl.classList.add('ctx-active');

    // Позиционирование
    menu.style.display = 'block';
    const vw = window.innerWidth, vh = window.innerHeight;
    const mw = 200, mh = items.length * 48;
    let left = x, top = y;
    if (left + mw > vw - 8) left = vw - mw - 8;
    if (top + mh > vh - 8) top = y - mh;
    if (top < 8) top = 8;
    menu.style.left = left + 'px';
    menu.style.top = top + 'px';
    menu.classList.add('visible');

    // Вибрация на мобильных
    if (navigator.vibrate) navigator.vibrate(30);
}

function _closeCtxMenu() {
    const menu = document.getElementById('msg-ctx-menu');
    if (menu) { menu.classList.remove('visible'); menu.style.display = 'none'; }
    // Убираем backdrop
    const backdrop = document.getElementById('msg-ctx-backdrop');
    if (backdrop) backdrop.remove();
    // Убираем подсветку
    document.querySelectorAll('.message.ctx-active').forEach(m => m.classList.remove('ctx-active'));
    setTimeout(() => { _ctxSuppressClick = false; }, 50);
}

// Переподключаем обработчики после перерисовки сообщений
function _reattachCtxMenu() {
    const container = document.getElementById('messages-container');
    if (!container) return;
    container.removeEventListener('touchstart', _onMsgTouchStart);
    container.removeEventListener('touchend', _onMsgTouchEnd);
    container.removeEventListener('touchmove', _onMsgTouchEnd);
    container.removeEventListener('contextmenu', _onMsgContextMenu);
    container.addEventListener('touchstart', _onMsgTouchStart, { passive: true });
    container.addEventListener('touchend', _onMsgTouchEnd);
    container.addEventListener('touchmove', _onMsgTouchEnd, { passive: true });
    container.addEventListener('contextmenu', _onMsgContextMenu);
}

window._closeCtxMenu = _closeCtxMenu;
window._reattachCtxMenu = _reattachCtxMenu;

// ── Reply (ответ на сообщение) ───────────────────────────────────────────────
let _replyToId = null;
let _replyToText = '';

function _startReply(msgId, text, senderName) {
    _replyToId = msgId;
    _replyToText = text;
    let bar = document.getElementById('reply-bar');
    if (!bar) {
        bar = document.createElement('div');
        bar.id = 'reply-bar';
        bar.style.cssText = 'display:flex;align-items:center;gap:10px;padding:8px 14px;background:var(--bg-secondary);border-top:1px solid var(--border-color);font-size:13px;';
        const form = document.getElementById('message-form');
        form.parentNode.insertBefore(bar, form);
    }
    const preview = text.length > 60 ? text.slice(0, 60) + '…' : text;
    bar.innerHTML = `
        <i class="fas fa-reply" style="color:#667eea;flex-shrink:0;"></i>
        <div style="flex:1;min-width:0;">
            <div style="color:#667eea;font-weight:600;font-size:12px;">${escapeHtml(senderName)}</div>
            <div style="color:var(--text-secondary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(preview)}</div>
        </div>
        <button onclick="_cancelReply()" style="background:none;border:none;cursor:pointer;color:var(--text-secondary);font-size:18px;padding:0 4px;">&times;</button>
    `;
    bar.style.display = 'flex';
    document.getElementById('message-input').focus();
}

function _cancelReply() {
    _replyToId = null;
    _replyToText = '';
    const bar = document.getElementById('reply-bar');
    if (bar) bar.style.display = 'none';
}
window._startReply = _startReply;
window._cancelReply = _cancelReply;

// ── Swipe to reply (mobile) ──────────────────────────────────────────────────
function _attachSwipeReplyEl(el) {
    let startX = 0, startY = 0, dx = 0;
    let triggered = false;
    const THRESHOLD = 60; // px to trigger reply

    // Double-tap for quick 👍 reaction
    let lastTap = 0;
    el.addEventListener('touchend', function(tapEv) {
        const now = Date.now();
        if (now - lastTap < 300) {
            // Double tap detected
            const msgId = parseInt(el.getAttribute('data-message-id'));
            const isGroup = el.dataset.isGroup === '1';
            sendReaction(msgId, '👍', isGroup);
            if (navigator.vibrate) navigator.vibrate(20);
            tapEv.preventDefault();
        }
        lastTap = now;
    }, { passive: false });

    el.addEventListener('touchstart', function(e) {
        startX = e.touches[0].clientX;
        startY = e.touches[0].clientY;
        dx = 0;
        triggered = false;
        el.style.transition = 'none';
    }, { passive: true });

    el.addEventListener('touchmove', function(e) {
        dx = e.touches[0].clientX - startX;
        const dy = e.touches[0].clientY - startY;
        // Only horizontal swipe
        if (Math.abs(dy) > Math.abs(dx)) return;
        const isMine = el.getAttribute('data-is-mine') === '1';
        // Mine: swipe right (dx > 0), theirs: swipe left (dx < 0)
        const validDir = isMine ? dx > 0 : dx < 0;
        if (!validDir) return;
        const shift = Math.min(Math.abs(dx), THRESHOLD) * (isMine ? 1 : -1);
        el.style.transform = `translateX(${shift}px)`;
        if (Math.abs(dx) >= THRESHOLD && !triggered) {
            triggered = true;
            if (navigator.vibrate) navigator.vibrate(30);
        }
    }, { passive: true });

    el.addEventListener('touchend', function() {
        el.style.transition = 'transform 0.2s ease';
        el.style.transform = 'translateX(0)';
        if (triggered) {
            const msgId = parseInt(el.getAttribute('data-message-id'));
            const contentEl = el.querySelector('.message-content');
            const text = contentEl ? contentEl.textContent.trim() : '';
            const isMine = el.getAttribute('data-is-mine') === '1';
            let senderName = isMine ? (document.getElementById('current-user-display-name')?.textContent || 'Вы') : (document.querySelector('.chat-username')?.textContent || '');
            _startReply(msgId, text, senderName);
        }
    }, { passive: true });
}

function _attachSwipeReply(container) {
    container.querySelectorAll('.message').forEach(el => {
        // Avoid double-attaching
        if (!el.dataset.swipeAttached) {
            el.dataset.swipeAttached = '1';
            _attachSwipeReplyEl(el);
        }
    });
}
window._attachSwipeReply = _attachSwipeReply;

function scrollToMsg(msgId) {
    const el = document.querySelector(`[data-message-id="${msgId}"]`);
    if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.classList.add('msg-highlight');
        setTimeout(() => el.classList.remove('msg-highlight'), 1500);
    }
}

// Рендер контента сообщения бота с кнопками
function renderBotContent(text) {
    // Экранируем текст и подсвечиваем @упоминания
    const escaped = escapeHtml(text || '').replace(/\n/g, '<br>');
    return escaped.replace(/@(\w+)/g, '<span class="mention-highlight">@$1</span>');
}

function renderBotButtons(buttons) {
    if (!buttons || !buttons.length) return '';
    return '<div class="bot-buttons">' +
        buttons.map(b => {
            if (!b.label) return '';
            if (b.url) {
                return `<a class="bot-btn" href="${escapeHtml(b.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(b.label)}</a>`;
            }
            const cmd = (b.reply || '').replace(/'/g, "\\'");
            return `<button class="bot-btn" onclick="window.sendBotCommand('${cmd}')">${escapeHtml(b.label)}</button>`;
        }).join('') +
    '</div>';
}

function sendBotCommand(cmd) {
    if (!cmd) return;
    _doSendText(cmd);
}
window.sendBotCommand = sendBotCommand;

let _isSending = false;

async function _doSendText(content) {
    if (!content || (!currentChatUserId && !currentGroupId)) return;
    if (_isSending) return;
    _isSending = true;
    const input = document.getElementById('message-input');
    // Сбрасываем typing
    hideTypingIndicator();
    if (_isTyping) {
        _isTyping = false;
        clearTimeout(_typingTimer);
        if (socket && socket.connected) {
            if (currentChatUserId) socket.emit('typing_stop', {to_user_id: currentChatUserId});
            else if (currentGroupId) socket.emit('typing_stop', {group_id: currentGroupId});
        }
    }
    try {
        if (currentGroupId) {
            const res = await fetch(`/groups/${currentGroupId}/send`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content })
            });
            const d = await res.json();
            if (d.success && input) input.value = '';
            else if (d.error) showError(d.error);
        } else {
            const res = await fetch('/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ receiver_id: currentChatUserId, content })
            });
            const d = await res.json();
            if (d.success && input) input.value = '';
            else if (d.error === 'spam_blocked') showSpamblockModal(d.until);
            else if (d.error === 'premium_required') showPremiumModal(d.message);
        }
    } catch (err) {
        showError('Не удалось отправить сообщение');
    } finally {
        _isSending = false;
    }
}

// Загрузка объединенного списка чатов и групп
async function loadAllChats() {
    try {
        // Загружаем пользователей и группы параллельно
        const [usersResponse, groupsResponse] = await Promise.all([
            fetch('/users'),
            fetch('/groups')
        ]);
        
        if (!usersResponse.ok || !groupsResponse.ok) {
            throw new Error('Ошибка загрузки');
        }
        
        const usersData = await usersResponse.json();
        const groupsData = await groupsResponse.json();
        
        // Объединяем чаты и группы
        const allChats = [
            ...usersData.users.map(u => ({ 
                ...u, 
                type: 'user',
                // Для сортировки используем текущее время если нет last_message_time
                sort_time: u.last_message_time || new Date(0).toISOString()
            })),
            ...groupsData.groups.map(g => ({ 
                ...g, 
                type: 'group',
                // Для сортировки используем текущее время если нет last_message_time
                sort_time: g.last_message_time || new Date(0).toISOString()
            }))
        ];
        
        // Сортируем по времени последнего сообщения (новые сверху)
        allChats.sort((a, b) => {
            // Преобразуем время в сравнимый формат
            const timeA = a.sort_time;
            const timeB = b.sort_time;
            
            if (timeA > timeB) return -1;
            if (timeA < timeB) return 1;
            return 0;
        });
        
        displayAllChats(allChats);
    } catch (error) {
        console.error('Ошибка загрузки чатов:', error);
        showError('Не удалось загрузить чаты');
    }
}

// Отображение объединенного списка
function displayAllChats(chats) {
    const chatsList = document.getElementById('chats-list');

    // Элемент "Избранное" всегда первый
    const favHtml = `
        <div class="chat-item" id="favorites-chat-item" onclick="openFavorites()" style="cursor:pointer;">
            <div class="chat-avatar" style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);display:flex;align-items:center;justify-content:center;color:#fff;font-size:18px;flex-shrink:0;">
                <i class="fas fa-bookmark"></i>
            </div>
            <div class="chat-info">
                <div class="chat-name">Избранное</div>
                <div class="chat-preview" style="color:var(--text-secondary);font-size:13px;">Сохранённые сообщения</div>
            </div>
        </div>`;
    
    if (!chats || chats.length === 0) {
        chatsList.innerHTML = favHtml + `
            <div class="no-chats">
                <i class="fas fa-comment-slash"></i>
                <p>У вас пока нет чатов</p>
                <p class="hint">Найдите пользователя или создайте группу</p>
            </div>`;
        return;
    }
    
    let html = favHtml;
    chats.forEach(chat => {
        if (chat.type === 'user') {
            // Пользователь
            let avatarStyle;
            let avatarContent;
            
            if (chat.avatar_url) {
                avatarStyle = `background-image: url('${chat.avatar_url}'); background-size: cover; background-position: center; background-color: ${chat.avatar_color || '#667eea'};`;
                avatarContent = '';
            } else {
                avatarStyle = `background-color: ${chat.avatar_color || '#667eea'};`;
                avatarContent = chat.avatar_letter || '?';
            }
            
            const verifiedBadge = chat.is_verified ? '<i class="fas fa-check-circle" style="color: #667eea; margin-left: 5px; font-size: 14px;"></i>' : '';
            const botBadge = chat.is_bot ? '<span style="background:linear-gradient(135deg,#667eea,#764ba2);color:white;font-size:9px;padding:1px 6px;border-radius:8px;font-weight:700;margin-left:5px;vertical-align:middle;">BOT</span>' : '';
            
            // Последнее сообщение (если есть)
            let lastMessageText = chat.last_message || 'Нет сообщений';
            
            // Время последнего сообщения
            let timeText = formatMsgTime(chat.last_message_time);
            
            // Счетчик непрочитанных сообщений (не показываем если чат открыт или был открыт)
            const isCurrentChat = chat.type === 'user' && openedChats.has(chat.id);
            const unreadBadge = chat.unread_count > 0 && !isCurrentChat ? `
                <div class="unread-badge">${chat.unread_count}</div>
            ` : '';
            
            html += `
                <div class="chat-item user-chat-item" data-user-id="${chat.id}" data-username="${escapeHtml(chat.display_name || chat.username)}" data-avatar-color="${chat.avatar_color}" data-avatar-letter="${chat.avatar_letter}" data-is-bot="${chat.is_bot ? 'true' : 'false'}">
                    <div class="chat-avatar" style="${avatarStyle}">
                        ${avatarContent}
                    </div>
                    <div class="chat-info">
                        <div class="chat-username">${escapeHtml(chat.display_name || chat.username)}${verifiedBadge}${botBadge}</div>
                        <div class="chat-last-message" style="color: #a0aec0; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(lastMessageText)}</div>
                    </div>
                    ${unreadBadge}
                    <div class="chat-time" style="color: #a0aec0; font-size: 12px; white-space: nowrap;">${timeText}</div>
                </div>
            `;
        } else {
            // Группа/Канал
            let avatarStyle;
            let avatarContent;
            
            if (chat.avatar_url) {
                avatarStyle = `background-image: url('${chat.avatar_url}'); background-size: cover; background-position: center; background-color: ${chat.avatar_color || '#667eea'};`;
                avatarContent = '';
            } else {
                avatarStyle = `background-color: ${chat.avatar_color || '#667eea'};`;
                avatarContent = chat.avatar_letter || chat.name[0].toUpperCase();
            }
            
            const groupType = chat.is_channel ? 'Канал' : 'Группа';
            const groupIcon = chat.is_channel ? 'fa-bullhorn' : 'fa-users';
            
            // Последнее сообщение (если есть)
            let lastMessageText = chat.last_message || 'Нет сообщений';
            
            // Время последнего сообщения
            let timeText = formatMsgTime(chat.last_message_time);
            
            // Счетчик непрочитанных сообщений (не показываем если группа открыта)
            const isCurrentGroup = chat.type === 'group' && chat.id === currentGroupId;
            const unreadBadge = chat.unread_count > 0 && !isCurrentGroup ? `
                <div class="unread-badge">${chat.unread_count}</div>
            ` : '';
            
            html += `
                <div class="chat-item group-chat-item" data-group-id="${chat.id}" data-group-name="${escapeHtml(chat.name)}">
                    <div class="chat-avatar" style="${avatarStyle}">
                        ${avatarContent}
                    </div>
                    <div class="chat-info">
                        <div class="chat-username">
                            <i class="fas ${groupIcon}" style="font-size: 12px; margin-right: 4px; color: #a0aec0;"></i>
                            ${escapeHtml(chat.name)}
                        </div>
                        <div class="chat-last-message" style="color: #a0aec0; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(lastMessageText)}</div>
                    </div>
                    ${unreadBadge}
                    <div class="chat-time" style="color: #a0aec0; font-size: 12px; white-space: nowrap;">${timeText}</div>
                </div>
            `;
        }
    });
    
    chatsList.innerHTML = html;
    
    // Добавляем обработчики событий для чатов (event delegation для мобильных устройств)
    setupChatItemListeners();
}

// Настройка обработчиков событий для элементов чата
function setupChatItemListeners() {
    const chatsList = document.getElementById('chats-list');
    if (!chatsList) return;
    
    // Удаляем старый обработчик если есть
    const oldHandler = chatsList._clickHandler;
    if (oldHandler) {
        chatsList.removeEventListener('click', oldHandler);
        chatsList.removeEventListener('touchend', oldHandler);
    }
    
    // Touch scroll detection — не открывать чат если был скролл
    let _touchStartY = 0;
    let _touchMoved = false;
    chatsList.addEventListener('touchstart', function(e) {
        _touchStartY = e.touches[0].clientY;
        _touchMoved = false;
    }, { passive: true });
    chatsList.addEventListener('touchmove', function(e) {
        if (Math.abs(e.touches[0].clientY - _touchStartY) > 8) _touchMoved = true;
    }, { passive: true });

    // Создаем новый обработчик
    const clickHandler = function(e) {
        // На тач-устройствах игнорируем если был скролл
        if (e.type === 'touchend' && _touchMoved) return;

        // Находим ближайший chat-item
        const chatItem = e.target.closest('.chat-item');
        if (!chatItem) return;
        
        // Проверяем тип чата
        if (chatItem.classList.contains('user-chat-item')) {
            // Открываем чат с пользователем
            const userId = parseInt(chatItem.dataset.userId);
            const username = chatItem.dataset.username;
            const avatarColor = chatItem.dataset.avatarColor;
            const avatarLetter = chatItem.dataset.avatarLetter;
            openChat(userId, username, avatarColor, avatarLetter);
        } else if (chatItem.classList.contains('group-chat-item')) {
            // Открываем группу
            const groupId = parseInt(chatItem.dataset.groupId);
            const groupName = chatItem.dataset.groupName;
            openGroup(groupId, groupName);
        }
    };
    
    // Сохраняем ссылку на обработчик
    chatsList._clickHandler = clickHandler;
    
    // Добавляем обработчики для клика и тача
    chatsList.addEventListener('click', clickHandler);
    chatsList.addEventListener('touchend', clickHandler);
}

// Загрузка списка чатов (старая функция для совместимости)
async function loadChats() {
    await loadAllChats();
}

// Отображение списка чатов
function displayChats(users) {
    const chatsList = document.getElementById('chats-list');
    
    console.log('=== DISPLAY CHATS ===');
    console.log('Users received:', users);
    console.log('Users count:', users ? users.length : 0);
    
    if (!users || users.length === 0) {
        chatsList.innerHTML = `
            <div class="no-chats">
                <i class="fas fa-comment-slash"></i>
                <p>У вас пока нет чатов</p>
                <p class="hint">Найдите пользователя для начала общения</p>
            </div>
        `;
        return;
    }
    
    let html = '';
    users.forEach((user, index) => {
        console.log(`User ${index}:`, {
            id: user.id,
            username: user.username,
            display_name: user.display_name,
            avatar_url: user.avatar_url,
            avatar_letter: user.avatar_letter,
            avatar_color: user.avatar_color,
            is_verified: user.is_verified
        });
        
        // Определяем стиль аватарки
        let avatarStyle;
        let avatarContent;
        
        if (user.avatar_url) {
            avatarStyle = `background-image: url('${user.avatar_url}'); background-size: cover; background-position: center; background-color: ${user.avatar_color || '#667eea'};`;
            avatarContent = '';
            console.log(`  → Using image: ${user.avatar_url}`);
        } else {
            avatarStyle = `background-color: ${user.avatar_color || '#667eea'};`;
            avatarContent = user.avatar_letter || '?';
            console.log(`  → Using letter: ${avatarContent} with color: ${user.avatar_color}`);
        }
        
        // Галочка верификации
        const verifiedBadge = user.is_verified ? '<i class="fas fa-check-circle" style="color: #667eea; margin-left: 5px; font-size: 14px;"></i>' : '';
        console.log(`  → Verified badge: ${user.is_verified ? 'YES' : 'NO'}`);
        
        // Определяем статус
        let statusText = 'Offline';
        let statusColor = '#a0aec0';
        if (user.is_bot) {
            statusText = 'Бот';
            statusColor = '#667eea';
        } else if (user.is_online) {
            statusText = 'Online';
            statusColor = '#38a169';
        } else if (user.last_seen) {
            statusText = `был(а) в сети ${formatLastSeen(user.last_seen)}`;
        }
        
        html += `
            <div class="chat-item user-chat-item" data-user-id="${user.id}" data-username="${escapeHtml(user.display_name || user.username)}" data-avatar-color="${user.avatar_color}" data-avatar-letter="${user.avatar_letter}">
                <div class="chat-avatar" style="${avatarStyle}">
                    ${avatarContent}
                </div>
                <div class="chat-info">
                    <div class="chat-username">${escapeHtml(user.display_name || user.username)}${verifiedBadge}</div>
                    <div class="user-status ${user.is_online ? 'online' : 'offline'}" style="color: ${statusColor};">${statusText}</div>
                </div>
                <div class="chat-time"></div>
            </div>
        `;
    });
    
    console.log('=== HTML GENERATED ===');
    console.log('Setting innerHTML...');
    chatsList.innerHTML = html;
    console.log('Done!');
    
    // Добавляем обработчики событий
    setupChatItemListeners();
}

// Поиск пользователей
async function handleSearch(e) {
    const query = e.target.value.trim();
    const resultsContainer = document.getElementById('search-results');
    
    // Очищаем предыдущий таймаут
    if (searchTimeout) {
        clearTimeout(searchTimeout);
    }
    
    // Скрываем результаты если запрос пустой
    if (!query) {
        resultsContainer.style.display = 'none';
        resultsContainer.innerHTML = '';
        return;
    }
    
    // Устанавливаем таймаут для предотвращения частых запросов
    searchTimeout = setTimeout(async () => {
        try {
            const response = await fetch(`/search?q=${encodeURIComponent(query)}`);
            if (!response.ok) throw new Error('Ошибка поиска');
            
            const data = await response.json();
            displaySearchResults(data);
        } catch (error) {
            console.error('Ошибка поиска:', error);
            resultsContainer.innerHTML = '<div class="search-result-item">Ошибка поиска</div>';
            resultsContainer.style.display = 'block';
        }
    }, 300);
}

// Отображение результатов поиска
function displaySearchResults(data) {
    const resultsContainer = document.getElementById('search-results');
    
    // Если data это массив (старый формат), преобразуем
    if (Array.isArray(data)) {
        data = { users: data, groups: [] };
    }
    
    const users = data.users || [];
    const groups = data.groups || [];
    
    if (users.length === 0 && groups.length === 0) {
        resultsContainer.innerHTML = '<div class="search-result-item">Ничего не найдено</div>';
        resultsContainer.style.display = 'block';
        return;
    }
    
    let html = '';
    
    // Отображаем пользователей
    if (users.length > 0) {
        html += '<div style="padding: 8px 12px; font-size: 12px; color: #a0aec0; font-weight: 600;">ПОЛЬЗОВАТЕЛИ</div>';
        users.forEach(user => {
            const avatarStyle = user.avatar_url 
                ? `background-image: url('${user.avatar_url}'); background-size: cover; background-position: center;`
                : `background: ${user.avatar_color}`;
            const avatarContent = user.avatar_url ? '' : user.avatar_letter;
            
            html += `
                <div class="search-result-item" onclick="openChat(${user.id}, '${escapeHtml(user.display_name || user.username)}', '${user.avatar_color}', '${user.avatar_letter}')">
                    <div class="search-result-avatar" style="${avatarStyle}">
                        ${avatarContent}
                    </div>
                    <div class="search-result-info">
                        <h4>${escapeHtml(user.display_name || user.username)}${user.is_bot ? ' <span style="background:linear-gradient(135deg,#667eea,#764ba2);color:white;font-size:9px;padding:1px 6px;border-radius:8px;font-weight:700;vertical-align:middle;">BOT</span>' : ''}</h4>
                        <p>${user.bio ? escapeHtml(user.bio) : 'Нажмите для начала чата'}</p>
                    </div>
                </div>
            `;
        });
    }
    
    // Отображаем группы
    if (groups.length > 0) {
        html += '<div style="padding: 8px 12px; font-size: 12px; color: #a0aec0; font-weight: 600; margin-top: 8px;">ГРУППЫ И КАНАЛЫ</div>';
        groups.forEach(group => {
            const groupUsername = group.username ? `@${group.username}` : '';
            const groupType = group.is_channel ? 'Канал' : 'Группа';
            const avatarStyle = group.avatar_url 
                ? `background-image: url('${group.avatar_url}'); background-size: cover; background-position: center;`
                : `background: ${group.avatar_color}`;
            const avatarContent = group.avatar_url ? '' : group.avatar_letter;
            
            html += `
                <div class="search-result-item" onclick="joinGroupFromSearch(${group.id})">
                    <div class="search-result-avatar" style="${avatarStyle}">
                        ${avatarContent}
                    </div>
                    <div class="search-result-info">
                        <h4>${escapeHtml(group.name)} ${groupUsername ? '<span style="color: #a0aec0; font-size: 13px;">' + groupUsername + '</span>' : ''}</h4>
                        <p>${group.description ? escapeHtml(group.description) : groupType + ' • ' + group.members_count + ' участников'}</p>
                    </div>
                    <button class="btn btn-primary" style="padding: 6px 12px; font-size: 13px; margin-left: auto;" onclick="event.stopPropagation(); joinGroupFromSearch(${group.id})">
                        <i class="fas fa-sign-in-alt"></i> Войти
                    </button>
                </div>
            `;
        });
    }
    
    resultsContainer.innerHTML = html;
    resultsContainer.style.display = 'block';
    
    // Скрываем результаты при клике вне
    document.addEventListener('click', function hideResults(e) {
        if (!resultsContainer.contains(e.target) && e.target.id !== 'search-input') {
            resultsContainer.style.display = 'none';
            document.removeEventListener('click', hideResults);
        }
    });
}

// Открытие чата с пользователем
async function openChat(userId, username) {
    console.log('[openChat] called, userId=', userId, 'username=', username);

    // Проверяем репутацию — показываем предупреждение если < 50%
    // Пропускаем если уже видели предупреждение для этого пользователя
    const _warnKey = `rep_warned_${userId}`;
    if (!localStorage.getItem(_warnKey)) {
        try {
            const repResp = await fetch(`/api/user/${userId}/reputation`);
            if (repResp.ok) {
                const repData = await repResp.json();
                if (repData.reputation < 50) {
                    const proceed = await new Promise(resolve => {
                        const modal = document.createElement('div');
                        modal.className = 'modal active';
                        modal.innerHTML = `
                            <div class="modal-content" style="max-width:360px;text-align:center;">
                                <div class="modal-header" style="justify-content:center;border-bottom:none;padding-bottom:0;">
                                    <h2 style="color:#e53e3e;"><i class="fas fa-exclamation-triangle"></i> Внимание!</h2>
                                </div>
                                <div class="modal-body" style="padding-top:10px;">
                                    <p style="font-size:15px;margin-bottom:8px;">Будьте осторожны!</p>
                                    <p style="color:var(--text-secondary);font-size:14px;margin-bottom:16px;">У этого пользователя репутация ниже 50%</p>
                                    <div class="reputation-bar-bg" style="margin-bottom:20px;">
                                        <div class="reputation-bar-fill" style="width:${repData.reputation}%;background:${repData.color};"></div>
                                    </div>
                                    <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap;">
                                        <button class="btn btn-primary" id="_rep_ok_btn" style="min-width:120px;">Хорошо</button>
                                        <button class="btn btn-secondary" id="_rep_no_btn" style="min-width:120px;">Лучше не надо</button>
                                    </div>
                                    <p style="font-size:11px;color:var(--text-secondary);margin-top:12px;">Нажав «Хорошо», вы больше не увидите это предупреждение</p>
                                </div>
                            </div>
                        `;
                        document.body.appendChild(modal);
                        document.getElementById('_rep_ok_btn').onclick = () => {
                            localStorage.setItem(_warnKey, '1');
                            modal.remove();
                            resolve(true);
                        };
                        document.getElementById('_rep_no_btn').onclick = () => {
                            modal.remove();
                            resolve(false);
                        };
                    });
                    if (!proceed) return;
                }
            }
        } catch(e) {}
    }

    // Сохраняем черновик текущего чата перед переключением
    _saveDraft();

    _cancelReply();
    if (window._abortRecordingOnChatSwitch) window._abortRecordingOnChatSwitch();
    currentChatUserId = userId;
    _favoritesOpen = false;
    openedChats.add(userId);
    _isSending = false;

    // Принудительно показываем кнопки личного чата
    ['call-btn', 'clear-history-btn'].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.classList.remove('bot-hidden');
            el.setAttribute('data-visible', '1');
            el.style.display = 'flex';
        }
    });
    // Скрываем кнопку группового звонка в личном чате
    const gcBtnHide = document.getElementById('group-call-btn');
    if (gcBtnHide) gcBtnHide.style.display = 'none';
    const streamBtnHide = document.getElementById('_stream-live-btn');
    if (streamBtnHide) streamBtnHide.style.display = 'none';

    // Сразу показываем форму ввода (личный чат — не канал)
    updateMessageInputVisibility(false, true);

    // Убираем кнопку платного поста (она только для каналов)
    const paidPostBtn = document.getElementById('paid-post-btn');
    if (paidPostBtn) paidPostBtn.remove();
    
    // Восстанавливаем кнопки личного чата (скрытые при просмотре группы)
    const addContactBtn = document.querySelector('.chat-header .icon-btn[onclick="addContactFromChat()"]');
    if (addContactBtn) addContactBtn.style.display = 'flex';
    const settingsBtn = document.getElementById('group-settings-btn');
    if (settingsBtn) settingsBtn.style.display = 'none';

    // Обновляем UI
    document.getElementById('chat-welcome').style.display = 'none';
    document.getElementById('chat-active').style.display = 'flex';
    document.getElementById('chat-username').textContent = username;

    // Показываем кнопки личного чата (уже показаны в начале функции)
    const _chatArea = document.getElementById('chat-area');
    if (_chatArea) _chatArea.classList.add('personal-chat-open');
    // На мобильных устройствах скрываем sidebar и показываем chat-area
    const sidebar = document.getElementById('sidebar');
    const chatArea = document.getElementById('chat-area');
    const backBtn = document.getElementById('back-to-chats-btn');
    
    if (sidebar && chatArea) {
        if (window.innerWidth <= 768) {
            sidebar.classList.add('mobile-hidden');
            chatArea.classList.add('mobile-active');
            if (backBtn) backBtn.style.display = 'flex';
        } else {
            if (backBtn) backBtn.style.display = 'none';
        }
    }
    
    // Сбрасываем активный класс у всех чатов
    document.querySelectorAll('.chat-item, .group-item').forEach(item => {
        item.classList.remove('active');
    });
    
    // Добавляем активный класс к выбранному чату
    const chatItem = document.querySelector(`.chat-item[data-user-id="${userId}"]`);
    if (chatItem) {
        chatItem.classList.add('active');
        // Сразу убираем бейдж визуально
        const badge = chatItem.querySelector('.unread-badge');
        if (badge) badge.remove();
    }

    // Сразу отмечаем как прочитанные (до загрузки сообщений)
    markChatAsRead(userId);
    
    // Загружаем информацию о пользователе для обновления аватарки в заголовке
    try {
        const userResponse = await fetch(`/api/user/${userId}`);
        
        if (userResponse.ok) {
            const userData = await userResponse.json();
            
            // Обновляем аватарку в заголовке чата
            const chatAvatar = document.getElementById('chat-header-avatar');
            
            if (chatAvatar) {
                if (userData.avatar_url) {
                    // Если есть изображение
                    chatAvatar.style.backgroundImage = `url('${userData.avatar_url}')`;
                    chatAvatar.style.backgroundSize = 'cover';
                    chatAvatar.style.backgroundPosition = 'center';
                    chatAvatar.style.backgroundColor = userData.avatar_color;
                    chatAvatar.textContent = '';
                } else {
                    // Если нет изображения - показываем букву
                    chatAvatar.style.backgroundImage = 'none';
                    chatAvatar.style.backgroundColor = userData.avatar_color;
                    chatAvatar.style.color = 'white';
                    chatAvatar.textContent = userData.avatar_letter;
                }
            }
            
            // Обновляем имя с галочкой верификации
            const chatUsername = document.getElementById('chat-username');
            if (chatUsername) {
                const verifiedBadge = userData.is_verified ? ' <i class="fas fa-check-circle" style="color: #667eea;"></i>' : '';
                const botBadge = userData.is_bot ? ' <span style="background:linear-gradient(135deg,#667eea,#764ba2);color:white;font-size:10px;padding:1px 7px;border-radius:8px;font-weight:700;vertical-align:middle;"><i class="fas fa-robot"></i> BOT</span>' : '';
                chatUsername.innerHTML = escapeHtml(userData.display_name || userData.username) + verifiedBadge + botBadge;
            }
            
            // Скрываем кнопку "Пожаловаться" для ботов
            const reportBtn = document.querySelector('.chat-header .icon-btn[onclick*="showReportModal"]');
            if (reportBtn) {
                reportBtn.style.display = userData.is_bot ? 'none' : '';
            }

            // Показываем кнопку звонка только для обычных пользователей (не ботов)
            if (userData.is_bot) {
                document.getElementById('call-btn')?.removeAttribute('data-visible');
                document.getElementById('call-btn')?.classList.add('bot-hidden');
            } else {
                document.getElementById('call-btn')?.classList.remove('bot-hidden');
            }
            // Кнопка очистки истории — всегда видна в личных чатах (управляется data-visible)
        }
    } catch (error) {
        console.error('Error loading user info:', error);
        // Fallback: кнопки управляются CSS классом personal-chat-open (уже добавлен выше)
        document.getElementById('call-btn')?.classList.remove('bot-hidden');
    }
    
    // Загружаем сообщения
    await loadMessages(userId);
    // Восстанавливаем черновик
    _restoreDraft('user_' + userId);
    
    // Показываем форму сообщений (это обычный чат, не канал)
    updateMessageInputVisibility(false, true);
    
    // Показываем кнопку добавления в чат
    currentGroupId = null;  // Сбрасываем ID группы

    // Скрываем кнопки группы
    const addMemberBtn = document.getElementById('add-member-btn');
    if (addMemberBtn) addMemberBtn.style.display = 'none';
    const groupSettingsBtn = document.getElementById('group-settings-btn');
    if (groupSettingsBtn) groupSettingsBtn.style.display = 'none';
    const groupSearchBtn = document.getElementById('group-search-btn');
    if (groupSearchBtn) groupSearchBtn.style.display = 'none';
    _currentGroupData = null;
    if (typeof updateAddToChatButton === 'function') {
        updateAddToChatButton();
    }
    
    // Загружаем закреплённое сообщение
    if (typeof loadPinnedMessage === 'function') loadPinnedMessage();

    // Фокус на поле ввода
    const msgInput = document.getElementById('message-input');
    if (msgInput) {
        msgInput.placeholder = 'Введите сообщение...';
        msgInput.focus();
    }
    
    // Скрываем результаты поиска
    document.getElementById('search-results').style.display = 'none';
    document.getElementById('search-input').value = '';
}

// Загрузка сообщений
async function loadMessages(userId) {
    try {
        const response = await fetch(`/chat/${userId}`);
        if (!response.ok) throw new Error('Ошибка загрузки сообщений');
        
        const data = await response.json();
        
        // Обновляем информацию о пользователе в заголовке
        if (data.other_user) {
            const chatUsername = document.getElementById('chat-username');
            const chatAvatar = document.querySelector('.chat-header .chat-avatar');
            const chatStatus = document.getElementById('chat-status');
            
            if (chatUsername) {
                const verifiedBadge = data.other_user.is_verified ? ' <i class="fas fa-check-circle" style="color: #667eea;"></i>' : '';
                chatUsername.innerHTML = escapeHtml(data.other_user.display_name || data.other_user.username) + verifiedBadge;
            }
            
            // Обновляем статус
            if (chatStatus) {
                if (data.other_user.is_bot) {
                    chatStatus.textContent = 'Бот';
                    chatStatus.style.color = '#667eea';
                } else if (data.other_user.is_online) {
                    chatStatus.textContent = 'Online';
                    chatStatus.style.color = '#38a169';
                } else if (data.other_user.last_seen) {
                    chatStatus.textContent = `был(а) в сети ${formatLastSeen(data.other_user.last_seen)}`;
                    chatStatus.style.color = '#a0aec0';
                } else {
                    chatStatus.textContent = 'Offline';
                    chatStatus.style.color = '#a0aec0';
                }
            }
            
            if (chatAvatar) {
                if (data.other_user.avatar_url) {
                    chatAvatar.style.backgroundImage = `url('${data.other_user.avatar_url}')`;
                    chatAvatar.style.backgroundSize = 'cover';
                    chatAvatar.style.backgroundPosition = 'center';
                    chatAvatar.style.backgroundColor = data.other_user.avatar_color;
                    chatAvatar.textContent = '';
                } else {
                    chatAvatar.style.backgroundImage = 'none';
                    chatAvatar.style.backgroundColor = data.other_user.avatar_color;
                    chatAvatar.textContent = data.other_user.avatar_letter;
                }
            }
        }
        
        // Применяем обои текущего пользователя
        applyWallpaper();
        
        displayMessages(data.messages);
        
        // Прокрутка вниз
        scrollToBottom();
    } catch (error) {
        console.error('Ошибка загрузки сообщений:', error);
        showError('Не удалось загрузить сообщения');
    }
}

// Отображение сообщений
function displayMessages(messages) {
    const container = document.getElementById('messages-container');
    // Убираем индикатор печатания при перерисовке
    hideTypingIndicator();
    
    if (!messages || messages.length === 0) {
        container.innerHTML = `
            <div class="no-messages">
                <i class="fas fa-comment-medical"></i>
                <p>Начните общение с этим пользователем</p>
                <p class="hint">Напишите первое сообщение</p>
            </div>
        `;
        return;
    }
    
    let html = '';
    messages.forEach(msg => {
        html += createMessageHTML(msg);
    });
    
    container.innerHTML = html;
    _fixStickerImages(container);
    _reattachCtxMenu();
    _attachSwipeReply(container);
}

// Создание HTML для сообщения
function createMessageHTML(msg) {
    const messageClass = msg.is_mine ? 'sent' : 'received';

    // Удалённые сообщения не показываем
    if (msg.is_deleted) return '';

    let content = '';
    
    // Альбом (множественные файлы)
    if (msg.message_type === 'album' && msg.media_files && msg.media_files.length > 0) {
        const gridClass = msg.media_files.length === 1 ? 'media-grid-1' :
                         msg.media_files.length === 2 ? 'media-grid-2' :
                         msg.media_files.length === 3 ? 'media-grid-3' :
                         msg.media_files.length === 4 ? 'media-grid-4' :
                         'media-grid-many';
        
        let mediaHtml = `<div class="message-album ${gridClass}">`;
        
        msg.media_files.forEach((file, index) => {
            if (file.media_type === 'image') {
                mediaHtml += `
                    <div class="album-item" onclick="viewImage('${file.media_url}')">
                        <img src="${file.media_url}" alt="Изображение" loading="lazy">
                    </div>
                `;
            } else if (file.media_type === 'video') {
                mediaHtml += `
                    <div class="album-item video-item" onclick="playAlbumVideo('${file.media_url}')">
                        <video src="${file.media_url}" preload="metadata"></video>
                        <div class="video-play-overlay">
                            <i class="fas fa-play"></i>
                        </div>
                    </div>
                `;
            } else {
                // Файл (документ)
                const icon = getFileIconForMessage(file.file_name);
                mediaHtml += `
                    <div class="album-item file-item">
                        <a href="${file.media_url}" download="${file.file_name}" style="text-decoration: none; color: inherit; display: flex; align-items: center; gap: 10px; padding: 10px;">
                            <i class="fas ${icon}" style="font-size: 24px; color: var(--primary-color);"></i>
                            <div style="flex: 1; overflow: hidden;">
                                <div style="font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(file.file_name)}</div>
                            </div>
                            <i class="fas fa-download" style="color: var(--primary-color);"></i>
                        </a>
                    </div>
                `;
            }
        });
        
        mediaHtml += '</div>';
        
        // Добавляем подпись если есть
        if (msg.content && msg.content !== 'Файлы') {
            mediaHtml += `<div class="message-content" style="margin-top: 8px;">${escapeHtml(msg.content)}</div>`;
        }
        
        content = mediaHtml;
    }
    // Голосовое сообщение
    else if (msg.message_type === 'voice' && msg.media_url) {
        const dur = msg.duration || 0;
        const m = String(Math.floor(dur / 60)).padStart(2, '0');
        const s = String(dur % 60).padStart(2, '0');
        const uid = 'voice_' + msg.id;
        content = `<div class="voice-message">
            <button class="voice-play-btn" onclick="playVoiceMsg(null,'${uid}',this)"><i class="fas fa-play"></i></button>
            <div class="voice-waveform" id="${uid}_bar"></div>
            <span class="voice-duration" id="${uid}_dur">${m}:${s}</span>
            <button class="voice-speed-btn" onclick="cycleVoiceSpeed('${uid}',this)" title="Скорость">1x</button>
            <audio id="${uid}" data-src="${encodeURIComponent(msg.media_url)}" onended="resetVoiceBtn('${uid}')"></audio>
        </div>`;
    }
    // Стикер
    else if (msg.message_type === 'sticker' || (msg.content && msg.content.startsWith('[sticker]'))) {
        const stickerUrl = msg.content ? msg.content.replace('[sticker]', '') : msg.media_url;
        const packId = msg.sticker_pack_id || '';
        const isAnimated = stickerUrl && stickerUrl.startsWith('data:application/json');
        if (isAnimated) {
            const uid = 'lottie_' + msg.id + '_' + Date.now();
            content = `<div class="sticker-message" data-sticker-url="${encodeURIComponent(stickerUrl)}" data-pack-id="${packId}" onclick="_onStickerClick(this)">
                <div id="${uid}" style="width:120px;height:120px;cursor:pointer;" title="Нажмите, чтобы посмотреть пак" data-lottie-src="${encodeURIComponent(stickerUrl)}"></div>
            </div>`;
        } else {
            content = `<div class="sticker-message" data-sticker-url="${encodeURIComponent(stickerUrl)}" data-pack-id="${packId}" onclick="_onStickerClick(this)">
                <img data-src="${encodeURIComponent(stickerUrl)}" alt="Стикер" style="width:120px;height:120px;object-fit:contain;cursor:pointer;border-radius:8px;" title="Нажмите, чтобы посмотреть пак">
            </div>`;
        }
    }
    // Изображение
    else if (msg.message_type === 'image' && msg.media_url) {
        const isDataUrl = msg.media_url.startsWith('data:');
        if (isDataUrl) {
            // data URL нельзя вставлять в onclick атрибут — используем data-атрибут
            content = `<img src="${msg.media_url}" class="message-image" data-msg-id="${msg.id}" onclick="viewImageById(this)" alt="Изображение" loading="lazy">`;
        } else {
            content = `<img src="${msg.media_url}" class="message-image" onclick="viewImage('${msg.media_url}')" alt="Изображение" loading="lazy">`;
        }
    }
    // Обычное текстовое сообщение
    else {
        const editedText = msg.edited_at ? ` <span class="edited-text">(изм.)</span>` : '';
        if (msg.message_type === 'poll' && msg.poll_id) {
            content = `<div class="poll-widget" id="poll-widget-${msg.poll_id}"><div class="poll-loading"><i class="fas fa-spinner fa-spin"></i> Загрузка опроса...</div></div>`;
            setTimeout(() => loadAndShowPoll(msg.poll_id, `poll-widget-${msg.poll_id}`), 50);
        } else if (msg.message_type === 'gift' && msg.gift) {
            content = renderGiftMessage(msg);
        } else {
            content = `<div class="message-content">${renderBotContent(msg.content)}${editedText}</div>`;
        }
    }

    return `
        <div class="message ${messageClass}" data-message-id="${msg.id}" data-is-mine="${msg.is_mine ? '1' : '0'}" data-is-deleted="${msg.is_deleted ? '1' : '0'}">
            ${msg.reply_to ? `<div class="reply-preview" onclick="scrollToMsg(${msg.reply_to.id})"><span class="reply-sender">${escapeHtml(msg.reply_to.sender_name)}</span><span class="reply-text">${escapeHtml((msg.reply_to.content||'').slice(0,80))}</span></div>` : ''}
            ${content}
            ${renderBotButtons(msg.bot_buttons)}
            <div class="message-time">
                ${msg.timestamp_iso ? formatMsgTime(msg.timestamp_iso) : msg.timestamp}
                ${msg.is_mine ? `<span class="msg-status" data-msg-status="${msg.id}">${msg.is_read ? '<i class="fas fa-check-double read"></i>' : '<i class="fas fa-check"></i>'}</span>` : ''}
            </div>
        </div>
    `;
}

// Вспомогательная функция для иконок файлов
function getFileIconForMessage(filename) {
    if (!filename) return 'fa-file';
    const ext = filename.split('.').pop().toLowerCase();
    if (['pdf'].includes(ext)) return 'fa-file-pdf';
    if (['doc', 'docx'].includes(ext)) return 'fa-file-word';
    if (['txt'].includes(ext)) return 'fa-file-alt';
    return 'fa-file';
}

// Прокрутка вниз
function scrollToBottom() {
    const container = document.getElementById('messages-container');
    if (container) {
        container.scrollTop = container.scrollHeight;
    }
}

// Показ ошибки или уведомления
function showError(message, type = 'error') {
    // Удаляем предыдущие уведомления
    document.querySelectorAll('.notification-toast').forEach(el => el.remove());
    
    // Создаем временное уведомление
    const toast = document.createElement('div');
    toast.className = `notification-toast ${type}`;
    
    const icon = type === 'error' ? 'exclamation-circle' : 
                 type === 'success' ? 'check-circle' : 'info-circle';
    
    toast.innerHTML = `<i class="fas fa-${icon}"></i> <span>${message}</span>`;
    
    document.body.appendChild(toast);
    
    // Удаляем через 4 секунды
    setTimeout(() => {
        toast.style.animation = 'slideOutRight 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// Алиас для совместимости
function showToast(message, type = 'info') { showError(message, type); }
// Анимация для уведомлений
const style = document.createElement('style');
style.textContent = `
    @keyframes slideOutRight {
        from { transform: translateX(0); opacity: 1; }
        to { transform: translateX(100%); opacity: 0; }
    }
`;
document.head.appendChild(style);

// Запрашиваем разрешение на браузерные уведомления
if ('Notification' in window && Notification.permission === 'default') {
    document.addEventListener('click', function _reqNotif() {
        Notification.requestPermission();
        document.removeEventListener('click', _reqNotif);
    }, { once: true });
}

// Стек тостов о новых сообщениях
const _msgToasts = [];

function showMessageNotification({ name, text, avatarUrl, avatarColor, avatarLetter, onClick }) {
    // Не показываем если вкладка активна и чат открыт — уже видно
    // (вызывающий код сам решает показывать или нет)

    // Десктопное уведомление через Electron (правый нижний угол)
    if (window.__tabletoneDesktop) {
        window.__tabletoneDesktop.notify(name, text);
    }

    // Браузерное уведомление (если вкладка не активна)
    if (document.hidden && 'Notification' in window && Notification.permission === 'granted') {
        const n = new Notification(name, {
            body: text,
            icon: avatarUrl || '/static/images/logo.png',
            tag: 'msg-' + name,
            renotify: true,
        });
        n.onclick = () => { window.focus(); n.close(); if (onClick) onClick(); };
    }

    // Убираем старые если больше 3
    while (_msgToasts.length >= 3) {
        const old = _msgToasts.shift();
        if (old && old.parentNode) old.remove();
    }

    const toast = document.createElement('div');
    toast.className = 'msg-toast';

    // Позиционируем с учётом уже существующих
    const offset = _msgToasts.length * 80;
    toast.style.top = (16 + offset) + 'px';

    // Аватарка
    let avatarHtml;
    if (avatarUrl) {
        avatarHtml = `<div class="msg-toast-avatar"><img src="${avatarUrl}" alt=""></div>`;
    } else {
        avatarHtml = `<div class="msg-toast-avatar" style="background:${avatarColor || '#667eea'}">${escapeHtml(avatarLetter || '?')}</div>`;
    }

    // Превью текста
    const preview = text ? escapeHtml(text.substring(0, 60)) : '📎 Вложение';

    toast.innerHTML = `
        ${avatarHtml}
        <div class="msg-toast-body">
            <div class="msg-toast-name">${escapeHtml(name)}</div>
            <div class="msg-toast-text">${preview}</div>
        </div>
        <button class="msg-toast-close" title="Закрыть">✕</button>
    `;

    toast.querySelector('.msg-toast-close').addEventListener('click', e => {
        e.stopPropagation();
        _dismissMsgToast(toast);
    });

    toast.addEventListener('click', () => {
        _dismissMsgToast(toast);
        if (onClick) onClick();
    });

    document.body.appendChild(toast);
    _msgToasts.push(toast);

    // Автоудаление через 5 сек
    setTimeout(() => _dismissMsgToast(toast), 5000);
}

function _dismissMsgToast(toast) {
    const idx = _msgToasts.indexOf(toast);
    if (idx !== -1) _msgToasts.splice(idx, 1);
    if (!toast.parentNode) return;
    toast.style.animation = 'msgToastOut 0.3s ease forwards';
    setTimeout(() => { toast.remove(); _repositionMsgToasts(); }, 300);
}

function _repositionMsgToasts() {
    _msgToasts.forEach((t, i) => { t.style.top = (16 + i * 80) + 'px'; });
}

// Экранирование HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Fetch с timeout
function fetchWithTimeout(url, options = {}, timeout = 10000) {
    return Promise.race([
        fetch(url, options),
        new Promise((_, reject) =>
            setTimeout(() => reject(new Error('Timeout')), timeout)
        )
    ]);
}

// Обновление содержимого сообщения (редактирование)
function updateMessageContent(messageId, content, editedAt) {
    const messageEl = document.querySelector(`[data-message-id="${messageId}"]`);
    if (!messageEl) return;
    
    const contentEl = messageEl.querySelector('.message-content');
    if (contentEl) {
        const escapedContent = escapeHtml(content);
        const editedText = editedAt ? ` <span class="edited-text">(отредактировано)</span>` : '';
        contentEl.innerHTML = `${escapedContent}${editedText}`;
    }
}

// Обновление удаленного сообщения
function updateMessageDeleted(messageId) {
    const messageEl = document.querySelector(`[data-message-id="${messageId}"]`);
    if (!messageEl) return;
    messageEl.style.transition = 'opacity 0.3s, transform 0.3s';
    messageEl.style.opacity = '0';
    messageEl.style.transform = 'scale(0.95)';
    setTimeout(() => messageEl.remove(), 300);
}

// Экспорт функций в глобальную область видимости
window.openChat = openChat;
window.showMessageMenu = showMessageMenu;
window.editMessage = editMessage;

async function clearChatHistory() {
    if (!currentChatUserId) return;
    const modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9999;display:flex;align-items:center;justify-content:center;';
    modal.innerHTML = `
        <div style="background:var(--bg-primary,#fff);color:var(--text-primary,#000);border-radius:14px;padding:24px;max-width:320px;width:90%;text-align:center;">
            <i class="fas fa-trash-alt" style="font-size:32px;color:#e53e3e;margin-bottom:12px;"></i>
            <h3 style="margin:0 0 8px;font-size:17px;">Очистить историю?</h3>
            <p style="margin:0 0 14px;color:var(--text-secondary);font-size:14px;">Сообщения будут удалены только у вас.</p>
            <label style="display:flex;align-items:center;justify-content:center;gap:8px;font-size:14px;margin-bottom:20px;cursor:pointer;">
                <input type="checkbox" id="clear-both-sides" style="width:16px;height:16px;cursor:pointer;">
                Удалить и у собеседника
            </label>
            <div style="display:flex;gap:10px;justify-content:center;">
                <button id="clear-confirm-btn" style="background:#e53e3e;color:#fff;border:none;border-radius:8px;padding:10px 22px;font-size:14px;font-weight:600;cursor:pointer;">Очистить</button>
                <button onclick="this.closest('[style*=fixed]').remove()" style="background:var(--bg-secondary);color:var(--text-primary);border:1px solid var(--border-color);border-radius:8px;padding:10px 22px;font-size:14px;font-weight:600;cursor:pointer;">Отмена</button>
            </div>
        </div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);
    document.getElementById('clear-confirm-btn').onclick = async () => {
        const bothSides = document.getElementById('clear-both-sides').checked;
        modal.remove();
        try {
            const r = await fetch(`/chat/${currentChatUserId}/clear`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({both_sides: bothSides})
            });
            const d = await r.json();
            if (d.success) {
                document.getElementById('messages-container').innerHTML = '';
                showError('История очищена', 'success');
            } else {
                showError(d.error || 'Ошибка');
            }
        } catch (e) {
            showError('Ошибка сети');
        }
    };
}

async function openChatByUsername(username) {
    console.log('[openChatByUsername] looking up:', username);
    try {
        const r = await fetch(`/api/user-by-username/${encodeURIComponent(username)}`);
        console.log('[openChatByUsername] api status:', r.status);
        if (r.ok) {
            const d = await r.json();
            console.log('[openChatByUsername] api result:', d);
            if (d && d.id) {
                openChat(d.id, d.display_name || d.username);
                return;
            }
        }
        const r2 = await fetch(`/search?q=${encodeURIComponent(username)}`);
        const d2 = await r2.json();
        console.log('[openChatByUsername] search result:', d2);
        const user = (d2.users || []).find(u => u.username === username);
        if (user) {
            openChat(user.id, user.display_name || user.username);
        } else {
            showError('Бот не найден');
        }
    } catch(e) { console.error('[openChatByUsername] error:', e); showError('Ошибка открытия чата'); }
}
window.openChatByUsername = openChatByUsername;
window.deleteMessage = deleteMessage;
window.setTheme = setTheme;
window.loadTheme = loadTheme;
// Настройка мобильной навигации
function setupMobileNavigation() {
    const isMobile = window.innerWidth <= 768;
    
    if (isMobile) {
        // Добавляем кнопку назад, если её еще нет
        const chatHeader = document.querySelector('.chat-header');
        if (chatHeader && !chatHeader.querySelector('.mobile-back-btn')) {
            const backButton = document.createElement('button');
            backButton.className = 'mobile-back-btn';
            backButton.innerHTML = '<i class="fas fa-arrow-left"></i>';
            backButton.onclick = showChatList;
            
            const chatUserInfo = chatHeader.querySelector('.chat-user-info');
            chatHeader.insertBefore(backButton, chatUserInfo);
        }
    }
}

// Показать список чатов на мобильных
function showChatList() {
    if (window.innerWidth <= 768) {
        const sidebar = document.querySelector('.sidebar');
        const chatArea = document.getElementById('chat-area');
        
        sidebar.classList.remove('mobile-hidden');
        chatArea.classList.remove('mobile-active');
        
        currentChatUserId = null;
    }
}

// Обновленная функция открытия чата для мобильных
window.openChat = async function(userId, displayName, avatarColor, avatarLetter) {
    _cancelReply();
    if (window._abortRecordingOnChatSwitch) window._abortRecordingOnChatSwitch();
    currentChatUserId = userId;
    currentGroupId = null; // Сбрасываем текущую группу
    openedChats.add(userId); // Помечаем чат как открытый — бейдж не показываем

    // Показываем кнопки личного чата
    ['call-btn', 'clear-history-btn'].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.classList.remove('bot-hidden');
            el.setAttribute('data-visible', '1');
            el.style.display = 'flex';
        }
    });
    document.getElementById('chat-area')?.classList.add('personal-chat-open');

    // Сразу показываем форму ввода и убираем кнопку платного поста
    updateMessageInputVisibility(false, true);
    const paidPostBtn = document.getElementById('paid-post-btn');
    if (paidPostBtn) paidPostBtn.remove();
    
    const isMobile = window.innerWidth <= 768;
    
    // Восстанавливаем кнопку "Пожаловаться" (скроем позже если бот)
    const reportBtn = document.querySelector('.chat-header .icon-btn[onclick*="showReportModal"]');
    if (reportBtn) reportBtn.style.display = '';
    
    // Обновляем UI
    document.getElementById('chat-welcome').style.display = 'none';
    document.getElementById('chat-active').style.display = 'flex';
    document.getElementById('chat-username').textContent = displayName;
    
    // Обновляем аватар в заголовке чата
    const chatAvatar = document.querySelector('.chat-header .chat-avatar');
    if (chatAvatar) {
        chatAvatar.style.background = avatarColor;
        chatAvatar.innerHTML = avatarLetter;
    }
    
    // Скрываем кнопку добавления участников для личных чатов
    const addMemberBtn = document.getElementById('add-member-btn');
    if (addMemberBtn) {
        addMemberBtn.style.display = 'none';
    }
    
    // Скрываем кнопку настроек группы
    const groupSettingsBtn = document.getElementById('group-settings-btn');
    if (groupSettingsBtn) groupSettingsBtn.style.display = 'none';
    const groupSearchBtn2 = document.getElementById('group-search-btn');
    if (groupSearchBtn2) groupSearchBtn2.style.display = 'none';
    _currentGroupData = null;
    
    // Сбрасываем активный класс у всех чатов
    document.querySelectorAll('.chat-item').forEach(item => {
        item.classList.remove('active');
    });
    
    // Добавляем активный класс к выбранному чату
    const chatItem = document.querySelector(`.chat-item[data-user-id="${userId}"]`);
    if (chatItem) {
        chatItem.classList.add('active');
        // Убираем бейдж непрочитанных
        const badge = chatItem.querySelector('.unread-badge');
        if (badge) badge.remove();
        
        // Получаем статус из списка чатов
        const statusEl = chatItem.querySelector('.user-status');
        if (statusEl) {
            const isOnline = statusEl.classList.contains('online');
            const chatStatus = document.getElementById('chat-status');
            if (chatStatus) {
                chatStatus.textContent = isOnline ? 'Online' : 'Offline';
                chatStatus.style.color = isOnline ? '#38a169' : '#a0aec0';
            }
        }
    }
    
    // На мобильных переключаем вид
    if (isMobile) {
        const sidebar = document.querySelector('.sidebar');
        const chatArea = document.getElementById('chat-area');
        
        sidebar.classList.add('mobile-hidden');
        chatArea.classList.add('mobile-active');
        
        // Настраиваем кнопку назад
        setupMobileNavigation();
    }
    
    // Загружаем сообщения
    await loadMessages(userId);
    
    // Отмечаем как прочитанные
    markChatAsRead(userId);
    
    // Обновляем информацию о пользователе в списке чатов после загрузки сообщений
    const chatItemInList = document.querySelector(`.chat-item[data-user-id="${userId}"]`);
    if (chatItemInList) {
        const chatUsername = document.getElementById('chat-username');
        if (chatUsername) {
            // Обновляем имя в списке чатов
            const username = chatItemInList.querySelector('.chat-username');
            if (username) {
                username.textContent = chatUsername.textContent;
            }
        }
    }
    
    // Фокус на поле ввода
    setTimeout(() => {
        document.getElementById('message-input').focus();
        // Скрываем кнопки звонка для ботов
        const _item = document.querySelector(`.chat-item[data-user-id="${userId}"]`);
        if (_item && _item.dataset.isBot === 'true') {
            document.getElementById('call-btn')?.classList.add('bot-hidden');
        } else {
            document.getElementById('call-btn')?.classList.remove('bot-hidden');
        }
    }, 100);
    
    // Скрываем результаты поиска
    document.getElementById('search-results').style.display = 'none';
    document.getElementById('search-input').value = '';
}

// Добавление сообщения в чат
function addMessageToChat(message) {
    const container = document.getElementById('messages-container');
    
    // Проверяем, не добавлено ли уже это сообщение
    const existingMessage = container.querySelector(`[data-message-id="${message.id}"]`);
    if (existingMessage) {
        return;
    }
    
    const noMessages = container.querySelector('.no-messages');
    if (noMessages) {
        container.innerHTML = '';
    }
    
    const messageHtml = createMessageHTML(message);
    container.insertAdjacentHTML('beforeend', messageHtml);
    _fixStickerImages(container);
    // Attach swipe to the newly added message element
    const newEl = container.querySelector(`[data-message-id="${message.id}"]`);
    if (newEl) _attachSwipeReplyEl(newEl);
    scrollToBottom();
}

// Проигрывание видео сообщения
function playVideoMessage(videoUrl) {
    // Создаем модальное окно для просмотра
    const modal = document.createElement('div');
    modal.className = 'video-modal active';
    modal.innerHTML = `
        <div class="video-container">
            <video class="video-preview" src="${videoUrl}" autoplay controls playsinline></video>
            <div class="video-controls">
                <button class="cancel-btn" onclick="this.closest('.video-modal').remove()">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    
    // Закрытие по клику вне видео
    modal.addEventListener('click', function(e) {
        if (e.target === modal) {
            modal.remove();
        }
    });
}

window.playVideoMessage = playVideoMessage;

// Проигрывание обычного видео из альбома
function playAlbumVideo(videoUrl) {
    // Создаем модальное окно для просмотра обычного видео
    const modal = document.createElement('div');
    modal.className = 'modal active';
    modal.innerHTML = `
        <div class="modal-content" style="max-width: 90vw; max-height: 90vh; background: #000; padding: 0; overflow: hidden;">
            <div style="position: relative; width: 100%; height: 100%;">
                <video 
                    src="${videoUrl}" 
                    controls 
                    autoplay 
                    playsinline
                    style="width: 100%; height: 100%; max-height: 90vh; object-fit: contain;">
                </video>
                <button 
                    onclick="this.closest('.modal').remove()" 
                    style="position: absolute; top: 10px; right: 10px; background: rgba(0,0,0,0.7); border: none; color: white; width: 40px; height: 40px; border-radius: 50%; cursor: pointer; display: flex; align-items: center; justify-content: center; font-size: 20px; z-index: 10;">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    
    // Закрытие по клику вне видео
    modal.addEventListener('click', function(e) {
        if (e.target === modal) {
            modal.remove();
        }
    });
}

window.playAlbumVideo = playAlbumVideo;

// Отправка сообщения
async function handleSendMessage(e) {
    e.preventDefault();
    if (_isSending) return;

    const messageInput = document.getElementById('message-input');
    const content = messageInput.value.trim();

    if (!content) return;
    _isSending = true;

    // Сразу останавливаем индикатор печатания
    if (_isTyping) {
        _isTyping = false;
        clearTimeout(_typingTimer);
        if (socket && socket.connected) {
            if (currentChatUserId) socket.emit('typing_stop', { to_user_id: currentChatUserId });
            else if (currentGroupId) socket.emit('typing_stop', { group_id: currentGroupId });
        }
    }

    // Если открыто Избранное — сохраняем как заметку
    if (_favoritesOpen && !currentChatUserId && !currentGroupId) {
        try {
            const r = await fetch('/favorites/note', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content })
            });
            const d = await r.json();
            if (d.success) {
                messageInput.value = '';
                // Добавляем сообщение в контейнер без перезагрузки
                const container = document.getElementById('messages-container');
                const noMsg = container.querySelector('.no-messages');
                if (noMsg) noMsg.remove();
                const msgEl = document.createElement('div');
                msgEl.className = 'message sent';
                msgEl.setAttribute('data-fav-id', d.id);
                msgEl.setAttribute('data-is-mine', '1');
                msgEl.setAttribute('data-is-deleted', '0');
                msgEl.innerHTML = `
                    <div class="message-content">${escapeHtml(d.content)}</div>
                    <div class="message-time">${d.saved_at}</div>`;
                container.appendChild(msgEl);
                scrollToBottom();
            } else {
                showError(d.error || 'Ошибка');
            }
        } catch (err) { showError('Ошибка'); }
        _isSending = false;
        return;
    }

    if (!currentChatUserId && !currentGroupId) {
        showError('Выберите чат для отправки сообщения');
        _isSending = false;
        return;
    }

    // Если открыта группа
    if (currentGroupId) {
        try {
            const response = await fetch(`/groups/${currentGroupId}/send`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content: content, reply_to_id: _replyToId || undefined })
            });
            if (!response.ok) throw new Error('Ошибка отправки сообщения');
            const data = await response.json();
            if (data.success) { messageInput.value = ''; _cancelReply(); }
        } catch (error) {
            showError('Не удалось отправить сообщение');
        }
        _isSending = false;
        return;
    }
    
    // Личный чат
    try {
        const response = await fetch('/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ receiver_id: currentChatUserId, content: content, reply_to_id: _replyToId || undefined })
        });
        const data = await response.json();
        if (data.error === 'spam_blocked') {
            showSpamblockModal(data.until);
        } else if (data.error === 'contacts_required') {
            showContactsRequiredModal();
        } else if (data.error === 'premium_required') {
            showPremiumModal(data.message || 'Эта функция доступна только для Premium пользователей.');
        } else if (data.error === 'not_enough_sparks') {
            _showNotEnoughSparksModal(data.required || 0);
        } else if (data.success) {
            messageInput.value = '';
            _cancelReply();
        } else if (!response.ok) {
            showError(data.error || 'Не удалось отправить сообщение');
        }
    } catch (error) {
        showError('Не удалось отправить сообщение');
    } finally {
        _isSending = false;
    }
}

// Обработка изменения размера окна
window.addEventListener('resize', function() {
    setupMobileNavigation();
});

// Показать меню сообщения
function showMessageMenu(messageId) {
    const menu = document.getElementById(`menu-${messageId}`);
    if (menu) {
        menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
    }
    
    // Скрыть другие меню
    document.querySelectorAll('.message-actions').forEach(m => {
        if (m.id !== `menu-${messageId}`) {
            m.style.display = 'none';
        }
    });
}

// Редактировать сообщение
function editMessage(messageId) {
    // Получаем текущее содержимое сообщения из DOM
    const messageEl = document.querySelector(`[data-message-id="${messageId}"]`);
    if (!messageEl) return;
    
    const contentEl = messageEl.querySelector('.message-content');
    if (!contentEl) return;
    
    // Убираем текст "(отредактировано)" если есть
    const currentContent = contentEl.textContent.replace('(отредактировано)', '').trim();
    
    const newContent = prompt('Отредактируйте сообщение:', currentContent);
    if (newContent === null || newContent.trim() === '') return;
    
    fetchWithTimeout(`/message/${messageId}/edit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: newContent })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            // Перезагружаем сообщения
            loadMessages(currentChatUserId);
        } else {
            showError(data.error || 'Ошибка редактирования сообщения');
        }
    })
    .catch(e => {
        console.error('Ошибка редактирования:', e);
        showError('Не удалось отредактировать сообщение');
    });
}

// Удалить сообщение
function deleteMessage(messageId) {
    if (!confirm('Вы уверены? Сообщение будет удалено.')) return;
    
    fetchWithTimeout(`/message/${messageId}/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            // Отправляем событие через Socket.IO
            if (socket && socket.connected) {
                socket.emit('message_deleted', {
                    message_id: messageId,
                    other_user_id: currentChatUserId
                });
            }
            loadMessages(currentChatUserId);
        } else {
            showError(data.error || 'Ошибка удаления сообщения');
        }
    })
    .catch(e => {
        console.error('Ошибка удаления:', e);
        showError('Не удалось удалить сообщение');
    });
}

// Изменение темы
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

// Загрузка сохраненной темы
function loadTheme() {
    const savedTheme = localStorage.getItem('theme') || document.body.getAttribute('data-theme') || 'light';
    setTheme(savedTheme);
}


// Обновление статуса онлайн/офлайн
function updateUserOnlineStatus(userId, isOnline, lastSeen = null) {
    const chatItem = document.querySelector(`.chat-item[data-user-id="${userId}"]`);
    if (chatItem) {
        const statusEl = chatItem.querySelector('.user-status');
        // Не трогаем статус ботов
        if (statusEl && statusEl.textContent === 'Бот') return;
        if (statusEl) {
            if (isOnline) {
                statusEl.textContent = 'Online';
                statusEl.style.color = '#38a169';
            } else {
                // Форматируем "был(а) в сети"
                if (lastSeen) {
                    statusEl.textContent = `был(а) в сети ${formatLastSeen(lastSeen)}`;
                } else {
                    statusEl.textContent = 'Offline';
                }
                statusEl.style.color = '#a0aec0';
            }
            
            // Обновляем класс для стилизации
            statusEl.classList.remove('online', 'offline');
            statusEl.classList.add(isOnline ? 'online' : 'offline');
        }
    }
    
    // Если это текущий открытый чат, обновляем статус в заголовке
    if (currentChatUserId === userId) {
        const chatStatus = document.getElementById('chat-status');
        if (chatStatus) {
            if (isOnline) {
                chatStatus.textContent = 'Online';
                chatStatus.style.color = '#38a169';
            } else {
                if (lastSeen) {
                    chatStatus.textContent = `был(а) в сети ${formatLastSeen(lastSeen)}`;
                } else {
                    chatStatus.textContent = 'Offline';
                }
                chatStatus.style.color = '#a0aec0';
            }
        }
    }
}

// Форматирование времени "был(а) в сети"
function formatLastSeen(lastSeenStr) {
    try {
        // lastSeenStr теперь в формате ISO (например "2026-03-08T10:30:00")
        const lastSeenDate = new Date(lastSeenStr);
        const now = new Date();
        
        const diffMs = now - lastSeenDate;
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);
        const diffDays = Math.floor(diffMs / 86400000);
        
        if (diffMins < 1) return 'только что';
        if (diffMins < 60) return `${diffMins} мин. назад`;
        if (diffHours < 24) return `${diffHours} ч. назад`;
        if (diffDays === 1) return 'вчера';
        if (diffDays < 7) return `${diffDays} дн. назад`;
        
        // Для старых дат показываем дату
        const day = String(lastSeenDate.getDate()).padStart(2, '0');
        const month = String(lastSeenDate.getMonth() + 1).padStart(2, '0');
        return `${day}.${month}`;
    } catch (e) {
        console.error('Error formatting last_seen:', e, lastSeenStr);
        return lastSeenStr;
    }
}


// Применение обоев при загрузке
document.addEventListener('DOMContentLoaded', function() {
    loadUserSettings();
});

// Загрузка настроек пользователя
async function loadUserSettings() {
    try {
        const response = await fetch('/user/settings');
        if (!response.ok) return;
        
        const data = await response.json();
        
        // Применяем обои
        if (data.chat_wallpaper) {
            updateWallpaper(data.chat_wallpaper);
        }
    } catch (error) {
        console.error('Ошибка загрузки настроек:', error);
    }
}

// Функция применения обоев
function applyWallpaper() {
    // Обои уже применены через loadUserSettings
    // Эта функция оставлена для совместимости
}

// Обновление обоев при изменении профиля
function updateWallpaper(wallpaper) {
    const messagesContainer = document.getElementById('messages-container');
    if (messagesContainer) {
        messagesContainer.setAttribute('data-wallpaper', wallpaper);
        console.log('Обои обновлены:', wallpaper);
    }
}

window.updateWallpaper = updateWallpaper;


// ============================================
// ВИДЕО КРУЖОЧКИ
// ============================================

let mediaRecorder = null;
let recordedChunks = [];
let recordingTimer = null;
let recordingSeconds = 0;
let videoStream = null;

// Инициализация видео кружочков
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
    let _pressTimer = null;       // 300ms → начать запись
    let _switchTimer = null;      // 600ms → сменить режим (мобильный)
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

    // ПКМ — переключить между кружком и гс (только правая кнопка мыши, не тач)
    [_videoBtnCycle, _voiceBtn].forEach(btn => {
        if (!btn) return;
        btn.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            // Отменяем таймер записи — ПКМ не должен стартовать запись
            if (_pressTimer) { clearTimeout(_pressTimer); _pressTimer = null; }
            if (_switchTimer) { clearTimeout(_switchTimer); _switchTimer = null; }
            // Только реальный ПКМ (button === 2), не тач-долгое нажатие
            if (e.button !== 2) return;
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
    // Глобальный хук — вызывается при смене чата
    window._abortRecordingOnChatSwitch = function() {
        if (_pressTimer) { clearTimeout(_pressTimer); _pressTimer = null; }
        if (_switchTimer) { clearTimeout(_switchTimer); _switchTimer = null; }
        if (_recording) _cancelRecording();
    };

    function _finishRecording() {
        if (!_recording) return;
        _recording = false; _locked = false;
        if (_mediaMode === 'voice') stopVoiceRecord();
        else {
            // Для видео-кружка: просто закрываем модалку без отправки
            // Пользователь сам нажимает "отправить" внутри модалки
            closeVideoRecorder();
        }
        _hideLockHint();
        _updateBtns();
    }

    function _showLockHint() {
        let hint = document.getElementById('_rec-hint');
        if (!hint) {
            hint = document.createElement('div');
            hint.id = '_rec-hint';
            hint.style.cssText = 'position:absolute;right:56px;bottom:8px;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:20px;padding:6px 12px;font-size:12px;color:var(--text-secondary);display:flex;align-items:center;gap:8px;pointer-events:none;z-index:10;white-space:nowrap;';
            hint.innerHTML = '<span>\u2190 \u041e\u0442\u043c\u0435\u043d\u0430</span><span style="margin:0 4px;">|</span><span>\u2191 \u0417\u0430\u043a\u0440\u0435\u043f\u0438\u0442\u044c</span>';
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

        // На тач-устройствах: 600ms = смена режима (без записи)
        // На десктопе смена режима — ПКМ (contextmenu)
        const isTouch = !!e.touches;
        if (isTouch) {
            _switchTimer = setTimeout(() => {
                _switchTimer = null;
                // Отменяем таймер записи если он ещё не сработал
                if (_pressTimer) { clearTimeout(_pressTimer); _pressTimer = null; }
                if (_recording) return; // уже пишем — не переключаем
                _mediaMode = _mediaMode === 'video' ? 'voice' : 'video';
                _updateBtns();
                // Вибрация как фидбек
                if (navigator.vibrate) navigator.vibrate(50);
            }, 600);
        }

        _pressTimer = setTimeout(() => {
            _pressTimer = null;
            if (_switchTimer) return; // ждём — вдруг это смена режима
            _recording = true;
            _showLockHint();
            if (_mediaMode === 'voice') startVoiceRecord();
            else openVideoRecorder();
        }, 300);
    }

    function _onPressMove(e) {
        if (_switchTimer && e.touches) {
            const touch = e.touches[0];
            const dx = touch.clientX - _pressStartX;
            const dy = touch.clientY - _pressStartY;
            // Если палец двигается — отменяем смену режима
            if (Math.abs(dx) > 10 || Math.abs(dy) > 10) {
                clearTimeout(_switchTimer); _switchTimer = null;
            }
        }
        if (!_recording || _locked) return;
        const touch = e.touches ? e.touches[0] : e;
        const dx = touch.clientX - _pressStartX;
        const dy = touch.clientY - _pressStartY;
        if (dx < -60) _cancelRecording();
        else if (dy < -60) _showLockedUI();
    }

    function _onPressEnd(e) {
        if (_switchTimer) { clearTimeout(_switchTimer); _switchTimer = null; }
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
});
// Открыть рекордер видео
async function openVideoRecorder() {
    if (!currentChatUserId) {
        showError('Сначала выберите чат');
        return;
    }
    
    // Создаем модальное окно
    const modal = document.createElement('div');
    modal.className = 'video-modal active';
    modal.id = 'video-modal';
    modal.innerHTML = `
        <div class="video-container">
            <video class="video-preview" id="video-preview" autoplay muted playsinline></video>
            <div class="recording-timer" id="recording-timer">00:00</div>
            <div class="video-controls">
                <button class="cancel-btn" onclick="closeVideoRecorder()">
                    <i class="fas fa-times"></i>
                </button>
                <button class="record-btn" id="record-btn" onclick="toggleRecording()">
                    <i class="fas fa-circle"></i>
                </button>
                <button class="send-video-btn" id="send-video-btn" onclick="sendVideoCircle()">
                    <i class="fas fa-paper-plane"></i>
                </button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    
    // Запрашиваем доступ к камере
    try {
        const stream = await navigator.mediaDevices.getUserMedia({
            video: {
                width: { ideal: 640 },
                height: { ideal: 640 },
                facingMode: 'user'
            },
            audio: true
        });
        
        videoStream = stream;
        const videoPreview = document.getElementById('video-preview');
        videoPreview.srcObject = stream;
        
        console.log('Камера подключена, stream:', stream);
    } catch (error) {
        console.error('Ошибка доступа к камере:', error);
        showError('Не удалось получить доступ к камере. Проверьте разрешения.');
        closeVideoRecorder();
    }
}

// Закрыть рекордер
function closeVideoRecorder() {
    // Останавливаем запись если идет
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
    }
    
    // Останавливаем поток
    if (videoStream) {
        videoStream.getTracks().forEach(track => track.stop());
        videoStream = null;
    }
    
    // Очищаем таймер
    if (recordingTimer) {
        clearInterval(recordingTimer);
        recordingTimer = null;
    }
    
    // Удаляем модальное окно
    const modal = document.querySelector('.video-modal');
    if (modal) {
        modal.remove();
    }
    
    // Сбрасываем данные
    recordedChunks = [];
    recordingSeconds = 0;
}

// Переключить запись
function toggleRecording() {
    const recordBtn = document.getElementById('record-btn');
    const timer = document.getElementById('recording-timer');
    const sendBtn = document.getElementById('send-video-btn');
    
    if (!mediaRecorder || mediaRecorder.state === 'inactive') {
        // Начать запись
        startRecording();
        recordBtn.classList.add('recording');
        recordBtn.innerHTML = '<i class="fas fa-stop"></i>';
        timer.classList.add('active');
        sendBtn.classList.remove('active');
    } else {
        // Остановить запись
        stopRecording();
        recordBtn.classList.remove('recording');
        recordBtn.innerHTML = '<i class="fas fa-circle"></i>';
        timer.classList.remove('active');
        sendBtn.classList.add('active');
    }
}

// Начать запись
function startRecording() {
    if (!videoStream) {
        showError('Видео поток не инициализирован');
        return;
    }
    
    recordedChunks = [];
    recordingSeconds = 0;
    
    try {
        // Проверяем поддерживаемые типы
        let mimeType = 'video/webm;codecs=vp9';
        if (!MediaRecorder.isTypeSupported(mimeType)) {
            mimeType = 'video/webm;codecs=vp8';
            if (!MediaRecorder.isTypeSupported(mimeType)) {
                mimeType = 'video/webm';
            }
        }
        
        console.log('Используем MIME тип:', mimeType);
        
        mediaRecorder = new MediaRecorder(videoStream, {
            mimeType: mimeType
        });
        
        mediaRecorder.ondataavailable = (event) => {
            if (event.data && event.data.size > 0) {
                recordedChunks.push(event.data);
                console.log('Получен chunk:', event.data.size, 'bytes');
            }
        };
        
        mediaRecorder.onstop = () => {
            console.log('Запись остановлена, chunks:', recordedChunks.length);
        };
        
        mediaRecorder.onerror = (event) => {
            console.error('Ошибка MediaRecorder:', event.error);
            showError('Ошибка записи видео');
        };
        
        mediaRecorder.start(100); // Собираем данные каждые 100мс
        console.log('Запись началась');
        
        // Запускаем таймер
        recordingTimer = setInterval(() => {
            recordingSeconds++;
            const minutes = Math.floor(recordingSeconds / 60);
            const seconds = recordingSeconds % 60;
            const timerEl = document.getElementById('recording-timer');
            if (timerEl) {
                timerEl.textContent = `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
            }
            
            // Максимум 60 секунд
            if (recordingSeconds >= 60) {
                toggleRecording();
            }
        }, 1000);
    } catch (error) {
        console.error('Ошибка начала записи:', error);
        showError('Не удалось начать запись: ' + error.message);
    }
}

// Остановить запись
function stopRecording() {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
    }
    
    if (recordingTimer) {
        clearInterval(recordingTimer);
        recordingTimer = null;
    }
}

// Отправить видео кружочек
async function sendVideoCircle() {
    if (recordedChunks.length === 0) {
        showError('Сначала запишите видео');
        return;
    }
    
    // Создаем blob из записанных данных
    const blob = new Blob(recordedChunks, { type: 'video/webm' });
    
    // Создаем FormData
    const formData = new FormData();
    formData.append('video', blob, `video_${Date.now()}.webm`);
    formData.append('receiver_id', currentChatUserId);
    formData.append('duration', recordingSeconds);
    
    try {
        const response = await fetch('/send/video-circle', {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        
        if (data.success) {
            console.log('Видео отправлено успешно:', data);
            
            // Добавляем сообщение локально (на случай если Socket.IO не сработает)
            const message = {
                id: data.message_id,
                content: '[Видео кружочек]',
                message_type: 'video_note',
                media_url: data.media_url,
                duration: recordingSeconds,
                timestamp: new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) + ' ' + new Date().toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }),
                is_mine: true
            };
            
            addMessageToChat(message);
            closeVideoRecorder();
        } else {
            showError(data.error || 'Ошибка отправки видео');
        }
    } catch (error) {
        console.error('Ошибка отправки видео:', error);
        showError('Не удалось отправить видео');
    }
}

// Экспорт функций
window.closeVideoRecorder = closeVideoRecorder;
window.toggleRecording = toggleRecording;
window.sendVideoCircle = sendVideoCircle;


// ============================================
// ОТПРАВКА КАРТИНОК
// ============================================

// Обработка выбора картинки
async function handleImageSelect(event) {
    const file = event.target.files[0];
    if (!file) return;
    
    if (!currentChatUserId) {
        showError('Сначала выберите чат');
        return;
    }
    
    // Проверка типа файла
    if (!file.type.startsWith('image/')) {
        showError('Можно отправлять только изображения');
        return;
    }
    
    // Проверка размера (макс 10MB)
    if (file.size > 10 * 1024 * 1024) {
        showError('Размер изображения не должен превышать 10MB');
        return;
    }
    
    // Создаем FormData
    const formData = new FormData();
    formData.append('image', file);
    formData.append('receiver_id', currentChatUserId);
    
    try {
        const response = await fetch('/send/image', {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        
        if (data.success) {
            console.log('Картинка отправлена:', data);
            
            // Добавляем сообщение локально
            const message = {
                id: data.message_id,
                content: '[Изображение]',
                message_type: 'image',
                media_url: data.media_url,
                timestamp: new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) + ' ' + new Date().toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }),
                is_mine: true
            };
            
            addMessageToChat(message);
        } else if (data.error === 'spam_blocked') {
            showSpamblockModal(data.until);
        } else {
            showError(data.error || 'Ошибка отправки изображения');
        }
    } catch (error) {
        console.error('Ошибка отправки изображения:', error);
        showError('Не удалось отправить изображение');
    }
    
    // Очищаем input
    event.target.value = '';
}

// Просмотр изображения
function viewImage(imageUrl) {
    const modal = document.createElement('div');
    modal.className = 'image-preview-modal active';
    modal.innerHTML = `
        <div class="image-preview-container">
            <button class="image-preview-close" onclick="this.closest('.image-preview-modal').remove()">
                <i class="fas fa-times"></i>
            </button>
            <img src="${imageUrl}" alt="Изображение">
        </div>
    `;
    document.body.appendChild(modal);
    modal.addEventListener('click', function(e) {
        if (e.target === modal) modal.remove();
    });
}

function viewImageById(imgEl) {
    viewImage(imgEl.src);
}

window.handleImageSelect = handleImageSelect;
window.viewImage = viewImage;
window.viewImageById = viewImageById;


// ============================================
// ГРУППЫ И КАНАЛЫ
// ============================================

// Показать модальное окно создания группы
function showCreateGroupModal() {
    const modal = document.createElement('div');
    modal.className = 'create-group-modal active';
    modal.id = 'create-group-modal';
    modal.innerHTML = `
        <div class="create-group-form">
            <h2><i class="fas fa-users"></i> Создать группу/канал</h2>
            
            <div class="group-type-selector">
                <button class="group-type-btn active" data-type="group" onclick="selectGroupType('group')">
                    <i class="fas fa-users"></i><br>Группа
                </button>
                <button class="group-type-btn" data-type="channel" onclick="selectGroupType('channel')">
                    <i class="fas fa-bullhorn"></i><br>Канал
                </button>
            </div>
            
            <div class="form-group">
                <label><i class="fas fa-signature"></i> Название</label>
                <input type="text" id="group-name" placeholder="Введите название" maxlength="100" required>
            </div>
            
            <div class="form-group">
                <label><i class="fas fa-at"></i> Username (необязательно)</label>
                <input type="text" id="group-username" placeholder="username" maxlength="50" pattern="[a-zA-Z0-9_]+">
                <small style="color: #a0aec0; font-size: 12px;">Только латиница, цифры и подчеркивание</small>
            </div>
            
            <div class="form-group">
                <label><i class="fas fa-info-circle"></i> Описание</label>
                <textarea id="group-description" placeholder="Описание группы/канала" maxlength="500" rows="3"></textarea>
            </div>
            
            <input type="hidden" id="group-type" value="group">
            
            <div style="display: flex; gap: 10px; margin-top: 20px;">
                <button class="btn btn-primary" onclick="createGroup()" style="flex: 1;">
                    <i class="fas fa-check"></i> Создать
                </button>
                <button class="btn" onclick="closeCreateGroupModal()" style="flex: 1; background: #e2e8f0; color: #4a5568;">
                    <i class="fas fa-times"></i> Отмена
                </button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    
    // Закрытие по клику вне формы
    modal.addEventListener('click', function(e) {
        if (e.target === modal) {
            closeCreateGroupModal();
        }
    });
}

// Закрыть модальное окно
function closeCreateGroupModal() {
    const modal = document.getElementById('create-group-modal');
    if (modal) {
        modal.remove();
    }
}

// Выбор типа группы
function selectGroupType(type) {
    document.querySelectorAll('.group-type-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    document.querySelector(`[data-type="${type}"]`).classList.add('active');
    document.getElementById('group-type').value = type;
}

// Создать группу
async function createGroup() {
    const name = document.getElementById('group-name').value.trim();
    const username = document.getElementById('group-username').value.trim();
    const description = document.getElementById('group-description').value.trim();
    const isChannel = document.getElementById('group-type').value === 'channel';
    
    if (!name) {
        showError('Введите название');
        return;
    }
    
    try {
        const response = await fetch('/groups/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: name,
                username: username || null,
                description: description,
                is_channel: isChannel,
                is_public: true
            })
        });
        
        const data = await response.json();
        
        if (response.ok && data.success) {
            closeCreateGroupModal();
            // Обновляем список чатов
            await loadAllChats();
            showError(isChannel ? 'Канал создан успешно!' : 'Группа создана успешно!', 'success');
            
            // Открываем созданную группу
            if (data.group && data.group.id) {
                setTimeout(() => {
                    openGroup(data.group.id, data.group.name);
                }, 500);
            }
        } else {
            showError(data.error || 'Ошибка создания группы');
        }
    } catch (error) {
        console.error('Ошибка создания группы:', error);
        showError('Не удалось создать группу');
    }
}

window.showCreateGroupModal = showCreateGroupModal;
window.closeCreateGroupModal = closeCreateGroupModal;
window.selectGroupType = selectGroupType;
window.createGroup = createGroup;

// Вступление в группу из поиска
async function joinGroupFromSearch(groupId) {
    try {
        const response = await fetch(`/groups/${groupId}/join`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        const data = await response.json();
        
        if (data.success) {
            document.getElementById('search-results').style.display = 'none';
            document.getElementById('search-input').value = '';
            await loadAllChats();
            if (!data.already_member) {
                showError('Вы успешно вступили в группу!', 'success');
            }
            // Открываем группу — берём имя из уже загруженного списка
            const groupItem = document.querySelector(`.chat-item[data-group-id="${groupId}"]`);
            const groupName = groupItem ? groupItem.querySelector('.chat-username')?.textContent?.trim() : '';
            openGroup(groupId, groupName || '');
        } else {
            showError(data.error || 'Не удалось вступить в группу');
        }
    } catch (error) {
        console.error('Ошибка вступления в группу:', error);
        showError('Не удалось вступить в группу');
    }
}

window.joinGroupFromSearch = joinGroupFromSearch;



// ============================================
// ВКЛАДКИ И ГРУППЫ
// ============================================

// Переключение вкладок
function switchTab(tab) {
    // Убираем активный класс со всех вкладок
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
    
    // Добавляем активный класс к выбранной вкладке
    if (tab === 'chats') {
        const chatsBtn = document.querySelector('.tab-btn[onclick*="chats"]');
        const chatsTab = document.getElementById('chats-tab');
        if (chatsBtn) chatsBtn.classList.add('active');
        if (chatsTab) chatsTab.classList.add('active');
        loadChats();
    } else if (tab === 'groups') {
        const groupsBtn = document.querySelector('.tab-btn[onclick*="groups"]');
        const groupsTab = document.getElementById('groups-tab');
        if (groupsBtn) groupsBtn.classList.add('active');
        if (groupsTab) groupsTab.classList.add('active');
        loadGroups();
    }
}

// Загрузка групп
async function loadGroups() {
    const groupsList = document.getElementById('groups-list');
    
    try {
        const response = await fetch('/groups');
        
        console.log('Response status:', response.status);
        console.log('Response headers:', response.headers.get('content-type'));
        
        if (response.status === 401) {
            // Пользователь не авторизован - перенаправляем на логин
            window.location.href = '/login';
            return;
        }
        
        if (!response.ok) {
            let errorData = {};
            const contentType = response.headers.get('content-type');
            
            if (contentType && contentType.includes('application/json')) {
                errorData = await response.json().catch(() => ({}));
            } else {
                const text = await response.text();
                console.error('Non-JSON response:', text);
                errorData = { error: 'Сервер вернул некорректный ответ' };
            }
            
            console.error('Ошибка загрузки групп:', errorData);
            throw new Error(errorData.error || 'Ошибка загрузки групп');
        }
        
        const data = await response.json();
        console.log('Groups data:', data);
        displayGroups(data.groups);
    } catch (error) {
        console.error('Ошибка загрузки групп:', error);
        
        if (!groupsList) {
            console.error('Element groups-list not found!');
            return;
        }
        
        groupsList.innerHTML = `
            <div class="no-chats">
                <i class="fas fa-exclamation-triangle"></i>
                <p>Не удалось загрузить группы</p>
                <p class="hint">${escapeHtml(error.message)}</p>
                <button class="btn btn-primary" onclick="loadGroups()" style="margin-top: 10px;">
                    <i class="fas fa-redo"></i> Попробовать снова
                </button>
            </div>
        `;
    }
}

// Отображение групп
function displayGroups(groups) {
    const groupsList = document.getElementById('groups-list');
    
    if (!groups || groups.length === 0) {
        groupsList.innerHTML = `
            <div class="no-chats">
                <i class="fas fa-users-slash"></i>
                <p>У вас пока нет групп</p>
                <p class="hint">Создайте группу или канал</p>
            </div>
        `;
        return;
    }
    
    let html = '';
    groups.forEach(group => {
        const channelBadge = group.is_channel ? '<span class="channel-badge">КАНАЛ</span>' : '';
        
        // Определяем стиль аватарки
        const avatarStyle = group.avatar_url 
            ? `background-image: url('${group.avatar_url}'); background-size: cover; background-position: center;`
            : `background: ${group.avatar_color}`;
        const avatarContent = group.avatar_url ? '' : group.avatar_letter;
        
        html += `
            <div class="group-item group-chat-item" data-group-id="${group.id}" data-group-name="${escapeHtml(group.name)}">
                <div class="group-avatar" style="${avatarStyle}">
                    ${avatarContent}
                </div>
                <div class="group-info">
                    <div class="group-name">
                        ${escapeHtml(group.name)}
                        ${channelBadge}
                    </div>
                    <div class="group-members">
                        <i class="fas fa-users"></i> ${group.members_count} участников
                    </div>
                </div>
            </div>
        `;
    });
    
    groupsList.innerHTML = html;
    
    // Добавляем обработчики событий для групп
    setupGroupItemListeners();
}

// Настройка обработчиков событий для элементов групп
function setupGroupItemListeners() {
    const groupsList = document.getElementById('groups-list');
    if (!groupsList) return;
    
    // Удаляем старый обработчик если есть
    const oldHandler = groupsList._clickHandler;
    if (oldHandler) {
        groupsList.removeEventListener('click', oldHandler);
        groupsList.removeEventListener('touchend', oldHandler);
    }
    
    // Создаем новый обработчик
    const clickHandler = function(e) {
        // Находим ближайший group-item
        const groupItem = e.target.closest('.group-item');
        if (!groupItem) return;
        
        // Открываем группу
        const groupId = parseInt(groupItem.dataset.groupId);
        const groupName = groupItem.dataset.groupName;
        openGroup(groupId, groupName);
    };
    
    // Сохраняем ссылку на обработчик
    groupsList._clickHandler = clickHandler;
    
    // Добавляем обработчики для клика и тача
    groupsList.addEventListener('click', clickHandler);
    groupsList.addEventListener('touchend', clickHandler);
}

// Открытие группы
async function openGroup(groupId, groupName) {
    console.log('openGroup called:', groupId, groupName);
    // Сохраняем черновик текущего чата перед переключением
    _saveDraft();

    _cancelReply();
    currentChatUserId = null;
    _isSending = false;
    _favoritesOpen = false;
    currentGroupId = groupId;
    // Загружаем участников для @упоминаний
    if (window._loadMentionMembers) window._loadMentionMembers(groupId);

    // Скрываем кнопки личного чата
    const _chatArea = document.getElementById('chat-area');
    if (_chatArea) _chatArea.classList.remove('personal-chat-open');
    document.getElementById('call-btn')?.removeAttribute('data-visible');
    document.getElementById('clear-history-btn')?.removeAttribute('data-visible');
    
    // Загружаем данные группы
    try {
        console.log('Fetching group data...');
        const response = await fetch(`/groups/${groupId}`);
        console.log('Response status:', response.status);
        
        if (!response.ok) {
            const errorText = await response.text();
            console.error('Server error:', errorText);
            throw new Error('Ошибка загрузки группы');
        }
        
        const data = await response.json();
        console.log('Group data received:', data);
        _currentGroupData = data.group; // сохраняем для проверки жалоб
        
        // Обновляем UI
        document.getElementById('chat-welcome').style.display = 'none';
        document.getElementById('chat-active').style.display = 'flex';
        
        // На мобильных устройствах скрываем sidebar и показываем chat-area
        const isMobile = window.innerWidth <= 768;
        
        if (isMobile) {
            const sidebar = document.querySelector('.sidebar');
            const chatArea = document.getElementById('chat-area');
            
            if (sidebar && chatArea) {
                sidebar.classList.add('mobile-hidden');
                chatArea.classList.add('mobile-active');
            }
            
            // Настраиваем кнопку назад
            setupMobileNavigation();
        }
        
        // Сбрасываем активный класс у всех чатов
        document.querySelectorAll('.chat-item, .group-item').forEach(item => {
            item.classList.remove('active');
        });
        
        // Добавляем активный класс к выбранной группе
        const groupItem = document.querySelector(`.chat-item[data-group-id="${groupId}"], .group-item[data-group-id="${groupId}"]`);
        if (groupItem) {
            groupItem.classList.add('active');
        }
        
        // Обновляем заголовок с аватаркой группы
        const chatUsername = document.getElementById('chat-username');
        const chatAvatar = document.getElementById('chat-header-avatar');
        const chatStatus = document.getElementById('chat-status');
        
        if (chatUsername) {
            chatUsername.textContent = data.group.name;
        }
        
        if (chatAvatar) {
            chatAvatar.style.backgroundImage = 'none';
            chatAvatar.style.backgroundColor = data.group.avatar_color;
            chatAvatar.textContent = data.group.name[0].toUpperCase();
        }
        
        if (chatStatus) {
            const channelText = data.group.is_channel ? 'Канал' : 'Группа';
            const usernameText = data.group.username ? ` • @${data.group.username}` : '';
            chatStatus.textContent = `${channelText}${usernameText} • ${data.members.length} участников`;
            chatStatus.style.color = '#a0aec0';
        }
        
        // Показываем/скрываем кнопки для групп
        const addToChatBtn = document.getElementById('add-to-chat-btn');
        if (addToChatBtn) {
            addToChatBtn.style.display = 'none';
        }
        
        // Показываем кнопку добавления участников если пользователь админ
        let addMemberBtn = document.getElementById('add-member-btn');
        if (!addMemberBtn) {
            addMemberBtn = document.createElement('button');
            addMemberBtn.id = 'add-member-btn';
            addMemberBtn.className = 'icon-btn';
            addMemberBtn.title = 'Добавить участника';
            addMemberBtn.innerHTML = '<i class="fas fa-user-plus"></i>';
            addMemberBtn.onclick = () => showAddMemberModal(groupId, data.group.name);
            const chatHeader = document.querySelector('.chat-header');
            const infoBtn = chatHeader.querySelector('.icon-btn[onclick*="viewChatInfo"]');
            if (infoBtn) chatHeader.insertBefore(addMemberBtn, infoBtn);
        }
        if (addMemberBtn) {
            addMemberBtn.style.display = data.group.is_admin ? 'block' : 'none';
        }

        // Кнопка настроек группы (шестерёнка) — только для админов
        let settingsBtn = document.getElementById('group-settings-btn');
        if (!settingsBtn) {
            settingsBtn = document.createElement('button');
            settingsBtn.id = 'group-settings-btn';
            settingsBtn.className = 'icon-btn';
            settingsBtn.title = 'Настройки группы';
            settingsBtn.innerHTML = '<i class="fas fa-cog"></i>';
            const chatHeader = document.querySelector('.chat-header');
            const infoBtn = chatHeader.querySelector('.icon-btn[onclick*="viewChatInfo"]');
            if (infoBtn) chatHeader.insertBefore(settingsBtn, infoBtn);
        }
        settingsBtn.onclick = () => { showGroupInfo(groupId); setTimeout(() => switchGinfoTab('settings'), 50); };
        settingsBtn.style.display = data.group.is_admin ? 'flex' : 'none';

        // Показываем кнопку поиска в группе
        const groupSearchBtn = document.getElementById('group-search-btn');
        if (groupSearchBtn) groupSearchBtn.style.display = 'flex';

        // Кнопка группового звонка — только для групп (не каналов) и только для админов
        let gcBtn = document.getElementById('group-call-btn');
        if (!gcBtn) {
            gcBtn = document.createElement('button');
            gcBtn.id = 'group-call-btn';
            gcBtn.className = 'icon-btn';
            gcBtn.title = 'Групповой звонок';
            gcBtn.innerHTML = '<i class="fas fa-phone-volume"></i>';
            gcBtn.onclick = () => startGroupCall(false);
            const callBtn = document.getElementById('call-btn');
            if (callBtn) callBtn.parentNode.insertBefore(gcBtn, callBtn);
        }
        gcBtn.style.display = (!data.group.is_channel && data.group.is_admin) ? 'flex' : 'none';

        // Кнопка трансляции — только для каналов и только для админов
        let streamBtn = document.getElementById('_stream-live-btn');
        if (!streamBtn) {
            streamBtn = document.createElement('button');
            streamBtn.id = '_stream-live-btn';
            streamBtn.className = 'icon-btn';
            streamBtn.title = 'Начать трансляцию';
            streamBtn.innerHTML = '<i class="fas fa-broadcast-tower"></i>';
            streamBtn.onclick = () => showStartStreamModal();
            const callBtn = document.getElementById('call-btn');
            if (callBtn) callBtn.parentNode.insertBefore(streamBtn, callBtn);
        }
        // Проверяем есть ли активная трансляция
        if (data.group.is_channel && data.group.is_admin) {
            streamBtn.style.display = 'flex';
            fetch(`/groups/${groupId}/stream/status`).then(r => r.json()).then(s => {
                if (s.active) { streamBtn.style.color = '#e53e3e'; streamBtn.title = '🔴 Трансляция идёт'; }
                else { streamBtn.style.color = ''; streamBtn.title = 'Начать трансляцию'; }
            }).catch(() => {});
        } else {
            streamBtn.style.display = 'none';
        }

        // Скрываем кнопки не нужные в группах/каналах
        const addContactBtn = document.querySelector('.chat-header .icon-btn[onclick="addContactFromChat()"]');
        if (addContactBtn) addContactBtn.style.display = 'none';

        // Обновляем видимость формы сообщений (для каналов показываем заглушку если не админ)
        // Проверяем что пользователь не переключился на личный чат пока грузилась группа
        if (currentGroupId === groupId) {
            updateMessageInputVisibility(data.group.is_channel, data.group.is_admin);
        }

        // Добавляем кнопку платного поста для владельцев каналов
        if (currentGroupId === groupId) setTimeout(_addPaidPostBtn, 100);
        
        // Сбрасываем placeholder поля ввода
        const msgInput = document.getElementById('message-input');
        if (msgInput) msgInput.placeholder = 'Введите сообщение...';
        // Восстанавливаем черновик группы
        _restoreDraft('group_' + groupId);
        // Отмечаем сообщения как прочитанные
        setTimeout(_markVisibleGroupMessages, 500);
        
        // Присоединяемся к комнате группы через Socket.IO
        if (socket && socket.connected) {
            socket.emit('join_group', { group_id: groupId });
        }
        
        displayGroupMessages(data.messages);
        scrollToBottom();
        
        // Загружаем закреплённое сообщение
        if (typeof loadPinnedMessage === 'function') loadPinnedMessage();

        // Отмечаем сообщения как прочитанные
        markGroupAsRead(groupId);
    } catch (error) {
        console.error('Ошибка загрузки группы:', error);
        showError('Не удалось загрузить группу');
        
        // Возвращаемся к списку чатов при ошибке на мобильных
        if (window.innerWidth <= 768) {
            backToChats();
        }
    }
}

// Отображение сообщений группы
function displayGroupMessages(messages) {
    const container = document.getElementById('messages-container');
    
    if (!messages || messages.length === 0) {
        container.innerHTML = `
            <div class="no-messages">
                <i class="fas fa-comment-medical"></i>
                <p>Начните общение в этой группе</p>
            </div>
        `;
        return;
    }
    
    let html = '';
    messages.forEach(msg => {
        html += createGroupMessageHTML(msg);
    });
    
    container.innerHTML = html;
    _fixStickerImages(container);
    _reattachCtxMenu();
    _attachSwipeReply(container);

    // Загружаем счётчики искр для постов канала
    if (_currentGroupData && _currentGroupData.is_channel) {
        container.querySelectorAll('.spark-reaction-bar[id^="spark-bar-"]').forEach(async bar => {
            const msgId = bar.id.replace('spark-bar-', '');
            try {
                const r = await fetch(`/sparks/post/${msgId}/total`);
                const d = await r.json();
                if (d.total > 0) bar.innerHTML = `<span class="spark-total">✨ ${d.total}</span>`;
            } catch {}
        });
    }
}

// Создание HTML для группового сообщения
function createGroupMessageHTML(msg) {
    const messageClass = msg.is_mine ? 'sent' : 'received';
    
    // Определяем стиль аватарки отправителя
    let senderInfo = '';
    if (!msg.is_mine) {
        let avatarStyle;
        let avatarContent;
        
        if (msg.sender_avatar_url) {
            avatarStyle = `background-image: url('${msg.sender_avatar_url}'); background-size: cover; background-position: center; background-color: ${msg.sender_avatar_color};`;
            avatarContent = '';
        } else {
            avatarStyle = `background: ${msg.sender_avatar_color};`;
            avatarContent = msg.sender_avatar_letter;
        }
        
        senderInfo = `
            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
                <div class="chat-avatar" style="${avatarStyle} width: 24px; height: 24px; font-size: 12px;">
                    ${avatarContent}
                </div>
                <div class="message-sender" style="color: ${msg.sender_avatar_color}; font-weight: 600; font-size: 12px;">
                    ${escapeHtml(msg.sender_name)}
                </div>
            </div>
        `;
    }
    
    let content = '';
    
    // Альбом (множественные файлы)
    if (msg.media_files && msg.media_files.length > 0) {
        const gridClass = msg.media_files.length === 1 ? 'media-grid-1' :
                         msg.media_files.length === 2 ? 'media-grid-2' :
                         msg.media_files.length === 3 ? 'media-grid-3' :
                         msg.media_files.length === 4 ? 'media-grid-4' :
                         'media-grid-many';
        
        let mediaHtml = `<div class="message-album ${gridClass}">`;
        
        msg.media_files.forEach((file, index) => {
            if (file.media_type === 'image') {
                mediaHtml += `
                    <div class="album-item" onclick="viewImage('${file.media_url}')">
                        <img src="${file.media_url}" alt="Изображение" loading="lazy">
                    </div>
                `;
            } else if (file.media_type === 'video') {
                mediaHtml += `
                    <div class="album-item video-item" onclick="playAlbumVideo('${file.media_url}')">
                        <video src="${file.media_url}" preload="metadata"></video>
                        <div class="video-play-overlay">
                            <i class="fas fa-play"></i>
                        </div>
                    </div>
                `;
            } else {
                // Файл (документ)
                const icon = getFileIconForMessage(file.file_name);
                mediaHtml += `
                    <div class="album-item file-item">
                        <a href="${file.media_url}" download="${file.file_name}" style="text-decoration: none; color: inherit; display: flex; align-items: center; gap: 10px; padding: 10px;">
                            <i class="fas ${icon}" style="font-size: 24px; color: var(--primary-color);"></i>
                            <div style="flex: 1; overflow: hidden;">
                                <div style="font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(file.file_name)}</div>
                            </div>
                            <i class="fas fa-download" style="color: var(--primary-color);"></i>
                        </a>
                    </div>
                `;
            }
        });
        
        mediaHtml += '</div>';
        
        // Добавляем подпись если есть и она не "Файлы"
        if (msg.content && msg.content !== 'Файлы') {
            mediaHtml += `<div class="message-content" style="margin-top: 8px;">${escapeHtml(msg.content)}</div>`;
        }
        
        content = mediaHtml;
    }
    // Стикер (группа)
    else if (msg.message_type === 'sticker' || (msg.content && msg.content.startsWith('[sticker]'))) {
        const stickerUrl = msg.content ? msg.content.replace('[sticker]', '') : '';
        const packId = msg.sticker_pack_id || '';
        const isAnimated = stickerUrl && stickerUrl.startsWith('data:application/json');
        if (isAnimated) {
            const uid = 'lottie_g' + msg.id + '_' + Date.now();
            content = `<div class="sticker-message" data-sticker-url="${encodeURIComponent(stickerUrl)}" data-pack-id="${packId}" onclick="_onStickerClick(this)">
                <div id="${uid}" style="width:120px;height:120px;cursor:pointer;" title="Нажмите, чтобы посмотреть пак" data-lottie-src="${encodeURIComponent(stickerUrl)}"></div>
            </div>`;
        } else {
            content = `<div class="sticker-message" data-sticker-url="${encodeURIComponent(stickerUrl)}" data-pack-id="${packId}" onclick="_onStickerClick(this)">
                <img data-src="${encodeURIComponent(stickerUrl)}" alt="Стикер" style="width:120px;height:120px;object-fit:contain;cursor:pointer;border-radius:8px;" title="Нажмите, чтобы посмотреть пак">
            </div>`;
        }
    }
    // Обычное текстовое сообщение
    else {
        const editedText = msg.edited_at ? ` <span class="edited-text">(изм.)</span>` : '';
        if (msg.message_type === 'poll' && msg.poll_id) {
            content = `<div class="poll-widget" id="poll-widget-${msg.poll_id}"><div class="poll-loading"><i class="fas fa-spinner fa-spin"></i> Загрузка опроса...</div></div>`;
            setTimeout(() => loadAndShowPoll(msg.poll_id, `poll-widget-${msg.poll_id}`), 50);
        } else if (msg.message_type === 'gift' && msg.gift) {
            content = renderGiftMessage(msg);
        } else {
            content = `<div class="message-content">${renderBotContent(msg.content)}${editedText}</div>`;
        }
    }

    return `
        <div class="message ${messageClass}" data-message-id="${msg.id}" data-is-mine="${msg.is_mine ? '1' : '0'}" data-is-deleted="${msg.is_deleted ? '1' : '0'}" data-is-group="1">
            ${senderInfo}
            ${msg.reply_to ? `<div class="reply-preview" onclick="scrollToMsg(${msg.reply_to.id})"><span class="reply-sender">${escapeHtml(msg.reply_to.sender_name)}</span><span class="reply-text">${escapeHtml((msg.reply_to.content||'').slice(0,80))}</span></div>` : ''}
            ${msg.is_paid && !msg.is_purchased ? `
                <div class="paid-post-blur">
                    <div class="paid-post-overlay">
                        <div style="font-size:32px;margin-bottom:8px;">🔒</div>
                        <div style="font-weight:700;font-size:15px;margin-bottom:4px;">Платный контент</div>
                        <div style="font-size:13px;color:rgba(255,255,255,0.8);margin-bottom:12px;">${msg.paid_price} ✨ искр</div>
                        <button class="paid-post-buy-btn" onclick="buyPaidPost(${msg.paid_post_id}, this)">
                            ✨ Купить за ${msg.paid_price} искр
                        </button>
                    </div>
                </div>` : content}
            ${renderBotButtons(msg.bot_buttons)}
            <div class="message-time">${msg.timestamp_iso ? formatMsgTime(msg.timestamp_iso) : msg.timestamp}</div>
            <div class="spark-reaction-bar" id="spark-bar-${msg.id}"></div>
        </div>
    `;
}

// Добавляем переменную для текущей группы
let currentGroupId = null;
let _currentGroupData = null; // данные текущей открытой группы

// Socket.IO для групп (добавляем обработчик после подключения)
function setupGroupSocketHandlers() {
    if (!socket) return;
    
    socket.on('new_group_message', function(data) {
        console.log('New group message:', data);
        if (currentGroupId === data.group_id) {
            // Определяем is_mine на клиенте
            const currentUserId = parseInt(document.body.getAttribute('data-user-id'));
            data.message.is_mine = data.message.sender_id === currentUserId;
            addGroupMessageToChat(data.message);
            
            // Отмечаем как прочитанное, так как группа открыта
            markGroupAsRead(data.group_id);
        } else {
            // Обновляем только конкретный элемент в списке
            const groupItem = document.querySelector(`.chat-item[data-group-id="${data.group_id}"]`);
            if (groupItem) {
                const lastMsgEl = groupItem.querySelector('.chat-last-message');
                if (lastMsgEl) lastMsgEl.textContent = data.message.content || '';
                const timeEl = groupItem.querySelector('.chat-time');
                if (timeEl) timeEl.textContent = data.message.timestamp_iso ? formatMsgTime(data.message.timestamp_iso) : (data.message.timestamp || '');
                let badge = groupItem.querySelector('.unread-badge');
                if (badge) {
                    badge.textContent = (parseInt(badge.textContent) || 0) + 1;
                } else {
                    badge = document.createElement('div');
                    badge.className = 'unread-badge';
                    badge.textContent = '1';
                    const timeDiv = groupItem.querySelector('.chat-time');
                    if (timeDiv) groupItem.insertBefore(badge, timeDiv);
                    else groupItem.appendChild(badge);
                }
            } else {
                loadAllChats();
            }

            // Всплывающее уведомление для группы/канала (не для своих сообщений)
            const currentUserId = parseInt(document.body.getAttribute('data-user-id'));
            if (data.message.sender_id !== currentUserId) {
                const grpItem = document.querySelector(`.chat-item[data-group-id="${data.group_id}"]`);
                const grpName = grpItem ? (grpItem.querySelector('.chat-name')?.textContent || 'Группа') : (data.group_name || 'Группа');
                const grpAvatar = grpItem ? grpItem.querySelector('.chat-avatar') : null;
                const grpColor = grpAvatar ? grpAvatar.style.backgroundColor : '#667eea';
                const grpLetter = grpAvatar ? (grpAvatar.textContent.trim() || grpName[0]) : grpName[0];
                showMessageNotification({
                    name: grpName,
                    text: (data.message.sender_name ? data.message.sender_name + ': ' : '') + (data.message.content || ''),
                    avatarUrl: grpAvatar && grpAvatar.style.backgroundImage ? grpAvatar.style.backgroundImage.replace(/url\(['"]?(.*?)['"]?\)/, '$1') : null,
                    avatarColor: grpColor,
                    avatarLetter: grpLetter,
                    onClick: () => openGroup(data.group_id, grpName),
                });
            }
        }
    });
}

// Добавление сообщения группы в чат
function addGroupMessageToChat(message) {
    const container = document.getElementById('messages-container');
    const noMessages = container.querySelector('.no-messages');
    
    if (noMessages) {
        container.innerHTML = '';
    }
    
    // Проверяем, не добавлено ли уже это сообщение (избегаем дубликатов)
    const existingMessage = container.querySelector(`[data-message-id="${message.id}"]`);
    if (existingMessage) {
        console.log('Message already exists, skipping:', message.id);
        return;
    }
    
    const messageHtml = createGroupMessageHTML(message);
    container.insertAdjacentHTML('beforeend', messageHtml);
    _fixStickerImages(container);
    scrollToBottom();
}

// Экспортируем функции
window.switchTab = switchTab;
window.openGroup = openGroup;
window.loadGroups = loadGroups;


// Возврат к списку чатов (для мобильных)
function backToChats() {
    const sidebar = document.querySelector('.sidebar');
    const chatArea = document.getElementById('chat-area');

    if (sidebar && chatArea) {
        sidebar.classList.remove('mobile-hidden');
        chatArea.classList.remove('mobile-active');

        // Скрываем активный чат
        document.getElementById('chat-active').style.display = 'none';
        document.getElementById('chat-welcome').style.display = 'flex';
        document.getElementById('chat-area')?.classList.remove('personal-chat-open');
        document.getElementById('call-btn')?.removeAttribute('data-visible');
        document.getElementById('clear-history-btn')?.removeAttribute('data-visible');

        // Сбрасываем текущий чат
        currentChatUserId = null;
        currentGroupId = null;
    }
}


// Просмотр информации о чате/группе
function viewChatInfo() {
    if (currentGroupId) {
        // Показываем информацию о группе
        showGroupInfo(currentGroupId);
    } else if (currentChatUserId) {
        // Показываем информацию о пользователе
        showUserInfo(currentChatUserId);
    }
}

// Показать информацию о пользователе
async function showUserInfo(userId) {
    try {
        const response = await fetch(`/api/user/${userId}`);
        if (!response.ok) throw new Error('Ошибка загрузки пользователя');
        
        const data = await response.json();
        
        // Определяем стиль аватарки
        let avatarStyle;
        let avatarContent;
        
        if (data.avatar_url) {
            avatarStyle = `background-image: url('${data.avatar_url}'); background-size: cover; background-position: center; background-color: ${data.avatar_color};`;
            avatarContent = '';
        } else {
            avatarStyle = `background: ${data.avatar_color};`;
            avatarContent = data.avatar_letter;
        }
        
        // Создаем модальное окно с информацией
        const modal = document.createElement('div');
        modal.className = 'modal active';
        modal.innerHTML = `
            <div class="modal-content" style="max-width: 400px;">
                <div class="modal-header">
                    <h2><i class="fas fa-${data.is_bot ? 'robot' : 'user'}"></i> ${data.is_bot ? 'Бот' : 'Профиль'}</h2>
                    <button class="close-btn" onclick="this.closest('.modal').remove()">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
                <div class="modal-body">
                    <div style="text-align: center; margin-bottom: 20px;">
                        <div class="chat-avatar" style="${avatarStyle} width: 80px; height: 80px; font-size: 32px; margin: 0 auto;">
                            ${avatarContent}
                        </div>
                        <h3 style="margin-top: 15px; display: flex; align-items: center; justify-content: center; gap: 8px; flex-wrap: wrap;">
                            ${escapeHtml(data.display_name)}
                            ${data.is_bot ? '<span style="background:linear-gradient(135deg,#667eea,#764ba2);color:white;font-size:11px;padding:2px 8px;border-radius:10px;font-weight:700;"><i class="fas fa-robot"></i> BOT</span>' : ''}
                            ${data.is_verified ? '<span class="verified-badge"><i class="fas fa-check-circle"></i></span>' : ''}
                            ${data.is_premium ? '<span class="premium-badge"><i class="fas fa-crown"></i> PRO</span>' : ''}
                            ${data.premium_emoji ? `<span class="premium-emoji">${data.premium_emoji}</span>` : ''}
                        </h3>
                        <p style="color: #a0aec0;">@${escapeHtml(data.username)}</p>
                    </div>
                    
                    ${data.bio ? `
                        <div style="margin-bottom: 20px; padding: 15px; background: var(--bg-secondary); border-radius: 8px;">
                            <p style="margin: 0;">${escapeHtml(data.bio)}</p>
                        </div>
                    ` : ''}
                    
                    ${!data.is_bot ? `
                    ${data.status_text ? `<div style="text-align:center;margin-bottom:10px;font-size:14px;color:var(--text-secondary);font-style:italic;">"${escapeHtml(data.status_text)}"</div>` : ''}
                    <div style="display:flex;gap:6px;color:#a0aec0;font-size:13px;justify-content:center;margin-bottom:8px;">
                        <span style="display:flex;align-items:center;gap:5px;">
                            <span style="width:8px;height:8px;border-radius:50%;background:${data.is_online ? '#48bb78' : '#a0aec0'};display:inline-block;flex-shrink:0;"></span>
                            ${data.is_online ? 'В сети' : (data.last_seen ? 'был(а) ' + formatLastSeen(data.last_seen) : 'Не в сети')}
                        </span>
                    </div>
                    <div style="display: flex; gap: 10px; color: #a0aec0; font-size: 14px; justify-content: center;">
                        <span><i class="fas fa-calendar"></i> На сайте с ${data.created_at}</span>
                    </div>
                    <div class="reputation-bar-wrap" style="margin-top:14px;" id="rep-wrap-${data.id}">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                            <span style="font-size:13px;font-weight:600;"><i class="fas fa-star" style="color:#d69e2e;margin-right:4px;"></i>Репутация</span>
                            <span id="rep-val-${data.id}" style="font-size:13px;font-weight:700;">...</span>
                        </div>
                        <div class="reputation-bar-bg">
                            <div class="reputation-bar-fill" id="rep-bar-${data.id}" style="width:0%;background:#667eea;"></div>
                        </div>
                        <div class="reputation-label" id="rep-level-${data.id}"></div>
                    </div>
                    ` : ''}
                </div>
            </div>
        `;
        document.body.appendChild(modal);
        
        // Закрытие по клику вне
        modal.addEventListener('click', function(e) {
            if (e.target === modal) {
                modal.remove();
            }
        });

        // Загружаем репутацию
        if (!data.is_bot) {
            fetch(`/api/user/${userId}/reputation`)
                .then(r => r.json())
                .then(rep => {
                    const valEl = document.getElementById(`rep-val-${userId}`);
                    const barEl = document.getElementById(`rep-bar-${userId}`);
                    const lvlEl = document.getElementById(`rep-level-${userId}`);
                    if (valEl) valEl.textContent = rep.reputation + '%';
                    if (barEl) { barEl.style.width = rep.reputation + '%'; barEl.style.background = rep.color; }
                    if (lvlEl) { lvlEl.textContent = rep.level; lvlEl.style.color = rep.color; }
                }).catch(() => {});

            // Кнопки действий
            const actionsDiv = document.createElement('div');
            actionsDiv.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;justify-content:center;margin-top:16px;padding:0 20px 16px;';
            checkBlockStatus(userId).then(bs => {
                actionsDiv.innerHTML = `
                    <button onclick="openSecretChat(${userId},'${escapeHtml(data.display_name)}');this.closest('.modal').remove();"
                        style="padding:8px 16px;border-radius:10px;border:1px solid #667eea;background:transparent;color:#667eea;font-size:13px;cursor:pointer;display:flex;align-items:center;gap:6px;">
                        <i class="fas fa-lock"></i> Секретный чат
                    </button>
                    <button id="block-btn-${userId}" onclick="toggleBlockFromInfo(${userId},'${escapeHtml(data.display_name)}',this)"
                        style="padding:8px 16px;border-radius:10px;border:1px solid ${bs.i_blocked ? '#38a169' : '#e53e3e'};background:transparent;color:${bs.i_blocked ? '#38a169' : '#e53e3e'};font-size:13px;cursor:pointer;display:flex;align-items:center;gap:6px;">
                        <i class="fas fa-${bs.i_blocked ? 'unlock' : 'ban'}"></i> ${bs.i_blocked ? 'Разблокировать' : 'Заблокировать'}
                    </button>
                    <a href="/u/${escapeHtml(data.username)}" target="_self"
                        style="padding:8px 16px;border-radius:10px;border:1px solid var(--border-color);background:transparent;color:var(--text-secondary);font-size:13px;cursor:pointer;display:flex;align-items:center;gap:6px;text-decoration:none;">
                        <i class="fas fa-external-link-alt"></i> Профиль
                    </a>
                `;
                modal.querySelector('.modal-content').appendChild(actionsDiv);
            });
        }
    } catch (error) {
        console.error('Ошибка загрузки информации о пользователе:', error);
        showError('Не удалось загрузить информацию');
    }
}

async function toggleBlockFromInfo(userId, name, btn) {
    const isBlocked = btn.textContent.includes('Разблокировать');
    if (isBlocked) {
        await unblockUser(userId);
        btn.innerHTML = '<i class="fas fa-ban"></i> Заблокировать';
        btn.style.color = '#e53e3e'; btn.style.borderColor = '#e53e3e';
    } else {
        if (!confirm(`Заблокировать ${name}?`)) return;
        await blockUser(userId);
        btn.innerHTML = '<i class="fas fa-unlock"></i> Разблокировать';
        btn.style.color = '#38a169'; btn.style.borderColor = '#38a169';
    }
}

// Показать информацию о группе
async function showGroupInfo(groupId) {
    try {
        const response = await fetch(`/groups/${groupId}`);
        if (!response.ok) throw new Error('Ошибка загрузки группы');
        
        const data = await response.json();
        
        // Определяем стиль аватарки группы
        let groupAvatarStyle;
        let groupAvatarContent;
        
        if (data.group.avatar_url) {
            groupAvatarStyle = `background-image: url('${data.group.avatar_url}'); background-size: cover; background-position: center; background-color: ${data.group.avatar_color};`;
            groupAvatarContent = '';
        } else {
            groupAvatarStyle = `background: ${data.group.avatar_color};`;
            groupAvatarContent = data.group.avatar_letter || data.group.name[0].toUpperCase();
        }
        
        // Создаем модальное окно с информацией
        const modal = document.createElement('div');
        modal.className = 'modal active';
        modal.innerHTML = `
            <div class="modal-content" style="max-width: 500px;">
                <div class="modal-header">
                    <h2><i class="fas fa-info-circle"></i> Информация о ${data.group.is_channel ? 'канале' : 'группе'}</h2>
                    <button class="close-btn" onclick="this.closest('.modal').remove()">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
                <div class="modal-body">
                    <div style="text-align: center; margin-bottom: 20px;">
                        <div class="group-avatar" style="${groupAvatarStyle} width: 80px; height: 80px; font-size: 32px; margin: 0 auto;">
                            ${groupAvatarContent}
                        </div>
                        <h3 style="margin-top: 15px;">${escapeHtml(data.group.name)}</h3>
                        <p style="color: #a0aec0;">${data.group.is_channel ? 'Канал' : 'Группа'} • ${data.members.length} участников</p>
                    </div>
                    
                    ${data.group.description ? `<p style="margin-bottom: 20px;">${escapeHtml(data.group.description)}</p>` : ''}
                    
                    <h4 style="margin-bottom: 10px;"><i class="fas fa-users"></i> Участники</h4>
                    <div class="members-list" style="max-height: 300px; overflow-y: auto;">
                        ${data.members.map(member => {
                            // Определяем стиль аватарки участника
                            let memberAvatarStyle;
                            let memberAvatarContent;
                            
                            if (member.avatar_url) {
                                memberAvatarStyle = `background-image: url('${member.avatar_url}'); background-size: cover; background-position: center; background-color: ${member.avatar_color};`;
                                memberAvatarContent = '';
                            } else {
                                memberAvatarStyle = `background: ${member.avatar_color};`;
                                memberAvatarContent = member.avatar_letter;
                            }
                            
                            return `
                            <div style="display: flex; align-items: center; padding: 10px; border-bottom: 1px solid var(--border-color);">
                                <div class="chat-avatar" style="${memberAvatarStyle} width: 40px; height: 40px; font-size: 16px; margin-right: 12px;">
                                    ${memberAvatarContent}
                                </div>
                                <div style="flex: 1;">
                                    <div style="font-weight: 600;">${escapeHtml(member.display_name)}</div>
                                    <div style="font-size: 12px; color: #a0aec0;">@${escapeHtml(member.username)}</div>
                                </div>
                                ${member.is_admin ? '<span style="background: var(--primary-color); color: white; padding: 2px 8px; border-radius: 4px; font-size: 11px;">АДМИН</span>' : ''}
                            </div>
                        `;
                        }).join('')}
                    </div>
                    
                    ${data.group.is_admin ? `
                        <button class="btn" style="width: 100%; margin-top: 15px; background: #e53e3e; color: white;" onclick="leaveGroup(${groupId})">
                            <i class="fas fa-sign-out-alt"></i> Покинуть ${data.group.is_channel ? 'канал' : 'группу'}
                        </button>
                    ` : ''}
                </div>
            </div>
        `;
        document.body.appendChild(modal);
        
        // Закрытие по клику вне
        modal.addEventListener('click', function(e) {
            if (e.target === modal) {
                modal.remove();
            }
        });
    } catch (error) {
        console.error('Ошибка загрузки информации о группе:', error);
        showError('Не удалось загрузить информацию');
    }
}

// Покинуть группу
async function leaveGroup(groupId) {
    if (!confirm('Вы уверены, что хотите покинуть эту группу?')) return;
    
    try {
        const response = await fetch(`/groups/${groupId}/leave`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (!response.ok) throw new Error('Ошибка');
        
        const data = await response.json();
        if (data.success) {
            // Закрываем модальное окно
            document.querySelector('.modal')?.remove();
            
            // Возвращаемся к списку групп
            currentGroupId = null;
            document.getElementById('chat-active').style.display = 'none';
            document.getElementById('chat-welcome').style.display = 'flex';
            
            // Обновляем список групп
            loadGroups();
            
            showError('Вы покинули группу', 'success');
        }
    } catch (error) {
        console.error('Ошибка:', error);
        showError('Не удалось покинуть группу');
    }
}

// ─── Информация о группе / канале (с вкладками) ───────────────────────────────

async function showGroupInfo(groupId) {
    try {
        const response = await fetch(`/groups/${groupId}`);
        if (!response.ok) throw new Error('Ошибка загрузки группы');
        const data = await response.json();
        _renderGroupInfoModal(data, groupId);
    } catch (error) {
        console.error('Ошибка загрузки информации о группе:', error);
        showError('Не удалось загрузить информацию');
    }
}

function _renderGroupInfoModal(data, groupId) {
    const g = data.group;
    const members = data.members || [];
    const isCreator = g.is_creator;
    const isAdmin = g.is_admin;
    const typeName = g.is_channel ? 'канале' : 'группе';
    const typeNameAcc = g.is_channel ? 'канал' : 'группу';

    const avatarStyle = g.avatar_url
        ? `background-image:url('${g.avatar_url}');background-size:cover;background-position:center;background-color:${g.avatar_color};`
        : `background:${g.avatar_color};`;
    const avatarContent = g.avatar_url ? '' : (g.avatar_letter || g.name[0].toUpperCase());

    const modal = document.createElement('div');
    modal.className = 'modal active';
    modal.id = 'group-info-modal';
    modal.innerHTML = `
        <div class="modal-content" style="max-width:520px;padding:0;overflow:hidden;max-height:90vh;display:flex;flex-direction:column;">
            <div class="modal-header" style="padding:16px 20px;flex-shrink:0;">
                <h2 style="font-size:17px;"><i class="fas fa-info-circle"></i> Информация о ${typeName}</h2>
                <button class="close-btn" onclick="this.closest('.modal').remove()"><i class="fas fa-times"></i></button>
            </div>

            <!-- Шапка группы -->
            <div style="display:flex;flex-direction:column;align-items:center;padding:20px;border-bottom:1px solid var(--border-color);flex-shrink:0;">
                <div class="group-avatar" style="${avatarStyle}width:80px;height:80px;font-size:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;margin-bottom:12px;">${avatarContent}</div>
                <div style="font-size:20px;font-weight:700;">${escapeHtml(g.name)}</div>
                ${g.username ? `<div style="color:var(--text-secondary);font-size:13px;">@${escapeHtml(g.username)}</div>` : ''}
                <div style="color:var(--text-secondary);font-size:13px;margin-top:4px;">${g.is_channel ? 'Канал' : 'Группа'} · ${members.length} участников</div>
                ${g.description ? `<div style="margin-top:10px;text-align:center;font-size:14px;color:var(--text-primary);">${escapeHtml(g.description)}</div>` : ''}
            </div>

            <!-- Вкладки -->
            <div style="display:flex;border-bottom:1px solid var(--border-color);flex-shrink:0;">
                <button class="ginfo-tab active" data-tab="members" onclick="switchGinfoTab('members')" style="flex:1;padding:12px;background:none;border:none;border-bottom:2px solid var(--primary-color);color:var(--primary-color);cursor:pointer;font-size:14px;font-weight:600;">
                    <i class="fas fa-users"></i> Участники
                </button>
                ${isAdmin ? `<button class="ginfo-tab" data-tab="settings" onclick="switchGinfoTab('settings')" style="flex:1;padding:12px;background:none;border:none;border-bottom:2px solid transparent;color:var(--text-secondary);cursor:pointer;font-size:14px;font-weight:600;">
                    <i class="fas fa-cog"></i> Настройки
                </button>` : ''}
            </div>

            <!-- Скроллируемая область -->
            <div style="overflow-y:auto;flex:1;min-height:0;">

            <!-- Вкладка участников -->
            <div id="ginfo-tab-members">
                ${members.map(m => {
                    const mAvStyle = m.avatar_url
                        ? `background-image:url('${m.avatar_url}');background-size:cover;background-position:center;background-color:${m.avatar_color};`
                        : `background:${m.avatar_color};`;
                    const mAvContent = m.avatar_url ? '' : m.avatar_letter;
                    const canManage = isAdmin && !m.is_self;
                    return `
                    <div style="display:flex;align-items:center;padding:10px 16px;border-bottom:1px solid var(--border-color);">
                        <div class="chat-avatar" style="${mAvStyle}width:40px;height:40px;min-width:40px;font-size:16px;margin-right:12px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;">${mAvContent}</div>
                        <div style="flex:1;min-width:0;">
                            <div style="font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(m.display_name)}</div>
                            <div style="font-size:12px;color:var(--text-secondary);">@${escapeHtml(m.username)}</div>
                        </div>
                        <div style="display:flex;align-items:center;gap:6px;">
                            ${m.is_admin ? `<span style="background:var(--primary-color);color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;">АДМИН</span>` : ''}
                            ${canManage ? `
                                <button onclick="showMemberPermissions(${groupId},${m.id},'${escapeHtml(m.display_name)}')" title="Права" style="background:none;border:none;cursor:pointer;color:var(--text-secondary);font-size:14px;padding:4px;">
                                    <i class="fas fa-sliders-h"></i>
                                </button>
                            ` : ''}
                            ${canManage && isCreator ? `
                                <button onclick="setMemberRole(${groupId},${m.id},${m.is_admin})" title="${m.is_admin ? 'Снять права' : 'Сделать админом'}" style="background:none;border:none;cursor:pointer;color:var(--text-secondary);font-size:14px;padding:4px;">
                                    <i class="fas fa-${m.is_admin ? 'user-minus' : 'user-shield'}"></i>
                                </button>
                                <button onclick="removeMember(${groupId},${m.id})" title="Удалить" style="background:none;border:none;cursor:pointer;color:#e53e3e;font-size:14px;padding:4px;">
                                    <i class="fas fa-user-times"></i>
                                </button>
                            ` : ''}
                        </div>
                    </div>`;
                }).join('')}
            </div>

            <!-- Вкладка настроек -->
            ${isAdmin ? `
            <div id="ginfo-tab-settings" style="display:none;padding:16px;">
                <div style="margin-bottom:14px;">
                    <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:4px;">Название</label>
                    <input id="gs-name" type="text" value="${escapeHtml(g.name)}" style="width:100%;padding:8px 12px;border:1px solid var(--border-color);border-radius:8px;font-size:14px;box-sizing:border-box;">
                </div>
                <div style="margin-bottom:14px;">
                    <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:4px;">Описание</label>
                    <textarea id="gs-desc" rows="3" style="width:100%;padding:8px 12px;border:1px solid var(--border-color);border-radius:8px;font-size:14px;resize:vertical;box-sizing:border-box;">${escapeHtml(g.description || '')}</textarea>
                </div>
                <div style="margin-bottom:14px;">
                    <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:4px;">Username (без @)</label>
                    <input id="gs-username" type="text" value="${escapeHtml(g.username || '')}" placeholder="Необязательно" style="width:100%;padding:8px 12px;border:1px solid var(--border-color);border-radius:8px;font-size:14px;box-sizing:border-box;">
                </div>
                <div style="margin-bottom:14px;display:flex;align-items:center;gap:10px;">
                    <input id="gs-public" type="checkbox" ${g.is_public ? 'checked' : ''} style="width:16px;height:16px;cursor:pointer;">
                    <label for="gs-public" style="font-size:14px;cursor:pointer;">Публичная ${g.is_channel ? 'канал' : 'группа'}</label>
                </div>

                <!-- Аватарка -->
                <div style="margin-bottom:14px;">
                    <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:6px;">Аватарка</label>
                    <div style="display:flex;gap:8px;align-items:center;">
                        <div class="group-avatar" id="gs-avatar-preview" style="${avatarStyle}width:48px;height:48px;min-width:48px;font-size:20px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;">${avatarContent}</div>
                        <label style="cursor:pointer;padding:8px 14px;border:1px solid var(--border-color);border-radius:8px;font-size:13px;">
                            <i class="fas fa-upload"></i> Загрузить
                            <input type="file" accept="image/*" style="display:none;" onchange="uploadGroupAvatarFromSettings(${groupId}, this)">
                        </label>
                        ${g.avatar_url ? `<button onclick="deleteGroupAvatarFromSettings(${groupId})" style="padding:8px 14px;background:none;border:1px solid #e53e3e;border-radius:8px;color:#e53e3e;cursor:pointer;font-size:13px;"><i class="fas fa-trash"></i> Удалить</button>` : ''}
                    </div>
                </div>

                <!-- Инвайт-ссылка -->
                <div style="margin-bottom:14px;">
                    <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:6px;"><i class="fas fa-link"></i> Инвайт-ссылка</label>
                    <div style="display:flex;gap:8px;">
                        <input id="gs-invite-link" type="text" readonly value="${g.invite_link ? window.location.origin + '/invite/' + g.invite_link : ''}" placeholder="Нажмите «Создать»" style="flex:1;padding:8px 12px;border:1px solid var(--border-color);border-radius:8px;font-size:13px;">
                        <button onclick="copyInviteLink()" title="Копировать" style="padding:8px 12px;border:1px solid var(--border-color);border-radius:8px;cursor:pointer;">
                            <i class="fas fa-copy"></i>
                        </button>
                        <button onclick="regenerateInviteLink(${groupId})" title="Пересоздать" style="padding:8px 12px;border:1px solid var(--border-color);border-radius:8px;cursor:pointer;">
                            <i class="fas fa-sync-alt"></i>
                        </button>
                    </div>
                </div>

                <!-- Медленный режим -->
                ${!g.is_channel ? `<div style="margin-bottom:14px;">
                    <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:4px;"><i class="fas fa-hourglass-half"></i> Медленный режим (сек между сообщениями)</label>
                    <input id="gs-slow-mode" type="number" min="0" max="86400" value="${g.slow_mode_seconds||0}" style="width:100%;padding:8px 12px;border:1px solid var(--border-color);border-radius:8px;font-size:14px;box-sizing:border-box;">
                    <button onclick="saveSlowMode(${groupId})" style="margin-top:6px;padding:6px 14px;background:var(--primary-color);color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:13px;">Сохранить</button>
                </div>
                <div style="margin-bottom:14px;">
                    <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:4px;"><i class="fas fa-door-open"></i> Приветствие новых участников</label>
                    <input id="gs-welcome" type="text" value="${escapeHtml(g.welcome_message||'')}" placeholder="Привет, {name}! Добро пожаловать!" style="width:100%;padding:8px 12px;border:1px solid var(--border-color);border-radius:8px;font-size:14px;box-sizing:border-box;">
                    <small style="color:#a0aec0;font-size:11px;">{name} — имя пользователя</small>
                    <button onclick="saveWelcomeMessage(${groupId})" style="margin-top:6px;padding:6px 14px;background:var(--primary-color);color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:13px;">Сохранить</button>
                </div>
                <div style="margin-bottom:14px;">
                    <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:4px;"><i class="fas fa-ban"></i> Запрещённые слова (через запятую)</label>
                    <input id="gs-spam-kw" type="text" value="${escapeHtml((g.spam_keywords||[]).join(', '))}" placeholder="спам, реклама, ..." style="width:100%;padding:8px 12px;border:1px solid var(--border-color);border-radius:8px;font-size:14px;box-sizing:border-box;">
                    <button onclick="saveSpamKeywords(${groupId})" style="margin-top:6px;padding:6px 14px;background:var(--primary-color);color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:13px;">Сохранить</button>
                </div>` : ''}

                
                <!-- Роли участников -->
                ${!g.is_channel ? `<div style="margin-bottom:14px;">
                    <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:6px;"><i class="fas fa-shield-alt"></i> Роли участников</label>
                    <div id="gs-roles-list" style="margin-bottom:8px;"></div>
                    <button onclick="loadGroupRolesUI(${groupId})" style="padding:6px 14px;border:1px solid var(--border-color);border-radius:8px;cursor:pointer;font-size:13px;margin-bottom:6px;">
                        <i class="fas fa-sync-alt"></i> Загрузить роли и участников
                    </button>
                    <div style="display:flex;gap:6px;margin-top:6px;">
                        <input id="gs-new-role-name" type="text" placeholder="Название роли" style="flex:1;padding:7px 10px;border:1px solid var(--border-color);border-radius:8px;font-size:13px;">
                        <input id="gs-new-role-color" type="color" value="#667eea" style="width:36px;height:34px;border:1px solid var(--border-color);border-radius:8px;cursor:pointer;padding:2px;">
                        <button onclick="createGroupRoleUI(${groupId})" style="padding:6px 12px;background:var(--primary-color);color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:13px;">
                            <i class="fas fa-plus"></i>
                        </button>
                    </div>
                </div>` : ''}
<button onclick="saveGroupSettings(${groupId})" style="width:100%;padding:10px;background:var(--primary-color);color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:14px;font-weight:600;margin-bottom:8px;">
                    <i class="fas fa-save"></i> Сохранить изменения
                </button>
                ${isCreator ? `
                <button onclick="deleteGroup(${groupId})" style="width:100%;padding:10px;background:#e53e3e;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:14px;font-weight:600;">
                    <i class="fas fa-trash"></i> Удалить ${typeNameAcc}
                </button>
                <button onclick="showTransferGroup(${groupId})" style="width:100%;padding:10px;background:#744210;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:14px;font-weight:600;margin-top:8px;">
                    <i class="fas fa-exchange-alt"></i> Передать ${typeNameAcc}
                </button>` : ''}
            </div>` : ''}

            </div><!-- конец скроллируемой области -->

            <!-- Нижние кнопки -->
            <div style="padding:12px 16px;border-top:1px solid var(--border-color);display:flex;gap:8px;flex-shrink:0;">
                <button onclick="toggleGroupMute(${groupId})" style="flex:1;padding:9px;border:1px solid var(--border-color);border-radius:8px;cursor:pointer;font-size:13px;" id="ginfo-mute-btn">
                    <i class="fas ${g.is_muted ? 'fa-bell' : 'fa-bell-slash'}"></i> ${g.is_muted ? 'Включить звук' : 'Выключить звук'}
                </button>
                <button onclick="leaveGroup(${groupId})" style="flex:1;padding:9px;background:#e53e3e;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:13px;">
                    <i class="fas fa-sign-out-alt"></i> Покинуть
                </button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
}

function switchGinfoTab(tab) {
    document.querySelectorAll('.ginfo-tab').forEach(b => {
        b.style.borderBottomColor = 'transparent';
        b.style.color = 'var(--text-secondary)';
        b.classList.remove('active');
    });
    const activeBtn = document.querySelector(`.ginfo-tab[data-tab="${tab}"]`);
    if (activeBtn) {
        activeBtn.style.borderBottomColor = 'var(--primary-color)';
        activeBtn.style.color = 'var(--primary-color)';
        activeBtn.classList.add('active');
    }
    document.getElementById('ginfo-tab-members').style.display = tab === 'members' ? 'block' : 'none';
    const settingsTab = document.getElementById('ginfo-tab-settings');
    if (settingsTab) settingsTab.style.display = tab === 'settings' ? 'block' : 'none';
}

async function saveGroupSettings(groupId) {
    const name = document.getElementById('gs-name').value.trim();
    const desc = document.getElementById('gs-desc').value.trim();
    const username = document.getElementById('gs-username').value.trim();
    const isPublic = document.getElementById('gs-public').checked;
    if (!name) { showError('Название не может быть пустым'); return; }
    try {
        const r = await fetch(`/groups/${groupId}/update`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, description: desc, username, is_public: isPublic })
        });
        const d = await r.json();
        if (d.success) {
            showError('Настройки сохранены', 'success');
            document.getElementById('group-info-modal')?.remove();
            // Обновляем заголовок чата
            const chatUsername = document.getElementById('chat-username');
            if (chatUsername) chatUsername.textContent = name;
            loadAllChats();
        } else {
            showError(d.error || 'Ошибка сохранения');
        }
    } catch (e) { showError('Ошибка сохранения'); }
}

async function deleteGroup(groupId) {
    if (!confirm('Удалить группу? Это действие необратимо!')) return;
    try {
        const r = await fetch(`/groups/${groupId}/delete`, { method: 'POST' });
        const d = await r.json();
        if (d.success) {
            document.getElementById('group-info-modal')?.remove();
            currentGroupId = null;
            document.getElementById('chat-active').style.display = 'none';
            document.getElementById('chat-welcome').style.display = 'flex';
            loadAllChats();
            showError('Группа удалена', 'success');
        } else {
            showError(d.error || 'Ошибка удаления');
        }
    } catch (e) { showError('Ошибка удаления'); }
}

async function removeMember(groupId, userId) {
    if (!confirm('Удалить участника из группы?')) return;
    try {
        const r = await fetch(`/groups/${groupId}/members/${userId}/remove`, { method: 'POST' });
        const d = await r.json();
        if (d.success) {
            showError('Участник удалён', 'success');
            document.getElementById('group-info-modal')?.remove();
            showGroupInfo(groupId);
        } else { showError(d.error || 'Ошибка'); }
    } catch (e) { showError('Ошибка'); }
}

async function setMemberRole(groupId, userId, isAdmin) {
    const action = isAdmin ? 'Снять права администратора?' : 'Назначить администратором?';
    if (!confirm(action)) return;
    try {
        const r = await fetch(`/groups/${groupId}/members/${userId}/role`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_admin: !isAdmin })
        });
        const d = await r.json();
        if (d.success) {
            document.getElementById('group-info-modal')?.remove();
            showGroupInfo(groupId);
        } else { showError(d.error || 'Ошибка'); }
    } catch (e) { showError('Ошибка'); }
}

async function toggleGroupMute(groupId) {
    try {
        const r = await fetch(`/groups/${groupId}/mute`, { method: 'POST' });
        const d = await r.json();
        if (d.success) {
            const btn = document.getElementById('ginfo-mute-btn');
            if (btn) {
                const muted = d.is_muted;
                btn.innerHTML = `<i class="fas ${muted ? 'fa-bell' : 'fa-bell-slash'}"></i> ${muted ? 'Включить звук' : 'Выключить звук'}`;
            }
            showError(d.is_muted ? 'Уведомления отключены' : 'Уведомления включены', 'success');
        }
    } catch (e) { showError('Ошибка'); }
}

async function regenerateInviteLink(groupId) {
    try {
        const r = await fetch(`/groups/${groupId}/invite_link`, { method: 'POST' });
        const d = await r.json();
        if (d.invite_link) {
            const fullLink = window.location.origin + '/invite/' + d.invite_link;
            const input = document.getElementById('gs-invite-link');
            if (input) input.value = fullLink;
            showError('Ссылка обновлена', 'success');
        }
    } catch (e) { showError('Ошибка'); }
}

function copyInviteLink() {
    const input = document.getElementById('gs-invite-link');
    if (!input || !input.value) { showError('Сначала создайте ссылку'); return; }
    navigator.clipboard.writeText(input.value).then(() => showError('Ссылка скопирована', 'success'));
}

async function uploadGroupAvatarFromSettings(groupId, input) {
    if (!input.files || !input.files[0]) return;
    const formData = new FormData();
    formData.append('avatar', input.files[0]);
    try {
        const r = await fetch(`/groups/${groupId}/avatar`, { method: 'POST', body: formData });
        const d = await r.json();
        if (d.success) {
            const preview = document.getElementById('gs-avatar-preview');
            if (preview) {
                preview.style.backgroundImage = `url('${d.avatar_url}')`;
                preview.style.backgroundSize = 'cover';
                preview.style.backgroundPosition = 'center';
                preview.textContent = '';
            }
            // Обновляем аватарку в заголовке
            const chatAvatar = document.getElementById('chat-header-avatar');
            if (chatAvatar) {
                chatAvatar.style.backgroundImage = `url('${d.avatar_url}')`;
                chatAvatar.style.backgroundSize = 'cover';
                chatAvatar.textContent = '';
            }
            showError('Аватарка обновлена', 'success');
        } else { showError(d.error || 'Ошибка загрузки'); }
    } catch (e) { showError('Ошибка загрузки'); }
}

async function deleteGroupAvatarFromSettings(groupId) {
    if (!confirm('Удалить аватарку группы?')) return;
    try {
        const r = await fetch(`/groups/${groupId}/avatar/delete`, { method: 'POST' });
        const d = await r.json();
        if (d.success) {
            showError('Аватарка удалена', 'success');
            document.getElementById('group-info-modal')?.remove();
            showGroupInfo(groupId);
        } else { showError(d.error || 'Ошибка'); }
    } catch (e) { showError('Ошибка'); }
}

// Покинуть группу
async function leaveGroup(groupId) {
    if (!confirm('Вы уверены, что хотите покинуть эту группу?')) return;
    try {
        const r = await fetch(`/groups/${groupId}/leave`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const d = await r.json();
        if (d.success) {
            document.querySelector('.modal')?.remove();
            currentGroupId = null;
            document.getElementById('chat-active').style.display = 'none';
            document.getElementById('chat-welcome').style.display = 'flex';
            loadAllChats();
            showError('Вы покинули группу', 'success');
        } else { showError(d.error || 'Ошибка'); }
    } catch (e) { showError('Не удалось покинуть группу'); }
}

window.viewChatInfo = viewChatInfo;
window.leaveGroup = leaveGroup;
window.showGroupInfo = showGroupInfo;
window.switchGinfoTab = switchGinfoTab;
window.saveGroupSettings = saveGroupSettings;
window.deleteGroup = deleteGroup;
window.removeMember = removeMember;
window.setMemberRole = setMemberRole;
window.toggleGroupMute = toggleGroupMute;
window.regenerateInviteLink = regenerateInviteLink;
window.copyInviteLink = copyInviteLink;
window.uploadGroupAvatarFromSettings = uploadGroupAvatarFromSettings;
window.deleteGroupAvatarFromSettings = deleteGroupAvatarFromSettings;
window.leaveGroup = leaveGroup;

// Удаление сообщения в группе
async function deleteGroupMessage(messageId) {
    if (!confirm('Удалить сообщение?')) return;
    try {
        const r = await fetch(`/groups/${currentGroupId}/messages/${messageId}/delete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const d = await r.json();
        if (d.success) {
            const msgEl = document.querySelector(`[data-message-id="${messageId}"]`);
            if (msgEl) {
                msgEl.style.transition = 'opacity 0.3s, transform 0.3s';
                msgEl.style.opacity = '0';
                msgEl.style.transform = 'scale(0.95)';
                setTimeout(() => msgEl.remove(), 300);
            }
        } else {
            showError(d.error || 'Ошибка удаления');
        }
    } catch (e) { showError('Ошибка удаления'); }
}
window.deleteGroupMessage = deleteGroupMessage;


// Блокировка скролла body при открытии модального окна
function lockBodyScroll() {
    document.body.style.overflow = 'hidden';
    document.body.style.position = 'fixed';
    document.body.style.width = '100%';
    document.body.style.top = `-${window.scrollY}px`;
}

function unlockBodyScroll() {
    const scrollY = document.body.style.top;
    document.body.style.overflow = '';
    document.body.style.position = '';
    document.body.style.width = '';
    document.body.style.top = '';
    window.scrollTo(0, parseInt(scrollY || '0') * -1);
}

// Переопределяем создание модальных окон
const originalCreateElement = document.createElement.bind(document);
document.createElement = function(tagName) {
    const element = originalCreateElement(tagName);
    
    if (tagName.toLowerCase() === 'div' && element.className === 'modal active') {
        lockBodyScroll();
        
        // Добавляем обработчик закрытия
        const originalRemove = element.remove.bind(element);
        element.remove = function() {
            unlockBodyScroll();
            originalRemove();
        };
    }
    
    return element;
};

// Обновляем существующие модальные окна
document.addEventListener('DOMContentLoaded', function() {
    // Наблюдаем за добавлением модальных окон
    const observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(mutation) {
            mutation.addedNodes.forEach(function(node) {
                if (node.classList && node.classList.contains('modal') && node.classList.contains('active')) {
                    lockBodyScroll();
                    
                    // Добавляем обработчик закрытия
                    const closeBtn = node.querySelector('.close-btn');
                    if (closeBtn) {
                        closeBtn.addEventListener('click', function() {
                            unlockBodyScroll();
                        });
                    }
                    
                    // Закрытие по клику вне
                    node.addEventListener('click', function(e) {
                        if (e.target === node) {
                            unlockBodyScroll();
                        }
                    });
                }
            });
            
            mutation.removedNodes.forEach(function(node) {
                if (node.classList && node.classList.contains('modal')) {
                    unlockBodyScroll();
                }
            });
        });
    });
    
    observer.observe(document.body, { childList: true });
});

// Модальное окно добавления участника в группу
async function showAddMemberModal(groupId, groupName) {
    const modal = document.createElement('div');
    modal.className = 'modal active';
    modal.id = 'add-member-modal';
    modal.innerHTML = `
        <div class="modal-content" style="max-width: 500px;">
            <div class="modal-header">
                <h2><i class="fas fa-user-plus"></i> Добавить участника</h2>
                <button class="close-btn" onclick="this.closest('.modal').remove()">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="modal-body">
                <p style="margin-bottom: 15px; color: #a0aec0;">Группа: ${escapeHtml(groupName)}</p>
                
                <div class="search-box" style="margin-bottom: 15px;">
                    <i class="fas fa-search"></i>
                    <input type="text" id="member-search" placeholder="Поиск пользователей..." onkeyup="searchUsersToAddToGroup(this.value)">
                </div>
                
                <div id="users-to-add-group-list" style="max-height: 400px; overflow-y: auto;">
                    <div style="text-align: center; padding: 20px; color: #a0aec0;">
                        <i class="fas fa-search"></i>
                        <p>Начните поиск пользователей</p>
                    </div>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    
    // Закрытие по клику вне
    modal.addEventListener('click', function(e) {
        if (e.target === modal) {
            modal.remove();
        }
    });
    
    // Сохраняем groupId для использования в других функциях
    modal.dataset.groupId = groupId;
}

// Поиск пользователей для добавления в группу
async function searchUsersToAddToGroup(query) {
    if (!query || query.length < 2) {
        document.getElementById('users-to-add-group-list').innerHTML = `
            <div style="text-align: center; padding: 20px; color: #a0aec0;">
                <i class="fas fa-search"></i>
                <p>Начните поиск пользователей</p>
            </div>
        `;
        return;
    }
    
    try {
        const response = await fetch(`/search?q=${encodeURIComponent(query)}`);
        if (!response.ok) throw new Error('Ошибка поиска');
        
        const data = await response.json();
        const users = data.users || [];
        
        // Фильтруем текущего пользователя
        const currentUserId = parseInt(document.body.getAttribute('data-user-id'));
        const filteredUsers = users.filter(u => u.id !== currentUserId);
        
        displayUsersToAddToGroup(filteredUsers);
    } catch (error) {
        console.error('Ошибка поиска:', error);
    }
}

// Отображение пользователей для добавления в группу
function displayUsersToAddToGroup(users) {
    const container = document.getElementById('users-to-add-group-list');
    
    if (users.length === 0) {
        container.innerHTML = `
            <div style="text-align: center; padding: 20px; color: #a0aec0;">
                <i class="fas fa-user-slash"></i>
                <p>Пользователи не найдены</p>
            </div>
        `;
        return;
    }
    
    let html = '';
    users.forEach(user => {
        const avatarStyle = user.avatar_url 
            ? `background-image: url('${user.avatar_url}'); background-size: cover; background-position: center;`
            : `background: ${user.avatar_color}`;
        const avatarContent = user.avatar_url ? '' : user.avatar_letter;
        
        html += `
            <div class="user-item" style="display: flex; align-items: center; padding: 10px; border-bottom: 1px solid var(--border-color); cursor: pointer;" onclick="addMemberToGroup(${user.id}, '${escapeHtml(user.display_name || user.username)}')">
                <div class="chat-avatar" style="${avatarStyle} width: 40px; height: 40px; font-size: 16px; margin-right: 12px;">
                    ${avatarContent}
                </div>
                <div style="flex: 1;">
                    <div style="font-weight: 600;">${escapeHtml(user.display_name || user.username)}</div>
                    <div style="font-size: 12px; color: #a0aec0;">@${escapeHtml(user.username)}</div>
                </div>
                <button class="btn btn-primary" style="padding: 6px 12px; font-size: 13px;">
                    <i class="fas fa-plus"></i> Добавить
                </button>
            </div>
        `;
    });
    
    container.innerHTML = html;
}

// Добавление участника в группу
async function addMemberToGroup(userId, displayName) {
    const modal = document.getElementById('add-member-modal');
    const groupId = modal.dataset.groupId;
    
    try {
        const response = await fetch(`/groups/${groupId}/add_member`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId })
        });
        
        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.error || 'Ошибка добавления участника');
        }
        
        showError(`${displayName} добавлен в группу!`, 'success');
        modal.remove();
        
        // Перезагружаем группу чтобы обновить список участников
        const groupName = document.getElementById('chat-username').textContent;
        await openGroup(parseInt(groupId), groupName);
    } catch (error) {
        console.error('Ошибка добавления участника:', error);
        showError(error.message || 'Не удалось добавить участника');
    }
}

window.showAddMemberModal = showAddMemberModal;
window.searchUsersToAddToGroup = searchUsersToAddToGroup;
window.addMemberToGroup = addMemberToGroup;


// Отметить сообщения в личном чате как прочитанные
async function markChatAsRead(userId) {
    try {
        const chatItem = document.querySelector(`.chat-item[data-user-id="${userId}"]`);
        if (chatItem) {
            const badge = chatItem.querySelector('.unread-badge');
            if (badge) badge.remove();
        }
        
        await fetch(`/chat/${userId}/mark_read`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
    } catch (error) {
        console.error('Error marking chat as read:', error);
    }
}

// Отметить сообщения в группе как прочитанные
async function markGroupAsRead(groupId) {
    try {
        // Немедленно убираем счетчик на клиенте
        const groupItem = document.querySelector(`.chat-item[data-group-id="${groupId}"]`);
        if (groupItem) {
            const badge = groupItem.querySelector('.unread-badge');
            if (badge) {
                badge.remove();
            }
        }
        
        await fetch(`/groups/${groupId}/mark_read`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
    } catch (error) {
        console.error('Error marking group as read:', error);
    }
}


// Переключение звука канала/группы
async function toggleChannelMute() {
    if (!currentGroupId) return;
    try {
        const r = await fetch(`/groups/${currentGroupId}/mute`, { method: 'POST' });
        const d = await r.json();
        if (d.success) {
            const btn = document.getElementById('channel-mute-btn');
            const text = document.getElementById('mute-text');
            if (btn && text) {
                if (d.is_muted) {
                    btn.classList.add('muted');
                    text.textContent = 'Включить звук';
                    btn.querySelector('i').className = 'fas fa-bell';
                } else {
                    btn.classList.remove('muted');
                    text.textContent = 'Выключить звук';
                    btn.querySelector('i').className = 'fas fa-bell-slash';
                }
            }
            showError(d.is_muted ? 'Уведомления отключены' : 'Уведомления включены', 'success');
        }
    } catch (e) { showError('Ошибка'); }
}

// Показать/скрыть форму сообщений или заглушку канала
function updateMessageInputVisibility(isChannel, isAdmin) {
    const messageForm = document.getElementById('message-form');
    const channelPlaceholder = document.getElementById('channel-mute-placeholder');
    
    console.log('updateMessageInputVisibility:', { isChannel, isAdmin });
    
    if (isChannel && !isAdmin) {
        // Канал, пользователь не админ - показываем заглушку
        console.log('Showing channel placeholder (not admin)');
        messageForm.style.display = 'none';
        channelPlaceholder.style.display = 'flex';
    } else {
        // Обычный чат или группа, или пользователь админ канала - показываем форму
        console.log('Showing message form');
        messageForm.style.display = 'flex';
        channelPlaceholder.style.display = 'none';
        // Сбрасываем placeholder если не Избранное
        if (!_favoritesOpen) {
            const msgInput = document.getElementById('message-input');
            if (msgInput && msgInput.placeholder === 'Написать заметку...') {
                msgInput.placeholder = 'Введите сообщение...';
            }
        }
    }
}

// Переменная для хранения текущей цели жалобы
let currentReportTarget = null;
let currentReportTargetId = null;
let currentReportTargetType = null; // 'user' или 'group'

// Показать модальное окно жалобы
function showReportModal() {
    const chatName = document.getElementById('chat-username').textContent;

    // Запрет жаловаться на свои группы/каналы
    if (currentGroupId) {
        const groupData = _currentGroupData;
        if (groupData && (groupData.is_creator || groupData.is_admin)) {
            showError('Нельзя жаловаться на свою группу или канал');
            return;
        }
        currentReportTargetId = currentGroupId;
        currentReportTargetType = 'group';
    } else {
        currentReportTargetId = currentChatUserId;
        currentReportTargetType = 'user';
    }

    currentReportTarget = chatName;
    document.getElementById('report-target-name').textContent = chatName;
    document.getElementById('report-modal').classList.add('active');
}

// Закрыть модальное окно
function closeReportModal() {
    document.getElementById('report-modal').classList.remove('active');
    document.getElementById('report-details').value = '';
}

// Отправить жалобу
function submitReport() {
    const category = document.getElementById('report-category').value;
    const details = document.getElementById('report-details').value;

    if (!category) {
        alert('Пожалуйста, выберите категорию жалобы');
        return;
    }

    fetch('/admin/report', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            username: currentReportTarget,
            category: category,
            details: details,
            reporter: document.body.getAttribute('data-username'),
            target_id: currentReportTargetId,
            target_type: currentReportTargetType
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('Жалоба успешно отправлена. Спасибо!');
            closeReportModal();
        } else {
            alert('Произошла ошибка при отправке жалобы. Пожалуйста, попробуйте позже.');
        }
    })
    .catch(error => {
        console.error('Error:', error);
        alert('Произошла ошибка при отправке жалобы.');
    });
}

// Закрыть модальное окно при клике вне его
window.onclick = function(event) {
    const modal = document.getElementById('report-modal');
    if (event.target == modal) {
        closeReportModal();
    }
}

// Экспортируем функцию
window.toggleChannelMute = toggleChannelMute;

// ─── Избранное ────────────────────────────────────────────────────────────────

let _favoritesOpen = false;

async function openFavorites() {
    _cancelReply();
    currentChatUserId = null;
    currentGroupId = null;
    _currentGroupData = null;
    _favoritesOpen = true;

    document.querySelectorAll('.chat-item, .group-item').forEach(i => i.classList.remove('active'));
    const favItem = document.getElementById('favorites-chat-item');
    if (favItem) favItem.classList.add('active');

    document.getElementById('chat-welcome').style.display = 'none';
    document.getElementById('chat-active').style.display = 'flex';

    if (window.innerWidth <= 768) {
        const sidebar = document.querySelector('.sidebar');
        const chatArea = document.getElementById('chat-area');
        if (sidebar) sidebar.classList.add('mobile-hidden');
        if (chatArea) chatArea.classList.add('mobile-active');
        setupMobileNavigation();
    }

    // Заголовок
    const chatAvatar = document.getElementById('chat-header-avatar');
    if (chatAvatar) {
        chatAvatar.style.backgroundImage = 'none';
        chatAvatar.style.background = 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)';
        chatAvatar.innerHTML = '<i class="fas fa-bookmark" style="font-size:18px;"></i>';
        chatAvatar.style.color = '#fff';
        chatAvatar.style.display = 'flex';
        chatAvatar.style.alignItems = 'center';
        chatAvatar.style.justifyContent = 'center';
    }
    const chatUsername = document.getElementById('chat-username');
    if (chatUsername) chatUsername.innerHTML = 'Избранное';
    const chatStatus = document.getElementById('chat-status');
    if (chatStatus) { chatStatus.textContent = 'Сохранённые сообщения'; chatStatus.style.color = '#a0aec0'; }

    // Скрываем ненужные кнопки
    const addToChatBtn = document.getElementById('add-to-chat-btn');
    if (addToChatBtn) addToChatBtn.style.display = 'none';
    const addMemberBtn = document.getElementById('add-member-btn');
    if (addMemberBtn) addMemberBtn.style.display = 'none';
    const groupSettingsBtn = document.getElementById('group-settings-btn');
    if (groupSettingsBtn) groupSettingsBtn.style.display = 'none';
    const reportBtn = document.querySelector('.chat-header .icon-btn[onclick*="showReportModal"]');
    if (reportBtn) reportBtn.style.display = 'none';
    const infoBtn = document.querySelector('.chat-header .icon-btn[onclick*="viewChatInfo"]');
    if (infoBtn) infoBtn.style.display = 'none';

    // Показываем форму ввода (для заметок)
    updateMessageInputVisibility(false, true);
    // Меняем placeholder
    const msgInput = document.getElementById('message-input');
    if (msgInput) msgInput.placeholder = 'Написать заметку...';

    await _loadFavoritesMessages();
    scrollToBottom();
}

async function _loadFavoritesMessages() {
    try {
        const r = await fetch('/favorites');
        const d = await r.json();
        const favs = d.favorites || [];
        const container = document.getElementById('messages-container');

        if (favs.length === 0) {
            container.innerHTML = `
                <div class="no-messages">
                    <i class="fas fa-bookmark" style="font-size:48px;opacity:0.3;margin-bottom:16px;display:block;"></i>
                    <p>Здесь будут ваши сохранённые сообщения и заметки</p>
                    <p class="hint">Нажмите ··· на любом сообщении → «В избранное»</p>
                </div>`;
            return;
        }

        container.innerHTML = favs.map(f => {
            const content = escapeHtml(f.content || '[медиа]');
            return `
            <div class="message sent" data-fav-id="${f.id}" data-is-mine="1" data-is-deleted="0">
                <div class="message-content">${content}</div>
                <div class="message-time">${f.saved_at || ''}</div>
            </div>`;
        }).join('');
        _reattachCtxMenu();
    } catch (e) { showError('Ошибка загрузки избранного'); }
}

async function addToFavorites(type, id) {
    const body = type === 'message' ? { message_id: id } : { group_message_id: id };
    try {
        const r = await fetch('/favorites/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const d = await r.json();
        if (d.success) {
            showError(d.already ? 'Уже в избранном' : 'Добавлено в избранное', 'success');
            document.querySelectorAll('.message-actions').forEach(m => m.style.display = 'none');
            if (_favoritesOpen) await _loadFavoritesMessages();
        }
    } catch (e) { showError('Ошибка'); }
}

async function deleteFavorite(favId) {
    try {
        const r = await fetch(`/favorites/${favId}/delete`, { method: 'POST' });
        const d = await r.json();
        if (d.success) {
            const msgEl = document.querySelector(`[data-fav-id="${favId}"]`);
            if (msgEl) msgEl.remove();
            const container = document.getElementById('messages-container');
            if (container && !container.querySelector('[data-fav-id]')) {
                container.innerHTML = `
                    <div class="no-messages">
                        <i class="fas fa-bookmark" style="font-size:48px;opacity:0.3;margin-bottom:16px;display:block;"></i>
                        <p>Здесь будут ваши сохранённые сообщения и заметки</p>
                        <p class="hint">Нажмите ··· на любом сообщении → «В избранное»</p>
                    </div>`;
            }
        }
    } catch (e) { showError('Ошибка'); }
}

window.addToFavorites = addToFavorites;
window.openFavorites = openFavorites;
window.deleteFavorite = deleteFavorite;

// Спам-блок
let _spamblockUntil = '';

function showSpamblockModal(until) {
    _spamblockUntil = until || '';
    document.getElementById('spamblock-modal').classList.add('active');
}

function _showNotEnoughSparksModal(required) {
    let m = document.getElementById('_sparks-required-modal');
    if (!m) {
        m = document.createElement('div');
        m.id = '_sparks-required-modal';
        m.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:99999;align-items:center;justify-content:center;';
        m.innerHTML = `<div style="background:var(--bg-primary);color:var(--text-primary);border-radius:16px;padding:28px 24px;max-width:340px;width:90%;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,0.3);">
            <div style="font-size:40px;margin-bottom:12px;">✨</div>
            <h3 style="font-size:18px;font-weight:700;margin-bottom:8px;">Недостаточно искр</h3>
            <p id="_sparks-req-text" style="color:#718096;font-size:14px;margin-bottom:20px;"></p>
            <div style="display:flex;gap:10px;justify-content:center;">
                <button onclick="document.getElementById('_sparks-required-modal').style.display='none';" style="background:var(--bg-secondary);color:var(--text-primary);border:1px solid var(--border-color);border-radius:10px;padding:10px 20px;font-size:14px;cursor:pointer;">Закрыть</button>
                <a href="/profile" style="background:linear-gradient(135deg,#d69e2e,#f6ad55);color:#fff;border:none;border-radius:10px;padding:10px 20px;font-size:14px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;">Купить искры</a>
            </div>
        </div>`;
        m.addEventListener('click', e => { if (e.target === m) m.style.display = 'none'; });
        document.body.appendChild(m);
    }
    document.getElementById('_sparks-req-text').textContent = `Для первого сообщения этому пользователю нужно ${required} ✨ искр. Пополните баланс.`;
    m.style.display = 'flex';
}

function showSpamblockDetails() {
    document.getElementById('spamblock-modal').classList.remove('active');
    document.getElementById('spamblock-until-text').textContent = _spamblockUntil || 'неизвестно';
    document.getElementById('spamblock-details-modal').classList.add('active');
}

// ============================================
// РЕАКЦИИ НА СООБЩЕНИЯ
// ============================================

const REACTION_EMOJIS = ['👍','❤️','😂','😮','😢','🔥','👏','🎉'];

async function showReactionPicker(msgId, isGroup) {
    document.querySelectorAll('.reaction-picker').forEach(p => p.remove());
    const triggerEl = document.querySelector(`[data-message-id="${msgId}"] .reaction-trigger`)
                   || document.querySelector(`[data-message-id="${msgId}"]`);
    if (!triggerEl) return;

    const picker = document.createElement('div');
    picker.className = 'reaction-picker';
    picker.style.cssText = 'position:fixed;z-index:9999;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:24px;padding:6px 10px;display:flex;flex-wrap:wrap;gap:4px;box-shadow:0 4px 20px rgba(0,0,0,0.2);max-width:320px;';

    let html = REACTION_EMOJIS.map(e =>
        `<button class="reaction-emoji-btn" onclick="sendReaction('${msgId}','${e}',${isGroup})" style="background:none;border:none;cursor:pointer;font-size:22px;padding:2px 3px;border-radius:8px;transition:transform .1s;" onmouseover="this.style.transform='scale(1.3)'" onmouseout="this.style.transform='scale(1)'">${e}</button>`
    ).join('');

    if (_isPremium) {
        html += `<button id="custom-reaction-toggle" onclick="_toggleCustomReactions('${msgId}',${isGroup},this)" style="background:linear-gradient(135deg,#667eea,#764ba2);border:none;cursor:pointer;font-size:13px;padding:3px 8px;border-radius:8px;color:#fff;font-weight:600;" title="Кастомные реакции">✨</button>`;
    }

    picker.innerHTML = html;

    const rect = triggerEl.getBoundingClientRect();
    document.body.appendChild(picker);
    requestAnimationFrame(() => {
        const pw = picker.offsetWidth || 320;
        const ph = picker.offsetHeight || 48;
        let left = rect.left;
        let top = rect.top - ph - 8;
        if (left + pw > window.innerWidth - 8) left = window.innerWidth - pw - 8;
        if (left < 8) left = 8;
        if (top < 8) top = rect.bottom + 8;
        picker.style.left = left + 'px';
        picker.style.top = top + 'px';
    });

    setTimeout(() => document.addEventListener('click', function h(ev) {
        if (!picker.contains(ev.target)) { picker.remove(); document.removeEventListener('click', h); }
    }), 50);
}

// Показать/скрыть кастомные реакции в пикере (только Premium)
async function _toggleCustomReactions(msgId, isGroup, btn) {
    const picker = btn.closest('.reaction-picker');
    const existing = picker.querySelector('.custom-reactions-row');
    if (existing) { existing.remove(); return; }

    const row = document.createElement('div');
    row.className = 'custom-reactions-row';
    row.style.cssText = 'width:100%;display:flex;flex-wrap:wrap;gap:4px;padding-top:6px;border-top:1px solid var(--border-color);margin-top:4px;';
    row.innerHTML = '<span style="font-size:11px;color:var(--text-secondary);width:100%;padding:0 2px;">Мои реакции:</span>';

    try {
        const r = await fetch('/reactions/my');
        const d = await r.json();
        if (d.error === 'premium_required') { showPremiumModal('Кастомные реакции доступны только для Premium'); return; }
        const allPacks = [...(d.owned || []), ...(d.added || [])];
        if (!allPacks.length) {
            row.innerHTML += '<span style="font-size:12px;color:var(--text-secondary);padding:4px;">Нет кастомных реакций. Создайте пак!</span>';
        } else {
            allPacks.forEach(pack => {
                pack.reactions.forEach(rx => {
                    const img = document.createElement('img');
                    img.src = rx.image_url;
                    img.dataset.reactionId = rx.id;
                    img.dataset.packId = pack.id;
                    img.style.cssText = 'width:32px;height:32px;object-fit:contain;cursor:pointer;border-radius:6px;padding:1px;transition:transform .1s;';
                    img.title = rx.name + ' (ПКМ — посмотреть пак)';
                    img.onmouseover = () => img.style.transform = 'scale(1.3)';
                    img.onmouseout = () => img.style.transform = 'scale(1)';
                    img.onclick = () => sendCustomReaction(msgId, rx.id, rx.image_url, isGroup);
                    img.oncontextmenu = (e) => { e.preventDefault(); viewReactionPack(pack.id); };
                    row.appendChild(img);
                });
            });
        }
    } catch(e) {
        row.innerHTML += '<span style="font-size:12px;color:#e53e3e;">Ошибка загрузки</span>';
    }
    picker.appendChild(row);
}
window._toggleCustomReactions = _toggleCustomReactions;

// Отправить кастомную реакцию (изображение)
async function sendCustomReaction(msgId, reactionId, imageUrl, isGroup) {
    document.querySelectorAll('.reaction-picker').forEach(p => p.remove());
    const url = isGroup ? `/group-message/${msgId}/react` : `/message/${msgId}/react`;
    // Используем URL изображения как emoji-ключ для кастомных реакций
    try {
        const r = await fetch(url, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({emoji: imageUrl, is_custom: true, reaction_id: reactionId}) });
        const d = await r.json();
        if (d.reactions) renderReactions(msgId, d.reactions);
    } catch(e) { console.error(e); }
}
window.sendCustomReaction = sendCustomReaction;

// Просмотр пака реакций (по ПКМ на реакцию)
async function viewReactionPack(packId) {
    try {
        const r = await fetch(`/reactions/pack/${packId}`);
        const d = await r.json();
        if (d.error) { showError(d.error); return; }
        const modal = document.createElement('div');
        modal.className = 'modal active';
        modal.innerHTML = `
            <div class="modal-content" style="max-width:380px;padding:0;overflow:hidden;">
                <div class="modal-header" style="padding:14px 18px;">
                    <h2 style="font-size:16px;">✨ ${escapeHtml(d.name)}</h2>
                    <button class="close-btn" onclick="this.closest('.modal').remove()"><i class="fas fa-times"></i></button>
                </div>
                <div style="padding:12px;display:flex;flex-wrap:wrap;gap:6px;max-height:300px;overflow-y:auto;">
                    ${d.reactions.map(rx => `<img src="${rx.image_url}" title="${escapeHtml(rx.name)}" style="width:56px;height:56px;object-fit:contain;border-radius:8px;" loading="lazy">`).join('')}
                </div>
                <div style="padding:12px;border-top:1px solid var(--border-color);">
                    ${!d.is_added && !d.is_owner ? `<button onclick="addReactionPackFromModal(${d.id},this)" style="width:100%;padding:9px;background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:14px;">✨ Добавить пак реакций</button>`
                    : `<div style="text-align:center;color:var(--text-secondary);font-size:13px;">✓ Пак уже в коллекции</div>`}
                </div>
            </div>`;
        document.body.appendChild(modal);
        modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    } catch(e) { showError('Ошибка загрузки пака реакций'); }
}
window.viewReactionPack = viewReactionPack;

async function addReactionPackFromModal(packId, btn) {
    btn.disabled = true; btn.textContent = 'Добавление...';
    try {
        const r = await fetch(`/reactions/pack/${packId}/add`, { method: 'POST' });
        const d = await r.json();
        if (d.success) {
            btn.textContent = '✓ Добавлено';
            btn.style.background = '#38a169';
        } else if (d.error === 'premium_required') {
            showPremiumModal('Кастомные реакции доступны только для Premium');
            btn.disabled = false; btn.textContent = '✨ Добавить пак реакций';
        } else { showError(d.error || 'Ошибка'); btn.disabled = false; }
    } catch(e) { showError('Ошибка'); btn.disabled = false; }
}
window.addReactionPackFromModal = addReactionPackFromModal;

async function sendReaction(msgId, emoji, isGroup) {
    document.querySelectorAll('.reaction-picker').forEach(p => p.remove());
    const url = isGroup ? `/group-message/${msgId}/react` : `/message/${msgId}/react`;
    try {
        const r = await fetch(url, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({emoji}) });
        const d = await r.json();
        if (d.reactions) renderReactions(msgId, d.reactions);
    } catch(e) { console.error(e); }
}

function renderReactions(msgId, reactions) {
    const msgEl = document.querySelector(`[data-message-id="${msgId}"]`);
    if (!msgEl) return;
    let bar = msgEl.querySelector('.reactions-bar');
    if (!bar) {
        bar = document.createElement('div');
        bar.className = 'reactions-bar';
        const timeEl = msgEl.querySelector('.message-time');
        if (timeEl) msgEl.insertBefore(bar, timeEl); else msgEl.appendChild(bar);
    }
    const myId = parseInt(document.body.getAttribute('data-user-id'));
    bar.innerHTML = Object.entries(reactions).map(([emoji, data]) => {
        const mine = data.users && data.users.includes(myId);
        const isCustom = emoji.startsWith('/static/') || emoji.startsWith('http');
        const emojiHtml = isCustom
            ? `<img src="${emoji}" style="width:18px;height:18px;object-fit:contain;vertical-align:middle;border-radius:3px;">`
            : emoji;
        const packId = data.pack_id || '';
        const ctxAttr = isCustom && packId ? `oncontextmenu="event.preventDefault();viewReactionPack(${packId})"` : '';
        return `<span class="reaction-chip${mine?' mine':''}" onclick="sendReaction('${msgId}','${emoji.replace(/'/g,"\\'")}',${msgEl.dataset.isGroup==='1'})" ${ctxAttr} title="${isCustom ? 'ПКМ — посмотреть пак' : ''}">${emojiHtml} ${data.count}</span>`;
    }).join('');
    if (!Object.keys(reactions).length) bar.remove();
}

// Socket.IO обработчик реакций
if (typeof socket !== 'undefined' && socket) {
    socket.on('reaction_updated', function(data) {
        const msgId = data.message_id || data.group_message_id;
        if (msgId) renderReactions(msgId, data.reactions);
    });
}

window.showReactionPicker = showReactionPicker;
window.sendReaction = sendReaction;
window.renderReactions = renderReactions;

// ============================================
// СТАТУС "ПЕЧАТАЕТ..."
// ============================================

let _typingTimer = null;
let _isTyping = false;

function setupTypingIndicator() {
    const input = document.getElementById('message-input');
    if (!input) return;
    input.addEventListener('input', function() {
        if (!socket || !socket.connected) return;
        if (!_isTyping) {
            _isTyping = true;
            if (currentChatUserId) socket.emit('typing_start', {to_user_id: currentChatUserId});
            else if (currentGroupId) socket.emit('typing_start', {group_id: currentGroupId});
        }
        clearTimeout(_typingTimer);
        _typingTimer = setTimeout(() => {
            _isTyping = false;
            if (currentChatUserId) socket.emit('typing_stop', {to_user_id: currentChatUserId});
            else if (currentGroupId) socket.emit('typing_stop', {group_id: currentGroupId});
        }, 2000);
    });
}

function showTypingIndicator(name) {
    const container = document.getElementById('messages-container');
    if (!container) return;
    let el = document.getElementById('typing-indicator');
    if (!el) {
        el = document.createElement('div');
        el.id = 'typing-indicator';
        el.className = 'typing-indicator';
    }
    el.innerHTML = `<div class="typing-bubble"><span class="typing-dots"><span></span><span></span><span></span></span></div>`;
    el.style.display = 'flex';
    // Всегда в конце контейнера
    container.appendChild(el);
    scrollToBottom();
}

function hideTypingIndicator(userId) {
    const el = document.getElementById('typing-indicator');
    if (el) el.style.display = 'none';
}

document.addEventListener('DOMContentLoaded', function() {
    setupTypingIndicator();
    // Socket.IO typing handlers вешаем в connectSocketIO через initTypingSocketHandlers()

    // Вставка изображения из буфера (Ctrl+V)
    document.addEventListener('paste', function(e) {
        if (!currentChatUserId && !currentGroupId) return;
        const items = e.clipboardData && e.clipboardData.items;
        if (!items) return;
        for (const item of items) {
            if (item.type.startsWith('image/')) {
                e.preventDefault();
                const file = item.getAsFile();
                if (!file) return;
                // Показываем превью перед отправкой
                const url = URL.createObjectURL(file);
                const modal = document.createElement('div');
                modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:9999;display:flex;align-items:center;justify-content:center;';
                modal.innerHTML = `
                    <div style="background:var(--bg-primary,#fff);border-radius:16px;padding:20px;max-width:480px;width:90%;text-align:center;">
                        <p style="margin:0 0 12px;font-weight:600;color:var(--text-primary,#000);">Отправить изображение?</p>
                        <img src="${url}" style="max-width:100%;max-height:300px;border-radius:10px;object-fit:contain;margin-bottom:12px;">
                        <textarea id="paste-caption" placeholder="Подпись (необязательно)..." style="width:100%;padding:8px;border:1px solid var(--border-color,#e2e8f0);border-radius:8px;background:var(--bg-secondary,#f7fafc);color:var(--text-primary,#000);resize:none;height:60px;font-family:inherit;box-sizing:border-box;margin-bottom:12px;"></textarea>
                        <div style="display:flex;gap:10px;">
                            <button id="paste-cancel" style="flex:1;padding:10px;border-radius:10px;border:none;background:var(--bg-secondary,#f0f0f0);color:var(--text-muted,#666);cursor:pointer;">Отмена</button>
                            <button id="paste-send" style="flex:2;padding:10px;border-radius:10px;border:none;background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;font-weight:600;cursor:pointer;">Отправить</button>
                        </div>
                    </div>`;
                document.body.appendChild(modal);
                modal.querySelector('#paste-cancel').onclick = () => { modal.remove(); URL.revokeObjectURL(url); };
                modal.addEventListener('click', e => { if (e.target === modal) { modal.remove(); URL.revokeObjectURL(url); } });
                modal.querySelector('#paste-send').onclick = async () => {
                    const caption = modal.querySelector('#paste-caption').value.trim();
                    modal.remove();
                    URL.revokeObjectURL(url);
                    // Отправляем через существующий механизм загрузки файлов
                    if (typeof selectedFiles !== 'undefined') {
                        selectedFiles = [file];
                        if (caption) {
                            // Отправляем напрямую
                        }
                        const formData = new FormData();
                        formData.append('files', file);
                        if (caption) formData.append('caption', caption);
                        if (currentChatUserId) formData.append('receiver_id', currentChatUserId);
                        if (currentGroupId) formData.append('group_id', currentGroupId);
                        try {
                            const r = await fetch('/send_multiple_files', { method: 'POST', body: formData });
                            const d = await r.json();
                            if (!d.success) showError(d.error || 'Ошибка отправки');
                        } catch(e) { showError('Ошибка отправки'); }
                    }
                };
                break;
            }
        }
    });
});

// ============================================
// ОПРОСЫ
// ============================================

function showAttachMenu() {
    // Удаляем старое меню если есть
    const old = document.getElementById('attach-menu-popup');
    if (old) { old.remove(); return; }

    const menu = document.createElement('div');
    menu.id = 'attach-menu-popup';
    menu.style.cssText = 'position:fixed;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:14px;box-shadow:0 4px 20px rgba(0,0,0,0.18);z-index:99999;padding:8px;display:flex;flex-direction:column;gap:4px;min-width:180px;';
    menu.innerHTML = `
        <button onclick="document.getElementById('image-input').click();document.getElementById('attach-menu-popup')?.remove();"
            style="display:flex;align-items:center;gap:10px;padding:10px 14px;border:none;background:none;cursor:pointer;border-radius:10px;font-size:14px;color:var(--text-primary);width:100%;text-align:left;"
            onmouseover="this.style.background='var(--bg-tertiary)'" onmouseout="this.style.background='none'">
            <i class="fas fa-image" style="color:#667eea;width:18px;"></i> Фото / Видео / Файл
        </button>
        <button onclick="showPollModal();document.getElementById('attach-menu-popup')?.remove();"
            style="display:flex;align-items:center;gap:10px;padding:10px 14px;border:none;background:none;cursor:pointer;border-radius:10px;font-size:14px;color:var(--text-primary);width:100%;text-align:left;"
            onmouseover="this.style.background='var(--bg-tertiary)'" onmouseout="this.style.background='none'">
            <i class="fas fa-poll" style="color:#38a169;width:18px;"></i> Опрос
        </button>
    `;

    // Закрыть по клику вне
    setTimeout(() => {
        document.addEventListener('click', function _close(e) {
            if (!menu.contains(e.target) && e.target.id !== 'image-btn') {
                menu.remove();
                document.removeEventListener('click', _close);
            }
        });
    }, 10);

    document.body.appendChild(menu);
    // Позиционируем над кнопкой скрепки
    const btn = document.getElementById('image-btn');
    if (btn) {
        const r = btn.getBoundingClientRect();
        menu.style.left = Math.max(8, r.left) + 'px';
        menu.style.bottom = (window.innerHeight - r.top + 8) + 'px';
    } else {
        menu.style.left = '12px';
        menu.style.bottom = '80px';
    }
}
window.showAttachMenu = showAttachMenu;

function showPollModal() {
    if (!currentChatUserId && !currentGroupId) { showError('Сначала откройте чат'); return; }
    const modal = document.createElement('div');
    modal.id = 'poll-modal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px;';
    modal.innerHTML = `
        <div style="background:var(--bg-primary,#fff);color:var(--text-primary,#000);border-radius:16px;padding:24px;max-width:420px;width:100%;max-height:80vh;overflow-y:auto;">
            <h3 style="margin-bottom:16px;"><i class="fas fa-poll"></i> Создать опрос</h3>
            <div class="form-group">
                <label>Вопрос</label>
                <input type="text" id="poll-question" placeholder="Введите вопрос..." maxlength="500" style="width:100%;padding:10px;border:1px solid #e2e8f0;border-radius:8px;font-size:14px;box-sizing:border-box;">
            </div>
            <div id="poll-options-list">
                <div class="form-group"><input type="text" class="poll-option-input" placeholder="Вариант 1" maxlength="200" style="width:100%;padding:10px;border:1px solid #e2e8f0;border-radius:8px;font-size:14px;box-sizing:border-box;"></div>
                <div class="form-group"><input type="text" class="poll-option-input" placeholder="Вариант 2" maxlength="200" style="width:100%;padding:10px;border:1px solid #e2e8f0;border-radius:8px;font-size:14px;box-sizing:border-box;"></div>
            </div>
            <button onclick="addPollOption()" style="background:none;border:1px dashed #667eea;color:#667eea;border-radius:8px;padding:8px 16px;cursor:pointer;font-size:13px;margin-bottom:12px;width:100%;">+ Добавить вариант</button>
            <div style="display:flex;gap:8px;margin-bottom:16px;">
                <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
                    <input type="checkbox" id="poll-anonymous" checked> Анонимный
                </label>
                <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
                    <input type="checkbox" id="poll-multiple"> Несколько ответов
                </label>
            </div>
            <div style="display:flex;gap:10px;">
                <button onclick="submitPoll()" class="btn btn-primary" style="flex:1;">Создать</button>
                <button onclick="document.getElementById('poll-modal').remove()" class="btn" style="flex:1;background:#e2e8f0;color:#4a5568;">Отмена</button>
            </div>
        </div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);
}

function addPollOption() {
    const list = document.getElementById('poll-options-list');
    const count = list.querySelectorAll('.poll-option-input').length;
    if (count >= 10) { showError('Максимум 10 вариантов'); return; }
    const div = document.createElement('div');
    div.className = 'form-group';
    div.innerHTML = `<input type="text" class="poll-option-input" placeholder="Вариант ${count+1}" maxlength="200" style="width:100%;padding:10px;border:1px solid #e2e8f0;border-radius:8px;font-size:14px;box-sizing:border-box;">`;
    list.appendChild(div);
}

async function submitPoll() {
    const question = document.getElementById('poll-question').value.trim();
    const options = [...document.querySelectorAll('.poll-option-input')].map(i => i.value.trim()).filter(Boolean);
    const isAnonymous = document.getElementById('poll-anonymous').checked;
    const isMultiple = document.getElementById('poll-multiple').checked;
    if (!question) { showError('Введите вопрос'); return; }
    if (options.length < 2) { showError('Нужно минимум 2 варианта'); return; }
    const body = { question, options, is_anonymous: isAnonymous, is_multiple: isMultiple };
    if (currentChatUserId) body.receiver_id = currentChatUserId;
    else if (currentGroupId) body.group_id = currentGroupId;
    try {
        const r = await fetch('/poll/create', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
        const d = await r.json();
        if (d.success) { document.getElementById('poll-modal').remove(); showError('Опрос создан!', 'success'); }
        else showError(d.error || 'Ошибка');
    } catch(e) { showError('Ошибка создания опроса'); }
}

async function loadAndShowPoll(pollId, msgEl) {
    try {
        // msgEl может быть строкой (id элемента) или DOM-элементом
        if (typeof msgEl === 'string') {
            msgEl = document.getElementById(msgEl);
        }
        if (!msgEl) return;
        const r = await fetch(`/poll/${pollId}`);
        const d = await r.json();
        if (!d.poll) return;
        const poll = d.poll;
        let html = `<div class="poll-widget" data-poll-id="${poll.id}">
            <div class="poll-question">${escapeHtml(poll.question)}</div>
            <div class="poll-options">`;
        poll.options.forEach(opt => {
            const voted = poll.my_votes.includes(opt.id);
            html += `<div class="poll-option${voted?' voted':''}" onclick="votePoll(${poll.id},[${opt.id}])">
                <div class="poll-option-bar" style="width:${opt.percent}%"></div>
                <span class="poll-option-text">${escapeHtml(opt.text)}</span>
                <span class="poll-option-percent">${opt.percent}%</span>
            </div>`;
        });
        html += `</div><div class="poll-footer">${poll.total_votes} голосов${poll.is_anonymous?' • Анонимный':''}</div></div>`;
        const contentEl = msgEl.querySelector('.message-content') || msgEl;
        contentEl.innerHTML = html;
    } catch(e) { console.error(e); }
}

async function votePoll(pollId, optionIds) {
    try {
        const r = await fetch(`/poll/${pollId}/vote`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({option_ids: optionIds}) });
        const d = await r.json();
        if (d.success) {
            // Перезагружаем опрос
            const msgEl = document.querySelector(`[data-poll-id="${pollId}"]`)?.closest('.message');
            if (msgEl) loadAndShowPoll(pollId, msgEl);
        }
    } catch(e) { console.error(e); }
}

// Обработка poll_updated через Socket.IO
document.addEventListener('DOMContentLoaded', function() {
    const waitForSocket2 = setInterval(() => {
        if (window.socket) {
            clearInterval(waitForSocket2);
            window.socket.on('poll_updated', function(data) {
                const msgEl = document.querySelector(`[data-poll-id="${data.poll_id}"]`)?.closest('.message');
                if (msgEl) loadAndShowPoll(data.poll_id, msgEl);
            });
        }
    }, 500);
});

window.showPollModal = showPollModal;
window.addPollOption = addPollOption;
window.submitPoll = submitPoll;
window.votePoll = votePoll;

// ============================================
// ПЕРЕСЫЛКА СООБЩЕНИЙ
// ============================================

function showForwardModal(msgId, isGroup) {
    const modal = document.createElement('div');
    modal.id = 'forward-modal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px;';
    modal.innerHTML = `
        <div style="background:var(--bg-primary,#fff);color:var(--text-primary,#000);border-radius:16px;padding:24px;max-width:400px;width:100%;max-height:70vh;overflow-y:auto;">
            <h3 style="margin-bottom:16px;"><i class="fas fa-share"></i> Переслать сообщение</h3>
            <input type="text" id="forward-search" placeholder="Поиск чата..." style="width:100%;padding:10px;border:1px solid #e2e8f0;border-radius:8px;font-size:14px;box-sizing:border-box;margin-bottom:12px;" oninput="filterForwardList(this.value)">
            <div id="forward-list" style="max-height:300px;overflow-y:auto;"></div>
            <button onclick="document.getElementById('forward-modal').remove()" style="margin-top:12px;width:100%;padding:10px;background:#e2e8f0;border:none;border-radius:8px;cursor:pointer;font-size:14px;">Отмена</button>
        </div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);
    _loadForwardList(msgId, isGroup);
}

let _forwardChats = [];
async function _loadForwardList(msgId, isGroup) {
    const listEl = document.getElementById('forward-list');
    try {
        const [usersR, groupsR] = await Promise.all([fetch('/users'), fetch('/groups')]);
        const usersD = await usersR.json();
        const groupsD = await groupsR.json();
        _forwardChats = [
            ...(usersD.users || []).map(u => ({type:'user', id:u.id, name:u.display_name||u.username, msgId, isGroup})),
            ...(groupsD.groups || []).map(g => ({type:'group', id:g.id, name:g.name, msgId, isGroup}))
        ];
        _renderForwardList(_forwardChats);
    } catch(e) { listEl.innerHTML = '<p style="color:#e53e3e;">Ошибка загрузки</p>'; }
}

function _renderForwardList(chats) {
    const listEl = document.getElementById('forward-list');
    if (!listEl) return;
    listEl.innerHTML = chats.map(c => `
        <div onclick="doForward('${c.type}',${c.id},${c.msgId},${c.isGroup})" style="padding:10px;border-radius:8px;cursor:pointer;display:flex;align-items:center;gap:10px;transition:background 0.2s;" onmouseover="this.style.background='#f0f4f8'" onmouseout="this.style.background=''">
            <div style="width:36px;height:36px;border-radius:50%;background:#667eea;display:flex;align-items:center;justify-content:center;color:#fff;font-size:14px;font-weight:600;flex-shrink:0;">${escapeHtml(c.name[0].toUpperCase())}</div>
            <span style="font-size:14px;">${escapeHtml(c.name)}</span>
        </div>`).join('');
}

function filterForwardList(q) {
    const filtered = q ? _forwardChats.filter(c => c.name.toLowerCase().includes(q.toLowerCase())) : _forwardChats;
    _renderForwardList(filtered);
}

async function doForward(type, targetId, msgId, isGroup) {
    document.getElementById('forward-modal')?.remove();
    const body = {};
    if (isGroup) body.group_message_id = msgId; else body.message_id = msgId;
    if (type === 'user') body.to_user_id = targetId; else body.to_group_id = targetId;
    try {
        const r = await fetch('/message/forward', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
        const d = await r.json();
        if (d.success) showError('Сообщение переслано', 'success');
        else showError(d.error || 'Ошибка пересылки');
    } catch(e) { showError('Ошибка пересылки'); }
}

window.showForwardModal = showForwardModal;
window.filterForwardList = filterForwardList;
window.doForward = doForward;

// ============================================
// ЗАКРЕПЛЁННЫЕ СООБЩЕНИЯ
// ============================================

async function loadPinnedMessage() {
    const bar = document.getElementById('pinned-bar');
    if (!bar) return;
    try {
        let url = '';
        if (currentGroupId) url = `/groups/${currentGroupId}/pinned`;
        else if (currentChatUserId) url = `/chat/${currentChatUserId}/pinned`;
        else { bar.style.display = 'none'; return; }
        const r = await fetch(url);
        const d = await r.json();
        if (d.pinned) {
            bar.style.display = 'flex';
            bar.querySelector('.pinned-preview').textContent = d.pinned.preview || 'Закреплённое сообщение';
            bar.dataset.msgId = d.pinned.message_id;
        } else {
            bar.style.display = 'none';
        }
    } catch(e) { bar.style.display = 'none'; }
}

function scrollToPinned() {
    const bar = document.getElementById('pinned-bar');
    if (!bar || !bar.dataset.msgId) return;
    scrollToMsg(bar.dataset.msgId);
}

async function pinCurrentMessage(msgId, isGroup) {
    const url = isGroup ? `/groups/${currentGroupId}/pin` : `/chat/${currentChatUserId}/pin`;
    const body = isGroup ? {message_id: msgId} : {message_id: msgId};
    try {
        const r = await fetch(url, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
        const d = await r.json();
        if (d.success) { showError('Сообщение закреплено', 'success'); loadPinnedMessage(); }
        else showError(d.error || 'Ошибка');
    } catch(e) { showError('Ошибка'); }
}

// Socket.IO для закреплённых — обработчик добавлен в connectSocketIO

window.loadPinnedMessage = loadPinnedMessage;
window.scrollToPinned = scrollToPinned;
window.pinCurrentMessage = pinCurrentMessage;

// ============================================
// КОНТАКТЫ
// ============================================

async function showContactsModal() {
    const modal = document.createElement('div');
    modal.id = 'contacts-modal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px;';
    modal.innerHTML = `
        <div style="background:var(--bg-primary,#fff);color:var(--text-primary,#000);border-radius:16px;padding:24px;max-width:400px;width:100%;max-height:70vh;overflow-y:auto;">
            <h3 style="margin-bottom:16px;"><i class="fas fa-address-book"></i> Контакты</h3>
            <div id="contacts-list-modal" style="max-height:400px;overflow-y:auto;"></div>
            <button onclick="document.getElementById('contacts-modal').remove()" style="margin-top:12px;width:100%;padding:10px;background:#e2e8f0;border:none;border-radius:8px;cursor:pointer;font-size:14px;">Закрыть</button>
        </div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);
    const r = await fetch('/contacts');
    const d = await r.json();
    const listEl = document.getElementById('contacts-list-modal');
    if (!d.contacts || !d.contacts.length) {
        listEl.innerHTML = '<p style="color:#a0aec0;text-align:center;padding:20px;">Контактов пока нет</p>';
        return;
    }
    listEl.innerHTML = d.contacts.map(c => `
        <div style="display:flex;align-items:center;gap:10px;padding:10px;border-radius:8px;cursor:pointer;" onclick="openChat(${c.id},'${escapeHtml(c.display_name||c.username)}');document.getElementById('contacts-modal').remove();">
            <div style="width:40px;height:40px;border-radius:50%;background:${c.avatar_color};display:flex;align-items:center;justify-content:center;color:#fff;font-weight:600;flex-shrink:0;">${escapeHtml(c.avatar_letter)}</div>
            <div style="flex:1;">
                <div style="font-weight:600;font-size:14px;">${escapeHtml(c.display_name||c.username)}</div>
                <div style="font-size:12px;color:#a0aec0;">@${escapeHtml(c.username)}</div>
            </div>
            <button onclick="event.stopPropagation();removeContact(${c.id},this)" style="background:none;border:none;color:#e53e3e;cursor:pointer;font-size:12px;padding:4px 8px;border-radius:6px;border:1px solid #e53e3e;">Удалить</button>
        </div>`).join('');
}

async function addContactFromChat() {
    if (!currentChatUserId) return;
    const r = await fetch('/contacts/add', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({user_id: currentChatUserId}) });
    const d = await r.json();
    if (d.success) showError(d.already ? 'Уже в контактах' : 'Добавлен в контакты', 'success');
    else showError(d.error || 'Ошибка');
}

async function removeContact(userId, btn) {
    const r = await fetch('/contacts/remove', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({user_id: userId}) });
    const d = await r.json();
    if (d.success) { btn.closest('div[style]').remove(); showError('Контакт удалён', 'success'); }
}

window.showContactsModal = showContactsModal;
window.addContactFromChat = addContactFromChat;
window.removeContact = removeContact;

// Обработка ошибки contacts_required при отправке
const _origDoSendText = window._doSendText;
// Патчим handleSendMessage для обработки contacts_required
const _origHandleSendMessage = window.handleSendMessage;

function showContactsRequiredModal() {
    let modal = document.getElementById('contacts-required-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'contacts-required-modal';
        modal.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:99999;align-items:center;justify-content:center;';
        modal.innerHTML = `
            <div style="background:var(--bg-primary,#fff);color:var(--text-primary,#000);border-radius:16px;padding:28px 24px;max-width:360px;width:90%;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,0.3);">
                <div style="font-size:40px;margin-bottom:12px;">👥</div>
                <h3 style="font-size:18px;font-weight:700;margin-bottom:8px;">Нужны взаимные контакты</h3>
                <p style="color:#718096;font-size:14px;margin-bottom:20px;">Чтобы написать этому пользователю, добавьте его в контакты. Когда он тоже добавит вас — вы сможете общаться.</p>
                <div style="display:flex;gap:10px;justify-content:center;">
                    <button onclick="addContactFromChat();document.getElementById('contacts-required-modal').style.display='none';" style="background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;border:none;border-radius:10px;padding:10px 20px;font-size:14px;font-weight:600;cursor:pointer;">Добавить в контакты</button>
                    <button onclick="document.getElementById('contacts-required-modal').style.display='none';" style="background:#e2e8f0;color:#4a5568;border:none;border-radius:10px;padding:10px 20px;font-size:14px;cursor:pointer;">Отмена</button>
                </div>
            </div>`;
        modal.addEventListener('click', e => { if (e.target === modal) modal.style.display = 'none'; });
        document.body.appendChild(modal);
    }
    modal.style.display = 'flex';
}
window.showContactsRequiredModal = showContactsRequiredModal;

// ═══════════════════════════════════════════════════════════════════════════════
// ИСКРЫ И ПЛАТНЫЕ ПОСТЫ
// ═══════════════════════════════════════════════════════════════════════════════

async function buyPaidPost(paidPostId, btn) {
    const r = await fetch(`/paid-post/${paidPostId}/buy`, { method: 'POST' });
    const d = await r.json();
    if (d.error) { showError(d.error); return; }
    if (d.success || d.already_purchased) {
        // Перезагружаем группу чтобы показать контент
        showError('Контент открыт!', 'success');
        if (currentGroupId) openGroup(currentGroupId, document.getElementById('chat-username')?.textContent || '');
    }
}
window.buyPaidPost = buyPaidPost;

async function showSparkReactModal(msgId) {
    const balR = await fetch('/sparks/balance');
    const bal = await balR.json();
    const modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:99999;display:flex;align-items:center;justify-content:center;';
    modal.innerHTML = `
        <div style="background:var(--bg-primary,#fff);color:var(--text-primary,#000);border-radius:16px;padding:24px;max-width:320px;width:90%;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,0.3);">
            <div style="font-size:36px;margin-bottom:8px;">✨</div>
            <h3 style="font-size:17px;font-weight:700;margin-bottom:4px;">Искорная реакция</h3>
            <p style="color:#718096;font-size:13px;margin-bottom:16px;">Ваш баланс: <b style="color:#f6ad55;">${bal.balance} ✨</b></p>
            <div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-bottom:16px;">
                ${[1,5,10,25,50,100].map(n => `<button onclick="doSparkReact(${msgId},${n},this.closest('div[style*=fixed]'))" style="padding:8px 14px;border:2px solid #f6ad55;border-radius:10px;background:transparent;color:#f6ad55;font-weight:700;cursor:pointer;font-size:14px;">${n} ✨</button>`).join('')}
            </div>
            <button onclick="this.closest('div[style*=fixed]').remove()" style="background:#e2e8f0;color:#4a5568;border:none;border-radius:10px;padding:8px 20px;cursor:pointer;">Отмена</button>
        </div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);
}
window.showSparkReactModal = showSparkReactModal;

async function doSparkReact(msgId, amount, modal) {
    modal && modal.remove();

    // Показываем тост с кнопкой отмены на 5 секунд
    let cancelled = false;
    const toast = document.createElement('div');
    toast.style.cssText = 'position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:#2d3748;color:#fff;border-radius:12px;padding:10px 18px;display:flex;align-items:center;gap:12px;z-index:99999;font-size:14px;box-shadow:0 4px 20px rgba(0,0,0,0.3);';
    toast.innerHTML = `<span>✨ Отправляю ${amount} искр...</span><button id="_spark-cancel-btn" style="background:#f6ad55;color:#1a202c;border:none;border-radius:8px;padding:4px 12px;cursor:pointer;font-weight:700;font-size:13px;">Отмена</button>`;
    document.body.appendChild(toast);

    // Прогресс-бар
    const bar = document.createElement('div');
    bar.style.cssText = 'position:absolute;bottom:0;left:0;height:3px;background:#f6ad55;border-radius:0 0 12px 12px;width:100%;transition:width 5s linear;';
    toast.style.position = 'fixed';
    toast.style.overflow = 'hidden';
    toast.appendChild(bar);
    requestAnimationFrame(() => { bar.style.width = '0%'; });

    toast.querySelector('#_spark-cancel-btn').addEventListener('click', () => {
        cancelled = true;
        toast.remove();
    });

    await new Promise(r => setTimeout(r, 5000));
    toast.remove();
    if (cancelled) return;

    const r = await fetch(`/sparks/react/${msgId}`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({amount}) });
    const d = await r.json();
    if (d.error) { showError(d.error); return; }
    showError(`Отправлено ${amount} ✨`, 'success');
    const sparkBar = document.getElementById(`spark-bar-${msgId}`);
    if (sparkBar) sparkBar.innerHTML = `<span class="spark-total">✨ ${d.total}</span>`;
}
window.doSparkReact = doSparkReact;

// Добавляем кнопку "Платный пост" в форму канала для владельцев
function _addPaidPostBtn() {
    if (!_currentGroupData || !_currentGroupData.is_channel || !_currentGroupData.is_admin) return;
    if (document.getElementById('paid-post-btn')) return;
    const btn = document.createElement('button');
    btn.id = 'paid-post-btn';
    btn.type = 'button';
    btn.className = 'media-btn';
    btn.title = 'Платный пост';
    btn.innerHTML = '<i class="fas fa-lock"></i>';
    btn.onclick = showPaidPostModal;
    const form = document.getElementById('message-form');
    if (form) form.insertBefore(btn, form.querySelector('#message-input'));
}

function showPaidPostModal() {
    const modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:99999;display:flex;align-items:center;justify-content:center;padding:20px;';
    modal.innerHTML = `
        <div style="background:var(--bg-primary,#fff);color:var(--text-primary,#000);border-radius:16px;padding:24px;max-width:420px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,0.3);">
            <h3 style="font-size:17px;font-weight:700;margin-bottom:16px;">🔒 Платный пост</h3>
            <div style="margin-bottom:12px;">
                <label style="font-size:13px;font-weight:600;display:block;margin-bottom:6px;">Текст поста</label>
                <textarea id="pp-content" rows="4" style="width:100%;padding:10px;border:2px solid #e2e8f0;border-radius:10px;font-size:14px;resize:vertical;" placeholder="Описание платного контента..."></textarea>
            </div>
            <div style="margin-bottom:12px;">
                <label style="font-size:13px;font-weight:600;display:block;margin-bottom:6px;">Медиафайлы (фото/видео)</label>
                <input type="file" id="pp-media" accept="image/*,video/*" multiple style="font-size:13px;">
            </div>
            <div style="margin-bottom:16px;">
                <label style="font-size:13px;font-weight:600;display:block;margin-bottom:6px;">Цена в искрах ✨</label>
                <input type="number" id="pp-price" value="10" min="1" max="10000" style="width:100%;padding:10px;border:2px solid #e2e8f0;border-radius:10px;font-size:14px;">
            </div>
            <div style="display:flex;gap:10px;">
                <button onclick="submitPaidPost()" style="flex:1;padding:10px;background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;border:none;border-radius:10px;font-weight:600;cursor:pointer;">Опубликовать</button>
                <button onclick="this.closest('div[style*=fixed]').remove()" style="padding:10px 16px;background:#e2e8f0;color:#4a5568;border:none;border-radius:10px;cursor:pointer;">Отмена</button>
            </div>
        </div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);
}
window.showPaidPostModal = showPaidPostModal;

async function submitPaidPost() {
    const content = document.getElementById('pp-content')?.value.trim();
    const price = parseInt(document.getElementById('pp-price')?.value) || 10;
    const files = document.getElementById('pp-media')?.files;
    if (!content) { showError('Введите текст поста'); return; }
    const fd = new FormData();
    fd.append('content', content);
    fd.append('price_sparks', price);
    if (files) for (const f of files) fd.append('media', f);
    const r = await fetch(`/channel/${currentGroupId}/paid-post`, { method: 'POST', body: fd });
    const d = await r.json();
    if (d.success) {
        document.querySelector('div[style*="fixed"]')?.remove();
        showError('Платный пост опубликован!', 'success');
    } else showError(d.error || 'Ошибка');
}
window.submitPaidPost = submitPaidPost;

// Добавляем кнопку искорной реакции в контекстное меню каналов
// (уже добавлено через _showCtxMenu — здесь добавляем spark-bar при загрузке)
document.addEventListener('DOMContentLoaded', function() {
    // Обновляем spark-bar при получении события
    if (window.socket) {
        window.socket.on('spark_reaction', function(data) {
            const bar = document.getElementById(`spark-bar-${data.msg_id}`);
            if (bar) bar.innerHTML = `<span class="spark-total">✨ ${data.total}</span>`;
        });
    }
});

// ── Поиск по сообщениям ──────────────────────────────────────────────────────
function showSearchModal() {
    const isGroup = !!currentGroupId;
    const modal = document.createElement('div');
    modal.className = 'modal active';
    modal.id = 'search-msg-modal';
    modal.innerHTML = `<div class="modal-content" style="max-width:520px;">
        <div class="modal-header">
            <h3><i class="fas fa-search"></i> Поиск по сообщениям</h3>
            <button class="close-btn" onclick="this.closest('.modal').remove()">&times;</button>
        </div>
        <div class="modal-body">
            <input type="text" id="search-msg-input" placeholder="Введите текст..." style="width:100%;padding:10px;border:2px solid #e2e8f0;border-radius:10px;font-size:14px;box-sizing:border-box;" oninput="doSearchMessages(this.value)">
            <div id="search-msg-results" style="margin-top:12px;max-height:350px;overflow-y:auto;"></div>
        </div>
    </div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);
    setTimeout(() => document.getElementById('search-msg-input')?.focus(), 100);
}
window.showSearchModal = showSearchModal;

async function doSearchMessages(q) {
    if (q.length < 2) { document.getElementById('search-msg-results').innerHTML = ''; return; }
    const params = new URLSearchParams({ q });
    if (currentGroupId) params.set('group_id', currentGroupId);
    else if (currentChatUserId) params.set('chat_id', currentChatUserId);
    const r = await fetch('/search/messages?' + params);
    const d = await r.json();
    const el = document.getElementById('search-msg-results');
    if (!el) return;
    if (!d.results.length) { el.innerHTML = '<p style="color:#a0aec0;text-align:center;padding:16px;">Ничего не найдено</p>'; return; }
    el.innerHTML = d.results.map(m => `
        <div onclick="scrollToMsg(${m.id});document.getElementById('search-msg-modal')?.remove();"
             style="padding:10px;border-bottom:1px solid #e2e8f0;cursor:pointer;border-radius:8px;" class="search-msg-item">
            <div style="font-size:12px;color:#a0aec0;">${m.sender} · ${m.timestamp}</div>
            <div style="font-size:14px;color:var(--text-primary,#2d3748);margin-top:2px;">${escapeHtml(m.content).substring(0,120)}</div>
        </div>`).join('');
}
window.doSearchMessages = doSearchMessages;

// ── Папки чатов ──────────────────────────────────────────────────────────────
async function showFoldersModal() {
    const r = await fetch('/chat-folders');
    const d = await r.json();
    const folders = d.folders || [];
    const modal = document.createElement('div');
    modal.className = 'modal active';
    modal.innerHTML = `<div class="modal-content" style="max-width:480px;">
        <div class="modal-header">
            <h3><i class="fas fa-folder"></i> Папки чатов</h3>
            <button class="close-btn" onclick="this.closest('.modal').remove()">&times;</button>
        </div>
        <div class="modal-body">
            <div id="folders-list" style="margin-bottom:12px;">
                ${folders.length ? folders.map((f,i) => `
                <div style="display:flex;align-items:center;gap:8px;padding:8px;border:1px solid #e2e8f0;border-radius:8px;margin-bottom:6px;">
                    <span style="font-size:18px;">${f.emoji||'📁'}</span>
                    <span style="flex:1;font-size:14px;">${escapeHtml(f.name)}</span>
                    <button onclick="deleteFolderItem(${i},this)" style="background:#e53e3e;color:#fff;border:none;border-radius:6px;padding:4px 8px;cursor:pointer;font-size:12px;">✕</button>
                </div>`).join('') : '<p style="color:#a0aec0;font-size:13px;">Папок нет</p>'}
            </div>
            <div style="display:flex;gap:8px;">
                <input type="text" id="new-folder-emoji" placeholder="📁" style="width:50px;padding:8px;border:1px solid #e2e8f0;border-radius:8px;text-align:center;">
                <input type="text" id="new-folder-name" placeholder="Название папки" style="flex:1;padding:8px;border:1px solid #e2e8f0;border-radius:8px;">
                <button onclick="addFolderItem()" style="background:#667eea;color:#fff;border:none;border-radius:8px;padding:8px 14px;cursor:pointer;">+</button>
            </div>
        </div>
        <div class="modal-footer">
            <button class="btn btn-primary" onclick="saveFolders(this)"><i class="fas fa-save"></i> Сохранить</button>
        </div>
    </div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    modal._folders = folders;
    modal.id = 'folders-modal';
    document.body.appendChild(modal);
}
window.showFoldersModal = showFoldersModal;

function addFolderItem() {
    const modal = document.getElementById('folders-modal');
    const emoji = document.getElementById('new-folder-emoji').value.trim() || '📁';
    const name = document.getElementById('new-folder-name').value.trim();
    if (!name) return;
    modal._folders = modal._folders || [];
    modal._folders.push({ emoji, name });
    document.getElementById('new-folder-name').value = '';
    document.getElementById('new-folder-emoji').value = '';
    const list = document.getElementById('folders-list');
    const i = modal._folders.length - 1;
    const div = document.createElement('div');
    div.style.cssText = 'display:flex;align-items:center;gap:8px;padding:8px;border:1px solid #e2e8f0;border-radius:8px;margin-bottom:6px;';
    div.innerHTML = `<span style="font-size:18px;">${emoji}</span><span style="flex:1;font-size:14px;">${escapeHtml(name)}</span><button onclick="deleteFolderItem(${i},this)" style="background:#e53e3e;color:#fff;border:none;border-radius:6px;padding:4px 8px;cursor:pointer;font-size:12px;">✕</button>`;
    list.appendChild(div);
}
window.addFolderItem = addFolderItem;

function deleteFolderItem(idx, btn) {
    const modal = document.getElementById('folders-modal');
    modal._folders.splice(idx, 1);
    btn.closest('div[style]').remove();
}
window.deleteFolderItem = deleteFolderItem;

async function saveFolders(btn) {
    const modal = document.getElementById('folders-modal');
    const r = await fetch('/chat-folders', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ folders: modal._folders || [] }) });
    const d = await r.json();
    if (d.success) { modal.remove(); showError('Папки сохранены', 'success'); }
    else showError(d.error || 'Ошибка');
}
window.saveFolders = saveFolders;

// ── Скрытые чаты ─────────────────────────────────────────────────────────────
async function showHiddenChatsModal() {
    const pinPrompt = prompt('Введите PIN скрытых чатов (или оставьте пустым если не установлен):');
    if (pinPrompt === null) return; // нажата "Отмена"
    const r = await fetch('/hidden-chats', { method: 'GET', headers: {'X-Hidden-Pin': pinPrompt || ''} });
    const d = await r.json();
    if (d.error) { showError(d.error); return; }
    const chats = d.chats || [];
    const modal = document.createElement('div');
    modal.className = 'modal active';
    modal.innerHTML = `<div class="modal-content" style="max-width:480px;">
        <div class="modal-header">
            <h3><i class="fas fa-eye-slash"></i> Скрытые чаты</h3>
            <button class="close-btn" onclick="this.closest('.modal').remove()">&times;</button>
        </div>
        <div class="modal-body">
            ${chats.length ? chats.map(c => `
            <div style="display:flex;align-items:center;gap:10px;padding:10px;border-bottom:1px solid #e2e8f0;">
                <div style="width:36px;height:36px;border-radius:50%;background:${c.avatar_color||'#667eea'};display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;">${c.letter||'?'}</div>
                <span style="flex:1;font-size:14px;">${escapeHtml(c.name||c.username)}</span>
                <button onclick="unhideChat(${c.id},this)" style="font-size:12px;padding:4px 10px;background:#667eea;color:#fff;border:none;border-radius:6px;cursor:pointer;">Показать</button>
            </div>`).join('') : '<p style="color:#a0aec0;text-align:center;padding:20px;">Нет скрытых чатов</p>'}
            <div style="margin-top:16px;padding-top:12px;border-top:1px solid #e2e8f0;">
                <button onclick="showSetHiddenPinModal()" style="font-size:13px;padding:8px 14px;background:#e2e8f0;border:none;border-radius:8px;cursor:pointer;width:100%;"><i class="fas fa-lock"></i> Изменить PIN</button>
            </div>
        </div>
    </div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);
}
window.showHiddenChatsModal = showHiddenChatsModal;

async function unhideChat(userId, btn) {
    const r = await fetch('/hidden-chats/toggle', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ other_id: userId }) });
    const d = await r.json();
    if (d.success) { btn.closest('div[style]').remove(); showError('Чат показан', 'success'); }
}
window.unhideChat = unhideChat;

function showSetHiddenPinModal() {
    const pin = prompt('Введите новый PIN (4-6 цифр):');
    if (!pin || !/^\d{4,6}$/.test(pin)) { showError('PIN должен быть 4-6 цифр'); return; }
    fetch('/profile/hidden-chat-pin', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ pin }) })
    .then(r => r.json()).then(d => {
        if (d.success) showError('PIN установлен', 'success');
        else showError(d.error || 'Ошибка');
    });
}
window.showSetHiddenPinModal = showSetHiddenPinModal;

async function hideCurrentChat() {
    if (!currentChatUserId) return;
    const r = await fetch('/hidden-chats/toggle', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ other_id: currentChatUserId }) });
    const d = await r.json();
    if (d.success) { showError('Чат скрыт', 'success'); backToChats(); loadAllChats(); }
    else showError(d.error || 'Ошибка');
}
window.hideCurrentChat = hideCurrentChat;

// ── Таймер самоуничтожения ────────────────────────────────────────────────────
function showTimerModal(msgId, isGroup) {
    const modal = document.createElement('div');
    modal.className = 'modal active';
    modal.innerHTML = `<div class="modal-content" style="max-width:360px;">
        <div class="modal-header">
            <h3><i class="fas fa-clock"></i> Таймер самоуничтожения</h3>
            <button class="close-btn" onclick="this.closest('.modal').remove()">&times;</button>
        </div>
        <div class="modal-body">
            <p style="color:#718096;font-size:13px;margin-bottom:12px;">Сообщение удалится через выбранное время</p>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
                ${[['30 сек',30],['1 мин',60],['5 мин',300],['1 час',3600],['1 день',86400],['1 неделя',604800]].map(([label,secs]) =>
                    `<button onclick="setMsgTimer(${msgId},${secs},${isGroup?'true':'false'},this.closest('.modal'))" style="padding:10px;border:1px solid #e2e8f0;border-radius:8px;cursor:pointer;font-size:13px;background:var(--bg-secondary,#f7fafc);">${label}</button>`
                ).join('')}
            </div>
        </div>
    </div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);
}
window.showTimerModal = showTimerModal;

async function setMsgTimer(msgId, seconds, isGroup, modal) {
    const url = isGroup ? `/group-message/${msgId}/set-timer` : `/message/${msgId}/set-timer`;
    const r = await fetch(url, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ seconds }) });
    const d = await r.json();
    if (d.success) {
        modal?.remove();
        showError('Таймер установлен', 'success');
        // Запускаем локальный таймер удаления
        setTimeout(() => {
            const el = document.querySelector(`[data-message-id="${msgId}"]`);
            if (el) el.remove();
        }, seconds * 1000);
    } else showError(d.error || 'Ошибка');
}
window.setMsgTimer = setMsgTimer;

// ── Тема по расписанию ────────────────────────────────────────────────────────
function startThemeScheduleChecker() {
    function checkSchedule() {
        const schedule = document.body.dataset.themeSchedule;
        if (!schedule) return;
        try {
            const s = JSON.parse(schedule);
            if (!s.light_from || !s.dark_from) return;
            const now = new Date();
            const h = now.getHours(), m = now.getMinutes();
            const cur = h * 60 + m;
            const [lh, lm] = s.light_from.split(':').map(Number);
            const [dh, dm] = s.dark_from.split(':').map(Number);
            const lightStart = lh * 60 + lm;
            const darkStart = dh * 60 + dm;
            let target;
            if (lightStart <= darkStart) {
                target = (cur >= lightStart && cur < darkStart) ? 'light' : 'dark';
            } else {
                target = (cur >= lightStart || cur < darkStart) ? 'light' : 'dark';
            }
            const current = document.documentElement.getAttribute('data-theme') || localStorage.getItem('theme');
            if (current !== target) setTheme(target);
        } catch(e) {}
    }
    checkSchedule();
    setInterval(checkSchedule, 60000);
}

// Загружаем расписание темы при старте
(async function loadThemeSchedule() {
    try {
        const r = await fetch('/profile/theme-schedule');
        if (!r.ok) return;
        const d = await r.json();
        if (d.schedule) {
            document.body.dataset.themeSchedule = JSON.stringify(d.schedule);
            startThemeScheduleChecker();
        }
    } catch(e) {}
})();

// ── Настройки группы: slow mode, welcome, spam keywords ──────────────────────
async function saveSlowMode(groupId) {
    const secs = parseInt(document.getElementById('gs-slow-mode')?.value) || 0;
    const r = await fetch(`/groups/${groupId}/slow-mode`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ seconds: secs }) });
    const d = await r.json();
    showError(d.success ? 'Медленный режим сохранён' : (d.error || 'Ошибка'), d.success ? 'success' : 'error');
}
window.saveSlowMode = saveSlowMode;

async function saveWelcomeMessage(groupId) {
    const msg = document.getElementById('gs-welcome')?.value.trim() || '';
    const r = await fetch(`/groups/${groupId}/welcome-message`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ message: msg }) });
    const d = await r.json();
    showError(d.success ? 'Приветствие сохранено' : (d.error || 'Ошибка'), d.success ? 'success' : 'error');
}
window.saveWelcomeMessage = saveWelcomeMessage;

async function saveSpamKeywords(groupId) {
    const raw = document.getElementById('gs-spam-kw')?.value || '';
    const keywords = raw.split(',').map(k => k.trim()).filter(Boolean);
    const r = await fetch(`/groups/${groupId}/spam-keywords`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ keywords }) });
    const d = await r.json();
    showError(d.success ? 'Ключевые слова сохранены' : (d.error || 'Ошибка'), d.success ? 'success' : 'error');
}
window.saveSpamKeywords = saveSpamKeywords;

// ===== ПРАВА УЧАСТНИКОВ ГРУППЫ =====

async function showMemberPermissions(groupId, userId, displayName) {
    // Загружаем текущие права
    let info = { is_admin: false, admin_permissions: {}, member_restrictions: {} };
    try {
        const r = await fetch(`/groups/${groupId}/members/${userId}/info`);
        if (r.ok) info = await r.json();
    } catch(e) {}

    const ap = info.admin_permissions || {};
    const mr = info.member_restrictions || {};

    const ADMIN_PERMS = [
        { key: 'delete_messages', label: 'Удалять сообщения' },
        { key: 'ban_members',     label: 'Банить участников' },
        { key: 'pin_messages',    label: 'Закреплять сообщения' },
        { key: 'invite_users',    label: 'Приглашать пользователей' },
        { key: 'edit_group',      label: 'Редактировать группу' },
    ];
    const REACTIONS = ['👍','👎','❤️','🔥','😂','😮','😢','🎉','💯','🤔'];

    const adminSection = info.is_admin ? `
        <div style="margin-bottom:14px;">
            <div style="font-size:13px;font-weight:600;color:var(--text-secondary);margin-bottom:8px;text-transform:uppercase;">Права администратора</div>
            ${ADMIN_PERMS.map(p => `
                <label style="display:flex;align-items:center;gap:8px;padding:6px 0;cursor:pointer;font-size:14px;">
                    <input type="checkbox" data-perm="${p.key}" ${ap[p.key] ? 'checked' : ''} style="width:16px;height:16px;">
                    ${p.label}
                </label>
            `).join('')}
            <button onclick="saveMemberPermissions(${groupId},${userId})" style="margin-top:8px;padding:8px 16px;background:var(--primary-color);color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:13px;width:100%;">
                <i class="fas fa-save"></i> Сохранить права
            </button>
        </div>
        <hr style="border:none;border-top:1px solid var(--border-color);margin:12px 0;">
    ` : '';

    const allowedReactions = mr.allowed_reactions || REACTIONS;
    const restrictSection = `
        <div>
            <div style="font-size:13px;font-weight:600;color:var(--text-secondary);margin-bottom:8px;text-transform:uppercase;">Ограничения участника</div>
            <label style="display:flex;align-items:center;gap:8px;padding:6px 0;cursor:pointer;font-size:14px;">
                <input type="checkbox" id="restr-send-msg" ${mr.can_send_messages === false ? '' : 'checked'} style="width:16px;height:16px;">
                Может отправлять сообщения
            </label>
            <label style="display:flex;align-items:center;gap:8px;padding:6px 0;cursor:pointer;font-size:14px;">
                <input type="checkbox" id="restr-send-media" ${mr.can_send_media === false ? '' : 'checked'} style="width:16px;height:16px;">
                Может отправлять медиа
            </label>
            <label style="display:flex;align-items:center;gap:8px;padding:6px 0;cursor:pointer;font-size:14px;">
                <input type="checkbox" id="restr-react" ${mr.can_react === false ? '' : 'checked'} style="width:16px;height:16px;">
                Может ставить реакции
            </label>
            <div style="margin-top:10px;">
                <div style="font-size:13px;color:var(--text-secondary);margin-bottom:6px;">Разрешённые реакции:</div>
                <div style="display:flex;flex-wrap:wrap;gap:6px;">
                    ${REACTIONS.map(e => `
                        <label style="cursor:pointer;font-size:20px;opacity:${allowedReactions.includes(e) ? '1' : '0.3'};" title="${e}">
                            <input type="checkbox" data-reaction="${e}" ${allowedReactions.includes(e) ? 'checked' : ''} style="display:none;" onchange="this.parentElement.style.opacity=this.checked?'1':'0.3'">
                            ${e}
                        </label>
                    `).join('')}
                </div>
            </div>
            <button onclick="saveMemberRestrictions(${groupId},${userId})" style="margin-top:12px;padding:8px 16px;background:#e53e3e;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:13px;width:100%;">
                <i class="fas fa-save"></i> Сохранить ограничения
            </button>
        </div>
    `;

    const modal = document.createElement('div');
    modal.className = 'modal active';
    modal.id = 'member-perms-modal';
    modal.innerHTML = `
        <div class="modal-content" style="max-width:420px;padding:0;overflow:hidden;max-height:90vh;display:flex;flex-direction:column;">
            <div class="modal-header" style="padding:16px 20px;flex-shrink:0;">
                <h2 style="font-size:16px;"><i class="fas fa-sliders-h"></i> Права: ${escapeHtml(displayName)}</h2>
                <button class="close-btn" onclick="this.closest('.modal').remove()"><i class="fas fa-times"></i></button>
            </div>
            <div style="overflow-y:auto;flex:1;padding:16px;">
                ${adminSection}
                ${restrictSection}
            </div>
        </div>
    `;
    document.getElementById('member-perms-modal')?.remove();
    document.body.appendChild(modal);
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
}
window.showMemberPermissions = showMemberPermissions;

async function saveMemberPermissions(groupId, userId) {
    const checks = document.querySelectorAll('#member-perms-modal input[data-perm]');
    const perms = {};
    checks.forEach(c => { perms[c.dataset.perm] = c.checked; });
    try {
        const r = await fetch(`/groups/${groupId}/members/${userId}/role`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_admin: true, admin_permissions: perms })
        });
        const d = await r.json();
        showError(d.success ? 'Права сохранены' : (d.error || 'Ошибка'), d.success ? 'success' : 'error');
    } catch(e) { showError('Ошибка сохранения'); }
}
window.saveMemberPermissions = saveMemberPermissions;

async function saveMemberRestrictions(groupId, userId) {
    const canSendMsg   = document.getElementById('restr-send-msg')?.checked !== false;
    const canSendMedia = document.getElementById('restr-send-media')?.checked !== false;
    const canReact     = document.getElementById('restr-react')?.checked !== false;
    const reactionChecks = document.querySelectorAll('#member-perms-modal input[data-reaction]');
    const allowedReactions = [];
    reactionChecks.forEach(c => { if (c.checked) allowedReactions.push(c.dataset.reaction); });

    const restrictions = {
        can_send_messages: document.getElementById('restr-send-msg')?.checked ?? true,
        can_send_media:    document.getElementById('restr-send-media')?.checked ?? true,
        can_react:         document.getElementById('restr-react')?.checked ?? true,
        allowed_reactions: allowedReactions,
    };
    try {
        const r = await fetch(`/groups/${groupId}/members/${userId}/restrictions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ restrictions })
        });
        const d = await r.json();
        showError(d.success ? 'Ограничения сохранены' : (d.error || 'Ошибка'), d.success ? 'success' : 'error');
    } catch(e) { showError('Ошибка сохранения'); }
}
window.saveMemberRestrictions = saveMemberRestrictions;

function showTransferGroup(groupId) {
    const modal = document.createElement('div');
    modal.className = 'modal active';
    modal.id = 'transfer-group-modal';
    modal.innerHTML = `
        <div class="modal-content" style="max-width:380px;">
            <div class="modal-header">
                <h2 style="font-size:16px;"><i class="fas fa-exchange-alt"></i> Передать группу/канал</h2>
                <button class="close-btn" onclick="this.closest('.modal').remove()"><i class="fas fa-times"></i></button>
            </div>
            <div style="padding:16px;">
                <p style="font-size:13px;color:var(--text-secondary);margin-bottom:14px;">
                    Введите username нового владельца. Он должен быть участником группы.
                </p>
                <input id="transfer-username" type="text" placeholder="@username" style="width:100%;padding:9px 12px;border:1px solid var(--border-color);border-radius:8px;font-size:14px;box-sizing:border-box;margin-bottom:10px;">
                <input id="transfer-2fa" type="text" placeholder="Код 2FA (если включена)" style="width:100%;padding:9px 12px;border:1px solid var(--border-color);border-radius:8px;font-size:14px;box-sizing:border-box;margin-bottom:14px;">
                <button onclick="transferGroup(${groupId})" style="width:100%;padding:10px;background:#744210;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:14px;font-weight:600;">
                    <i class="fas fa-exchange-alt"></i> Подтвердить передачу
                </button>
            </div>
        </div>
    `;
    document.getElementById('transfer-group-modal')?.remove();
    document.body.appendChild(modal);
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
}
window.showTransferGroup = showTransferGroup;

async function transferGroup(groupId) {
    const username = document.getElementById('transfer-username')?.value.trim().replace(/^@/, '');
    const tfaCode  = document.getElementById('transfer-2fa')?.value.trim();
    if (!username) { showError('Введите username'); return; }
    try {
        const r = await fetch(`/groups/${groupId}/transfer`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, tfa_code: tfaCode })
        });
        const d = await r.json();
        if (d.success) {
            showError('Группа передана', 'success');
            document.getElementById('transfer-group-modal')?.remove();
            document.getElementById('group-info-modal')?.remove();
            loadAllChats();
        } else {
            showError(d.error || 'Ошибка передачи');
        }
    } catch(e) { showError('Ошибка передачи'); }
}
window.transferGroup = transferGroup;

// ===== СТИКЕРЫ + ЭМОДЗИ =====

const EMOJI_CATEGORIES = [
    { id: 'people', icon: '😀', label: 'Люди', emojis: ['😀','😃','😄','😁','😆','🥹','😅','😂','🤣','🥲','😊','😇','🙂','🙃','😉','😌','😍','🥰','😘','😗','😙','😚','😋','😛','😝','😜','🤪','🤨','🧐','🤓','😎','🥸','🤩','🥳','😏','😒','😞','😔','😟','😕','🙁','☹️','😣','😖','😫','😩','🥺','😢','😭','😤','😠','😡','🤬','🤯','😳','🥵','🥶','😱','😨','😰','😥','😓','🫣','🤗','🫡','🤔','🫠','🤭','🤫','🤥','😶','🫥','😐','😑','😬','🙄','😯','😦','😧','😮','😲','🥱','😴','🤤','😪','😵','🫨','🤐','🥴','🤢','🤮','🤧','😷','🤒','🤕','🤑','🤠','😈','👿','👹','👺','🤡','💩','👻','💀','☠️','👽','👾','🤖','🎃','😺','😸','😹','😻','😼','😽','🙀','😿','😾'] },
    { id: 'animals', icon: '🐶', label: 'Животные', emojis: ['🐶','🐱','🐭','🐹','🐰','🦊','🐻','🐼','🐨','🐯','🦁','🐮','🐷','🐸','🐵','🙈','🙉','🙊','🐔','🐧','🐦','🐤','🦆','🦅','🦉','🦇','🐺','🐗','🐴','🦄','🐝','🪱','🐛','🦋','🐌','🐞','🐜','🪲','🦟','🦗','🕷','🦂','🐢','🐍','🦎','🦖','🦕','🐙','🦑','🦐','🦞','🦀','🐡','🐠','🐟','🐬','🐳','🐋','🦈','🐊','🐅','🐆','🦓','🦍','🦧','🦣','🐘','🦛','🦏','🐪','🐫','🦒','🦘','🦬','🐃','🐂','🐄','🐎','🐖','🐏','🐑','🦙','🐐','🦌','🐕','🐩','🦮','🐕‍🦺','🐈','🐈‍⬛','🪶','🐓','🦃','🦤','🦚','🦜','🦢','🦩','🕊','🐇','🦝','🦨','🦡','🦫','🦦','🦥','🐁','🐀','🐿','🦔'] },
    { id: 'food', icon: '🍎', label: 'Еда', emojis: ['🍎','🍐','🍊','🍋','🍌','🍉','🍇','🍓','🫐','🍈','🍒','🍑','🥭','🍍','🥥','🥝','🍅','🍆','🥑','🥦','🥬','🥒','🌶','🫑','🧄','🧅','🥔','🍠','🫘','🥐','🥯','🍞','🥖','🥨','🧀','🥚','🍳','🧈','🥞','🧇','🥓','🥩','🍗','🍖','🌭','🍔','🍟','🍕','🫓','🥪','🥙','🧆','🌮','🌯','🫔','🥗','🥘','🫕','🥫','🍝','🍜','🍲','🍛','🍣','🍱','🥟','🦪','🍤','🍙','🍚','🍘','🍥','🥮','🍢','🧁','🍰','🎂','🍮','🍭','🍬','🍫','🍿','🍩','🍪','🌰','🥜','🍯','🧃','🥤','🧋','☕','🍵','🫖','🍺','🍻','🥂','🍷','🫗','🥃','🍸','🍹','🧉','🍾'] },
    { id: 'sports', icon: '⚽', label: 'Спорт', emojis: ['⚽','🏀','🏈','⚾','🥎','🎾','🏐','🏉','🥏','🎱','🪀','🏓','🏸','🏒','🥍','🏑','🏏','🪃','🥅','⛳','🪁','🎣','🤿','🎽','🎿','🛷','🥌','🎯','🪃','🏹','🎣','🤸','🏋️','🤼','🤺','🏇','⛷','🏂','🪂','🏊','🚣','🧗','🚵','🚴','🏆','🥇','🥈','🥉','🏅','🎖','🎗','🎫','🎟','🎪','🤹','🎭','🎨','🎬','🎤','🎧','🎼','🎹','🥁','🪘','🎷','🎺','🎸','🪕','🎻','🎲','♟','🎯','🎳','🎮','🕹'] },
    { id: 'travel', icon: '🚗', label: 'Транспорт', emojis: ['🚗','🚕','🚙','🚌','🚎','🏎','🚓','🚑','🚒','🚐','🛻','🚚','🚛','🚜','🏍','🛵','🛺','🚲','🛴','🛹','🛼','🚏','🛣','🛤','⛽','🚨','🚥','🚦','🛑','🚧','⚓','🛟','⛵','🚤','🛥','🛳','⛴','🚢','✈️','🛩','🛫','🛬','🪂','💺','🚁','🚟','🚠','🚡','🛰','🚀','🛸','🪐','🌍','🌎','🌏','🗺','🧭','🏔','⛰','🌋','🗻','🏕','🏖','🏜','🏝','🏞','🏟','🏛','🏗','🧱','🪨','🪵','🛖','🏘','🏚','🏠','🏡','🏢','🏣','🏤','🏥','🏦','🏨','🏩','🏪','🏫','🏬','🏭','🏯','🏰','💒','🗼','🗽','⛪','🕌','🛕','🕍','⛩','🕋'] },
    { id: 'objects', icon: '💡', label: 'Объекты', emojis: ['💡','🔦','🕯','🪔','🧯','🛢','💰','🪙','💳','💎','⚖️','🪜','🧰','🪛','🔧','🔨','⚒','🛠','⛏','🪚','🔩','🪤','🧲','🔫','💣','🪓','🔪','🗡','⚔️','🛡','🪃','🏹','🪝','🧲','🪜','🧪','🧫','🧬','🔭','🔬','🩺','🩻','💊','💉','🩹','🩼','🩺','🩻','🚪','🛏','🛋','🪑','🚽','🪠','🚿','🛁','🪤','🧴','🧷','🧹','🧺','🧻','🪣','🧼','🫧','🪥','🧽','🧯','🛒','🚪','🪞','🪟','🛏','🛋','🪑','🚽','🚿','🛁','🧴','🧷','🧹','🧺','🧻','🧼','🧽','🛒'] },
    { id: 'symbols', icon: '❤️', label: 'Символы', emojis: ['❤️','🧡','💛','💚','💙','💜','🖤','🤍','🤎','💔','❤️‍🔥','❤️‍🩹','❣️','💕','💞','💓','💗','💖','💘','💝','💟','☮️','✝️','☪️','🕉','☸️','✡️','🔯','🕎','☯️','☦️','🛐','⛎','♈','♉','♊','♋','♌','♍','♎','♏','♐','♑','♒','♓','🆔','⚛️','🉑','☢️','☣️','📴','📳','🈶','🈚','🈸','🈺','🈷️','✴️','🆚','💮','🉐','㊙️','㊗️','🈴','🈵','🈹','🈲','🅰️','🅱️','🆎','🆑','🅾️','🆘','❌','⭕','🛑','⛔','📛','🚫','💯','💢','♨️','🚷','🚯','🚳','🚱','🔞','📵','🚭','❗','❕','❓','❔','‼️','⁉️','🔅','🔆','〽️','⚠️','🚸','🔱','⚜️','🔰','♻️','✅','🈯','💹','❎','🌐','💠','Ⓜ️','🌀','💤','🏧','🚾','♿','🅿️','🛗','🈳','🈂️','🛂','🛃','🛄','🛅'] },
    { id: 'flags', icon: '🏳️', label: 'Флаги', emojis: ['🏳️','🏴','🏁','🚩','🏳️‍🌈','🏳️‍⚧️','🏴‍☠️','🇦🇫','🇦🇱','🇩🇿','🇦🇩','🇦🇴','🇦🇬','🇦🇷','🇦🇲','🇦🇺','🇦🇹','🇦🇿','🇧🇸','🇧🇭','🇧🇩','🇧🇧','🇧🇾','🇧🇪','🇧🇿','🇧🇯','🇧🇹','🇧🇴','🇧🇦','🇧🇼','🇧🇷','🇧🇳','🇧🇬','🇧🇫','🇧🇮','🇨🇻','🇰🇭','🇨🇲','🇨🇦','🇨🇫','🇹🇩','🇨🇱','🇨🇳','🇨🇴','🇰🇲','🇨🇬','🇨🇩','🇨🇷','🇨🇮','🇭🇷','🇨🇺','🇨🇾','🇨🇿','🇩🇰','🇩🇯','🇩🇲','🇩🇴','🇪🇨','🇪🇬','🇸🇻','🇬🇶','🇪🇷','🇪🇪','🇸🇿','🇪🇹','🇫🇯','🇫🇮','🇫🇷','🇬🇦','🇬🇲','🇬🇪','🇩🇪','🇬🇭','🇬🇷','🇬🇩','🇬🇹','🇬🇳','🇬🇼','🇬🇾','🇭🇹','🇭🇳','🇭🇺','🇮🇸','🇮🇳','🇮🇩','🇮🇷','🇮🇶','🇮🇪','🇮🇱','🇮🇹','🇯🇲','🇯🇵','🇯🇴','🇰🇿','🇰🇪','🇰🇮','🇰🇼','🇰🇬','🇱🇦','🇱🇻','🇱🇧','🇱🇸','🇱🇷','🇱🇾','🇱🇮','🇱🇹','🇱🇺','🇲🇬','🇲🇼','🇲🇾','🇲🇻','🇲🇱','🇲🇹','🇲🇭','🇲🇷','🇲🇺','🇲🇽','🇫🇲','🇲🇩','🇲🇨','🇲🇳','🇲🇪','🇲🇦','🇲🇿','🇲🇲','🇳🇦','🇳🇷','🇳🇵','🇳🇱','🇳🇿','🇳🇮','🇳🇪','🇳🇬','🇳🇴','🇴🇲','🇵🇰','🇵🇼','🇵🇸','🇵🇦','🇵🇬','🇵🇾','🇵🇪','🇵🇭','🇵🇱','🇵🇹','🇶🇦','🇷🇴','🇷🇺','🇷🇼','🇰🇳','🇱🇨','🇻🇨','🇼🇸','🇸🇲','🇸🇹','🇸🇦','🇸🇳','🇷🇸','🇸🇱','🇸🇬','🇸🇰','🇸🇮','🇸🇧','🇸🇴','🇿🇦','🇸🇸','🇪🇸','🇱🇰','🇸🇩','🇸🇷','🇸🇪','🇨🇭','🇸🇾','🇹🇼','🇹🇯','🇹🇿','🇹🇭','🇹🇱','🇹🇬','🇹🇴','🇹🇹','🇹🇳','🇹🇷','🇹🇲','🇹🇻','🇺🇬','🇺🇦','🇦🇪','🇬🇧','🇺🇸','🇺🇾','🇺🇿','🇻🇺','🇻🇪','🇻🇳','🇾🇪','🇿🇲','🇿🇼'] },
];

let _stickerPanelOpen = false;
let _stickerPacks = [];
let _emojiPanelTab = 'emoji'; // 'emoji' | 'stickers'
let _emojiActiveCat = 0;

async function toggleStickerPanel() {
    const existing = document.getElementById('sticker-panel');
    if (existing) { existing.remove(); _stickerPanelOpen = false; return; }
    _stickerPanelOpen = true;

    const panel = document.createElement('div');
    panel.id = 'sticker-panel';
    panel.style.cssText = 'position:fixed;width:340px;max-height:420px;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,0.22);z-index:99999;display:flex;flex-direction:column;overflow:hidden;';
    panel.innerHTML = `
        <div style="padding:6px 8px 0;flex-shrink:0;">
            <input id="emoji-search-input" type="text" placeholder="🔍 Поиск эмодзи..." oninput="_emojiSearch(this.value)"
                style="width:100%;padding:6px 10px;border:1.5px solid var(--border-color);border-radius:10px;font-size:13px;background:var(--bg-primary);color:var(--text-primary);box-sizing:border-box;outline:none;">
        </div>
        <div id="emoji-cat-bar" style="display:flex;align-items:center;gap:2px;padding:6px 8px;border-bottom:1px solid var(--border-color);overflow-x:auto;flex-shrink:0;scrollbar-width:none;">
            ${EMOJI_CATEGORIES.map((c,i) => `<button data-cat="${i}" onclick="_emojiSwitchCat(${i})" title="${c.label}" style="background:none;border:none;cursor:pointer;font-size:20px;padding:4px 5px;border-radius:8px;flex-shrink:0;opacity:${i===0?1:0.5};transition:opacity .15s;">${c.icon}</button>`).join('')}
        </div>
        <div id="sticker-panel-body" style="overflow-y:auto;flex:1;padding:8px;"></div>
        <div style="display:flex;border-top:1px solid var(--border-color);flex-shrink:0;">
            <button id="ep-tab-emoji" onclick="_emojiTabSwitch('emoji')" style="flex:1;padding:10px;border:none;background:var(--primary-color);color:#fff;font-size:13px;font-weight:600;cursor:pointer;border-radius:0 0 0 16px;">Эмодзи</button>
            <button id="ep-tab-stickers" onclick="_emojiTabSwitch('stickers')" style="flex:1;padding:10px;border:none;background:var(--bg-secondary);color:var(--text-secondary);font-size:13px;font-weight:600;cursor:pointer;">Стикеры</button>
            <button onclick="showStickerShop()" style="flex:1;padding:10px;border:none;background:var(--bg-secondary);color:#667eea;font-size:13px;font-weight:600;cursor:pointer;border-radius:0 0 16px 0;"><i class="fas fa-store"></i> Магазин</button>
        </div>
    `;

    document.body.appendChild(panel);
    // Позиционируем над кнопкой стикеров
    const stickerBtn = document.getElementById('sticker-btn');
    if (stickerBtn) {
        const r = stickerBtn.getBoundingClientRect();
        const panelW = Math.min(340, window.innerWidth - 16);
        panel.style.width = panelW + 'px';
        let left = r.right - panelW;
        if (left < 8) left = 8;
        panel.style.left = left + 'px';
        panel.style.bottom = (window.innerHeight - r.top + 8) + 'px';
    } else {
        panel.style.right = '8px';
        panel.style.bottom = '80px';
    }

    _emojiPanelTab = 'emoji';
    _emojiActiveCat = 0;
    _renderEmojiTab();

    setTimeout(() => document.addEventListener('click', function h(e) {
        if (!panel.contains(e.target) && !e.target.closest('#sticker-btn')) {
            panel.remove(); _stickerPanelOpen = false;
            document.removeEventListener('click', h);
        }
    }), 100);
}
window.toggleStickerPanel = toggleStickerPanel;

function _emojiTabSwitch(tab) {
    _emojiPanelTab = tab;
    const catBar = document.getElementById('emoji-cat-bar');
    const tabEmoji = document.getElementById('ep-tab-emoji');
    const tabStickers = document.getElementById('ep-tab-stickers');
    if (!tabEmoji) return;
    if (tab === 'emoji') {
        tabEmoji.style.background = 'var(--primary-color)'; tabEmoji.style.color = '#fff';
        tabStickers.style.background = 'var(--bg-secondary)'; tabStickers.style.color = 'var(--text-secondary)';
        catBar.style.display = 'flex';
        _renderEmojiTab();
    } else {
        tabStickers.style.background = 'var(--primary-color)'; tabStickers.style.color = '#fff';
        tabEmoji.style.background = 'var(--bg-secondary)'; tabEmoji.style.color = 'var(--text-secondary)';
        catBar.style.display = 'none';
        _loadStickerPanel();
    }
}
window._emojiTabSwitch = _emojiTabSwitch;

function _emojiSwitchCat(idx) {
    _emojiActiveCat = idx;
    document.querySelectorAll('#emoji-cat-bar button').forEach((b, i) => {
        b.style.opacity = i === idx ? '1' : '0.5';
        b.style.background = i === idx ? 'var(--hover-color)' : 'none';
    });
    _renderEmojiTab();
}
window._emojiSwitchCat = _emojiSwitchCat;

function _renderEmojiTab() {
    const body = document.getElementById('sticker-panel-body');
    if (!body) return;
    const cat = EMOJI_CATEGORIES[_emojiActiveCat];
    body.innerHTML = `
        <div style="font-size:11px;color:var(--text-secondary);padding:2px 4px 6px;font-weight:600;letter-spacing:.5px;">${cat.label}</div>
        <div style="display:flex;flex-wrap:wrap;gap:2px;">
            ${cat.emojis.map(e => `<button onclick="_insertEmoji('${e}')" style="background:none;border:none;cursor:pointer;font-size:24px;padding:4px;border-radius:8px;line-height:1;transition:background .1s;" onmouseover="this.style.background='var(--hover-color)'" onmouseout="this.style.background='none'">${e}</button>`).join('')}
        </div>
    `;
}

function _insertEmoji(emoji) {
    const input = document.getElementById('message-input') || document.getElementById('group-message-input');
    if (!input) return;
    const start = input.selectionStart;
    const end = input.selectionEnd;
    const val = input.value;
    input.value = val.slice(0, start) + emoji + val.slice(end);
    input.selectionStart = input.selectionEnd = start + emoji.length;
    input.focus();
    input.dispatchEvent(new Event('input'));
}
window._insertEmoji = _insertEmoji;

function _emojiSearch(q) {
    const body = document.getElementById('sticker-panel-body');
    const catBar = document.getElementById('emoji-cat-bar');
    if (!body) return;
    q = q.trim().toLowerCase();
    if (!q) {
        if (catBar) catBar.style.display = 'flex';
        _renderEmojiTab();
        return;
    }
    if (catBar) catBar.style.display = 'none';
    // Собираем все эмодзи из всех категорий
    const all = EMOJI_CATEGORIES.flatMap(c => c.emojis);
    // Фильтруем по названию через Intl или просто показываем все (нет словаря — показываем все)
    // Простой подход: ищем по unicode name через canvas trick не работает в браузере без словаря
    // Поэтому просто показываем все эмодзи с подсветкой поиска по категории label
    const matched = EMOJI_CATEGORIES
        .filter(c => c.label.toLowerCase().includes(q) || c.id.toLowerCase().includes(q))
        .flatMap(c => c.emojis);
    const results = matched.length ? matched : all.slice(0, 80);
    body.innerHTML = `
        <div style="font-size:11px;color:var(--text-secondary);padding:2px 4px 6px;font-weight:600;">
            ${matched.length ? `Результаты для «${q}»` : 'Все эмодзи'}
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:2px;">
            ${results.map(e => `<button onclick="_insertEmoji('${e}')" style="background:none;border:none;cursor:pointer;font-size:24px;padding:4px;border-radius:8px;line-height:1;transition:background .1s;" onmouseover="this.style.background='var(--hover-color)'" onmouseout="this.style.background='none'">${e}</button>`).join('')}
        </div>`;
}
window._emojiSearch = _emojiSearch;

async function _loadStickerPanel() {
    const body = document.getElementById('sticker-panel-body');
    if (!body) return;
    body.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-secondary);">Загрузка...</div>';
    try {
        const r = await fetch('/stickers/my');
        const d = await r.json();
        const allPacks = [...(d.owned || []), ...(d.added || [])];
        _stickerPacks = allPacks;
        if (!allPacks.length) {
            body.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-secondary);font-size:13px;">Нет стикеров.<br>Создайте свой пак!</div>';
            return;
        }
        body.innerHTML = '';
        // Кнопка создания пака
        const createBtn = document.createElement('div');
        createBtn.style.cssText = 'padding:4px 0 10px;';
        createBtn.innerHTML = `<button onclick="showCreateStickerPackModal()" style="background:var(--primary-color);color:#fff;border:none;border-radius:8px;padding:5px 12px;cursor:pointer;font-size:12px;width:100%;">+ Создать пак</button>`;
        body.appendChild(createBtn);
        allPacks.forEach(pack => {
            const section = document.createElement('div');
            section.style.cssText = 'margin-bottom:12px;';

            const label = document.createElement('div');
            label.style.cssText = 'font-size:12px;color:var(--text-secondary);margin-bottom:6px;padding:0 4px;';
            label.textContent = pack.name;
            section.appendChild(label);

            const grid = document.createElement('div');
            grid.style.cssText = 'display:flex;flex-wrap:wrap;gap:4px;';

            pack.stickers.forEach(s => {
                const cell = document.createElement('div');
                cell.style.cssText = 'width:60px;height:60px;cursor:pointer;border-radius:8px;padding:2px;transition:background .15s;flex-shrink:0;';
                cell.title = 'Нажмите чтобы отправить';
                cell.addEventListener('mouseover', () => cell.style.background = 'var(--hover-color)');
                cell.addEventListener('mouseout', () => cell.style.background = '');
                cell.addEventListener('click', () => sendStickerFromPanel(s.id));

                if (s.is_animated && window.lottie) {
                    try {
                        const jsonStr = atob(s.image_url.replace('data:application/json;base64,', ''));
                        lottie.loadAnimation({
                            container: cell,
                            renderer: 'svg',
                            loop: true,
                            autoplay: true,
                            animationData: JSON.parse(jsonStr),
                        });
                    } catch(e) { cell.textContent = '🎭'; }
                } else {
                    const img = document.createElement('img');
                    img.src = s.image_url;
                    img.style.cssText = 'width:100%;height:100%;object-fit:contain;';
                    cell.appendChild(img);
                }
                grid.appendChild(cell);
            });

            section.appendChild(grid);
            body.appendChild(section);
        });
    } catch(e) {
        body.innerHTML = '<div style="text-align:center;padding:20px;color:#e53e3e;">Ошибка загрузки</div>';
    }
}

async function sendStickerFromPanel(stickerId) {
    document.getElementById('sticker-panel')?.remove();
    _stickerPanelOpen = false;
    const payload = { sticker_id: stickerId };
    if (currentGroupId) payload.group_id = currentGroupId;
    else if (currentChatUserId) payload.receiver_id = currentChatUserId;
    else return;
    try {
        await fetch('/stickers/send', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
    } catch(e) { showError('Ошибка отправки стикера'); }
}
window.sendStickerFromPanel = sendStickerFromPanel;

// Просмотр пака стикеров по клику на стикер в чате
async function viewStickerPack(packId) {
    try {
        const r = await fetch(`/stickers/pack/${packId}`);
        const d = await r.json();
        if (d.error) { showError(d.error); return; }
        const modal = document.createElement('div');
        modal.className = 'modal active';
        modal.innerHTML = `
            <div class="modal-content" style="max-width:380px;padding:0;overflow:hidden;">
                <div class="modal-header" style="padding:14px 18px;">
                    <h2 style="font-size:16px;">🎨 ${escapeHtml(d.name)}</h2>
                    <button class="close-btn" onclick="this.closest('.modal').remove()"><i class="fas fa-times"></i></button>
                </div>
                <div id="vsp-grid-${packId}" style="padding:12px;display:flex;flex-wrap:wrap;gap:6px;max-height:300px;overflow-y:auto;"></div>
                <div style="padding:12px;border-top:1px solid var(--border-color);">
                    ${!d.is_added && !d.is_owner ? `<button onclick="addStickerPackFromModal(${d.id},this)" style="width:100%;padding:9px;background:var(--primary-color);color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:14px;">Добавить пак</button>`
                    : `<div style="text-align:center;color:var(--text-secondary);font-size:13px;">✓ Пак уже в коллекции</div>`}
                </div>
            </div>`;
        document.body.appendChild(modal);
        modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
        // Вставляем стикеры через DOM чтобы base64 не ломался
        const grid = document.getElementById(`vsp-grid-${packId}`);
        d.stickers.forEach(s => {
            const cell = document.createElement('div');
            cell.style.cssText = 'width:72px;height:72px;border-radius:8px;overflow:hidden;flex-shrink:0;';
            if (s.is_animated && window.lottie) {
                try {
                    const jsonStr = atob(s.image_url.replace('data:application/json;base64,', ''));
                    lottie.loadAnimation({ container: cell, renderer: 'svg', loop: true, autoplay: true, animationData: JSON.parse(jsonStr) });
                } catch(e) { cell.textContent = '🎭'; }
            } else {
                const img = document.createElement('img');
                img.src = s.image_url;
                img.style.cssText = 'width:100%;height:100%;object-fit:contain;';
                cell.appendChild(img);
            }
            grid.appendChild(cell);
        });
    } catch(e) { showError('Ошибка загрузки пака'); }
}
window.viewStickerPack = viewStickerPack;

async function addStickerPackFromModal(packId, btn) {
    btn.disabled = true; btn.textContent = 'Добавление...';
    try {
        const r = await fetch(`/stickers/pack/${packId}/add`, { method: 'POST' });
        const d = await r.json();
        if (d.success) {
            btn.textContent = '✓ Добавлено';
            btn.style.background = '#38a169';
        } else { showError(d.error || 'Ошибка'); btn.disabled = false; }
    } catch(e) { showError('Ошибка'); btn.disabled = false; }
}
window.addStickerPackFromModal = addStickerPackFromModal;

// Устанавливаем src стикеров из data-src после вставки в DOM (base64 ломается в innerHTML)
function _fixStickerImages(container) {
    container.querySelectorAll('img[data-src]').forEach(img => {
        img.src = decodeURIComponent(img.getAttribute('data-src'));
        img.removeAttribute('data-src');
    });
    // Fix voice audio src
    container.querySelectorAll('audio[data-src]').forEach(audio => {
        audio.src = decodeURIComponent(audio.getAttribute('data-src'));
        audio.removeAttribute('data-src');
    });
    // Инициализируем Lottie анимированные стикеры
    container.querySelectorAll('[data-lottie-src]').forEach(el => {
        if (el.dataset.lottieLoaded) return;
        el.dataset.lottieLoaded = '1';
        try {
            const jsonStr = atob(decodeURIComponent(el.dataset.lottieSrc).replace('data:application/json;base64,', ''));
            const animData = JSON.parse(jsonStr);
            if (window.lottie) {
                lottie.loadAnimation({
                    container: el,
                    renderer: 'svg',
                    loop: true,
                    autoplay: true,
                    animationData: animData,
                });
            }
        } catch(e) { console.warn('Lottie load error:', e); }
    });
}
window._fixStickerImages = _fixStickerImages;

// Клик по стикеру в чате — читаем URL из data-атрибута
function _onStickerClick(el) {
    const stickerUrl = decodeURIComponent(el.dataset.stickerUrl || '');
    const packId = el.dataset.packId ? parseInt(el.dataset.packId) : null;
    viewStickerPackBySticker(stickerUrl, packId);
}
window._onStickerClick = _onStickerClick;

// Просмотр пака стикеров по URL стикера (клик на стикер в чате)
async function viewStickerPackBySticker(stickerUrl, packId) {
    // Если packId известен — используем его, иначе ищем по URL
    if (packId) {
        viewStickerPack(packId);
        return;
    }
    // Ищем пак по URL стикера среди своих паков
    try {
        const r = await fetch('/stickers/my');
        const d = await r.json();
        const allPacks = [...(d.owned || []), ...(d.added || [])];
        for (const pack of allPacks) {
            if (pack.stickers.some(s => s.image_url === stickerUrl)) {
                viewStickerPack(pack.id);
                return;
            }
        }
        // Пак не найден в коллекции — показываем просто стикер с предложением найти пак
        const modal = document.createElement('div');
        modal.className = 'modal active';
        modal.innerHTML = `
            <div class="modal-content" style="max-width:280px;text-align:center;padding:24px;">
                <img src="${stickerUrl}" style="width:140px;height:140px;object-fit:contain;border-radius:12px;margin-bottom:12px;">
                <p style="color:var(--text-secondary);font-size:13px;">Пак этого стикера не найден в вашей коллекции</p>
                <button onclick="this.closest('.modal').remove()" style="margin-top:12px;padding:8px 20px;background:var(--primary-color);color:#fff;border:none;border-radius:8px;cursor:pointer;">Закрыть</button>
            </div>`;
        document.body.appendChild(modal);
        modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    } catch(e) { showError('Ошибка'); }
}
window.viewStickerPackBySticker = viewStickerPackBySticker;

function showCreateStickerPackModal() {
    document.getElementById('sticker-panel')?.remove();
    const modal = document.createElement('div');
    modal.className = 'modal active';
    modal.id = 'create-sticker-modal';
    modal.innerHTML = `
        <div class="modal-content" style="max-width:400px;">
            <div class="modal-header">
                <h2 style="font-size:16px;">🎨 Создать пак стикеров</h2>
                <button class="close-btn" onclick="this.closest('.modal').remove()"><i class="fas fa-times"></i></button>
            </div>
            <div style="padding:16px;">
                <input id="sp-name" type="text" placeholder="Название пака" style="width:100%;padding:9px 12px;border:1px solid var(--border-color);border-radius:8px;font-size:14px;box-sizing:border-box;margin-bottom:12px;">
                <label style="display:block;margin-bottom:8px;font-size:13px;color:var(--text-secondary);">Загрузите стикеры (PNG/GIF/WebP, до 20 штук):</label>
                <input id="sp-files" type="file" accept="image/png,image/gif,image/webp,image/jpeg" multiple style="width:100%;margin-bottom:12px;">
                <div id="sp-preview" style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px;"></div>
                <button onclick="submitCreateStickerPack()" style="width:100%;padding:10px;background:var(--primary-color);color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:14px;font-weight:600;">Создать</button>
            </div>
        </div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.getElementById('sp-files').addEventListener('change', function() {
        const preview = document.getElementById('sp-preview');
        preview.innerHTML = '';
        Array.from(this.files).slice(0, 20).forEach(f => {
            const url = URL.createObjectURL(f);
            preview.innerHTML += `<img src="${url}" style="width:56px;height:56px;object-fit:contain;border-radius:6px;border:1px solid var(--border-color);">`;
        });
    });
}
window.showCreateStickerPackModal = showCreateStickerPackModal;

async function submitCreateStickerPack() {
    const name = document.getElementById('sp-name')?.value.trim();
    const files = document.getElementById('sp-files')?.files;
    if (!name) { showError('Введите название'); return; }
    if (!files || files.length === 0) { showError('Выберите файлы'); return; }
    const fd = new FormData();
    fd.append('name', name);
    Array.from(files).slice(0, 20).forEach(f => fd.append('stickers', f));
    try {
        const r = await fetch('/stickers/pack/create', { method: 'POST', body: fd });
        const d = await r.json();
        if (d.success) {
            showError('Пак создан!', 'success');
            document.getElementById('create-sticker-modal')?.remove();
        } else { showError(d.error || 'Ошибка'); }
    } catch(e) { showError('Ошибка создания'); }
}
window.submitCreateStickerPack = submitCreateStickerPack;

// ── Web Push уведомления ──────────────────────────────────────────────────────
async function initWebPush() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
    try {
        const reg = await navigator.serviceWorker.register('/static/js/sw.js');
        const perm = await Notification.requestPermission();
        if (perm !== 'granted') return;
        const sub = await reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: await getVapidKey()
        });
        await fetch('/push/subscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(sub)
        });
    } catch(e) { console.log('Push init error:', e); }
}

async function getVapidKey() {
    const r = await fetch('/push/vapid-key');
    const d = await r.json();
    const base64 = d.public_key;
    const raw = atob(base64.replace(/-/g,'+').replace(/_/g,'/'));
    return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}

// Запускаем после загрузки

// ══════════════════════════════════════════════════════════════════════════════
// РОЛИ В ГРУППАХ — UI
// ══════════════════════════════════════════════════════════════════════════════

async function loadGroupRolesUI(groupId) {
    const [rolesResp, membersResp] = await Promise.all([
        fetch(`/groups/${groupId}/roles`),
        fetch(`/groups/${groupId}/members_with_roles`)
    ]);
    const rolesData = await rolesResp.json();
    const membersData = await membersResp.json();
    const roles = rolesData.roles || [];
    const members = membersData.members || [];

    const container = document.getElementById('gs-roles-list');
    if (!container) return;

    let html = '';
    if (roles.length) {
        html += '<div style="margin-bottom:8px;font-size:12px;color:var(--text-secondary);font-weight:600;">РОЛИ</div>';
        html += roles.map(r => `
            <div style="display:flex;align-items:center;gap:8px;padding:6px 8px;border:1px solid var(--border-color);border-radius:8px;margin-bottom:4px;">
                <span style="width:12px;height:12px;border-radius:50%;background:${r.color};flex-shrink:0;"></span>
                <span style="flex:1;font-size:13px;font-weight:600;">${escapeHtml(r.name)}</span>
                <button onclick="deleteGroupRoleUI(${groupId},${r.id},this.closest('div'))" style="padding:3px 8px;background:none;border:1px solid #e53e3e;border-radius:6px;color:#e53e3e;cursor:pointer;font-size:11px;"><i class="fas fa-trash"></i></button>
            </div>
        `).join('');
    }

    if (members.length) {
        html += '<div style="margin:10px 0 6px;font-size:12px;color:var(--text-secondary);font-weight:600;">УЧАСТНИКИ</div>';
        html += members.map(m => `
            <div style="display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:8px;margin-bottom:4px;background:var(--bg-secondary);">
                <div style="width:28px;height:28px;border-radius:50%;background:${m.avatar_url ? 'url(' + m.avatar_url + ') center/cover' : m.avatar_color};display:flex;align-items:center;justify-content:center;color:#fff;font-size:12px;font-weight:700;flex-shrink:0;">${m.avatar_url ? '' : (m.display_name[0] || '?').toUpperCase()}</div>
                <span style="flex:1;font-size:13px;">${escapeHtml(m.display_name)}</span>
                <select onchange="assignMemberRoleUI(${groupId},${m.user_id},this.value)" style="padding:4px 8px;border:1px solid var(--border-color);border-radius:6px;font-size:12px;background:var(--bg-primary);color:var(--text-primary);">
                    <option value="">— Без роли —</option>
                    ${roles.map(r => `<option value="${r.id}" ${m.role && m.role.id === r.id ? 'selected' : ''}>${escapeHtml(r.name)}</option>`).join('')}
                </select>
            </div>
        `).join('');
    }

    container.innerHTML = html || '<div style="color:var(--text-secondary);font-size:13px;">Нет ролей</div>';
}

async function createGroupRoleUI(groupId) {
    const nameInput = document.getElementById('gs-new-role-name');
    const colorInput = document.getElementById('gs-new-role-color');
    const name = nameInput?.value.trim();
    if (!name) { showToast('Введите название роли', 'error'); return; }
    const resp = await fetch(`/groups/${groupId}/roles`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, color: colorInput?.value || '#667eea' })
    });
    const data = await resp.json();
    if (data.success) {
        nameInput.value = '';
        showToast('Роль создана', 'success');
        loadGroupRolesUI(groupId);
    } else {
        showToast(data.error || 'Ошибка', 'error');
    }
}

async function deleteGroupRoleUI(groupId, roleId, el) {
    if (!confirm('Удалить роль?')) return;
    const resp = await fetch(`/groups/${groupId}/roles/${roleId}`, { method: 'DELETE' });
    const data = await resp.json();
    if (data.success) { showToast('Роль удалена', 'success'); loadGroupRolesUI(groupId); }
    else showToast(data.error || 'Ошибка', 'error');
}

async function assignMemberRoleUI(groupId, userId, roleId) {
    const resp = await fetch(`/groups/${groupId}/members/${userId}/role`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role_id: roleId || null })
    });
    const data = await resp.json();
    if (data.success) showToast('Роль назначена', 'success');
    else showToast(data.error || 'Ошибка', 'error');
}

document.addEventListener('DOMContentLoaded', () => {
    // Небольшая задержка чтобы не мешать основной загрузке
    setTimeout(initWebPush, 3000);
});

// ── Drag & Drop файлов в чат ──────────────────────────────────────────────────
(function initDragDrop() {
    document.addEventListener('DOMContentLoaded', () => {
        const chatArea = document.getElementById('chat-area');
        if (!chatArea) return;

        let dragCounter = 0;

        // Создаём оверлей
        const overlay = document.createElement('div');
        overlay.id = 'drag-drop-overlay';
        overlay.style.cssText = `
            display:none; position:fixed; inset:0; background:rgba(102,126,234,0.15);
            backdrop-filter:blur(2px); z-index:9999; align-items:center; justify-content:center;
            pointer-events:none;
        `;
        overlay.innerHTML = `
            <div style="background:var(--bg-primary,#fff);border:3px dashed #667eea;border-radius:20px;
                padding:40px 60px;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,0.2);">
                <i class="fas fa-cloud-upload-alt" style="font-size:48px;color:#667eea;display:block;margin-bottom:12px;"></i>
                <div style="font-size:18px;font-weight:600;color:#667eea;">Отпустите для отправки</div>
            </div>`;
        document.body.appendChild(overlay);

        document.addEventListener('dragenter', e => {
            if (!e.dataTransfer.types.includes('Files')) return;
            dragCounter++;
            overlay.style.display = 'flex';
        });

        document.addEventListener('dragleave', e => {
            dragCounter--;
            if (dragCounter <= 0) { dragCounter = 0; overlay.style.display = 'none'; }
        });

        document.addEventListener('dragover', e => e.preventDefault());

        document.addEventListener('drop', e => {
            e.preventDefault();
            dragCounter = 0;
            overlay.style.display = 'none';
            const files = Array.from(e.dataTransfer.files);
            if (!files.length) return;
            // Используем существующую систему выбора файлов
            if (typeof showFilesPreviewModal === 'function') {
                window.selectedFiles = files;
                showFilesPreviewModal();
            }
        });
    });
})();

// ── Превью ссылок в сообщениях ────────────────────────────────────────────────
const _linkPreviewCache = {};

async function fetchLinkPreview(url) {
    if (_linkPreviewCache[url] !== undefined) return _linkPreviewCache[url];
    try {
        const r = await fetch('/link-preview?url=' + encodeURIComponent(url));
        if (!r.ok) { _linkPreviewCache[url] = null; return null; }
        const d = await r.json();
        _linkPreviewCache[url] = d.preview || null;
        return _linkPreviewCache[url];
    } catch { _linkPreviewCache[url] = null; return null; }
}

function renderLinkPreview(preview) {
    if (!preview || !preview.title) return '';
    return `
    <div class="link-preview" style="margin-top:8px;border-left:3px solid #667eea;padding:8px 12px;
        background:var(--bg-secondary,#f8fafc);border-radius:0 8px 8px 0;max-width:320px;cursor:pointer;"
        onclick="window.open('${escapeHtml(preview.url)}','_blank')">
        ${preview.image ? `<img src="${escapeHtml(preview.image)}" style="width:100%;max-height:120px;object-fit:cover;border-radius:6px;margin-bottom:6px;" onerror="this.remove()">` : ''}
        <div style="font-weight:600;font-size:13px;color:var(--text-primary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(preview.title)}</div>
        ${preview.description ? `<div style="font-size:12px;color:var(--text-secondary);margin-top:2px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;">${escapeHtml(preview.description)}</div>` : ''}
        <div style="font-size:11px;color:#a0aec0;margin-top:4px;">${escapeHtml(preview.domain || '')}</div>
    </div>`;
}

// Вызывается после рендера сообщения — ищет ссылки и добавляет превью
async function attachLinkPreviews(msgEl, text) {
    const urlRegex = /https?:\/\/[^\s<>"]+/g;
    const urls = text.match(urlRegex);
    if (!urls || !urls.length) return;
    const url = urls[0]; // только первая ссылка
    const preview = await fetchLinkPreview(url);
    if (!preview) return;
    const previewEl = document.createElement('div');
    previewEl.innerHTML = renderLinkPreview(preview);
    msgEl.appendChild(previewEl.firstChild);
}
window.attachLinkPreviews = attachLinkPreviews;

// ── @Упоминания в группах ────────────────────────────────────────────────────

(function() {
    const input = document.getElementById('message-input');
    const dropdown = document.getElementById('mention-dropdown');
    if (!input || !dropdown) return;

    let _mentionMembers = []; // участники текущей группы

    // Загружаем участников при открытии группы
    window._loadMentionMembers = function(groupId) {
        if (!groupId) { _mentionMembers = []; return; }
        fetch(`/groups/${groupId}/members`)
            .then(r => r.json())
            .then(data => { _mentionMembers = data.members || []; })
            .catch(() => {});
    };

    input.addEventListener('input', function() {
        const val = this.value;
        const cursor = this.selectionStart;
        // Ищем @ перед курсором
        const before = val.slice(0, cursor);
        const match = before.match(/@(\w*)$/);
        if (!match || !window.currentGroupId) {
            dropdown.style.display = 'none';
            return;
        }
        const query = match[1].toLowerCase();
        const filtered = _mentionMembers.filter(m =>
            m.username.toLowerCase().startsWith(query) ||
            (m.display_name || '').toLowerCase().startsWith(query)
        ).slice(0, 6);

        if (!filtered.length) { dropdown.style.display = 'none'; return; }

        dropdown.innerHTML = filtered.map(m => `
            <div class="mention-item" data-username="${m.username}" style="display:flex;align-items:center;gap:10px;padding:10px 14px;cursor:pointer;transition:background 0.15s;">
                <div style="width:32px;height:32px;border-radius:50%;background:${m.avatar_color};display:flex;align-items:center;justify-content:center;color:white;font-weight:700;font-size:14px;flex-shrink:0;">
                    ${m.avatar_letter || m.username[0].toUpperCase()}
                </div>
                <div>
                    <div style="font-weight:600;font-size:13px;">${escapeHtml(m.display_name || m.username)}</div>
                    <div style="font-size:11px;color:var(--text-secondary);">@${escapeHtml(m.username)}</div>
                </div>
            </div>
        `).join('');
        dropdown.style.display = 'block';

        dropdown.querySelectorAll('.mention-item').forEach(item => {
            item.addEventListener('mouseenter', () => item.style.background = 'var(--hover-bg)');
            item.addEventListener('mouseleave', () => item.style.background = '');
            item.addEventListener('mousedown', e => {
                e.preventDefault();
                const uname = item.dataset.username;
                // Заменяем @query на @username
                const newVal = val.slice(0, cursor).replace(/@(\w*)$/, '@' + uname + ' ') + val.slice(cursor);
                input.value = newVal;
                input.focus();
                dropdown.style.display = 'none';
            });
        });
    });

    input.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') dropdown.style.display = 'none';
    });

    document.addEventListener('click', e => {
        if (!dropdown.contains(e.target) && e.target !== input) {
            dropdown.style.display = 'none';
        }
    });
})();

// Уведомление об упоминании
if (typeof socket !== 'undefined' && socket) {
    socket.on('mention_notification', function(data) {
        showMessageNotification({
            name: `${data.sender_name} упомянул вас в «${data.group_name}»`,
            text: data.content,
            avatarLetter: data.sender_name ? data.sender_name[0] : '?',
            avatarColor: '#667eea',
            onClick: () => { if (data.group_id) openGroup(data.group_id, data.group_name); }
        });
    });
} else {
    // Подключаем после инициализации сокета
    document.addEventListener('socketReady', function() {
        socket.on('mention_notification', function(data) {
            showMessageNotification({
                name: `${data.sender_name} упомянул вас в «${data.group_name}»`,
                text: data.content,
                avatarLetter: data.sender_name ? data.sender_name[0] : '?',
                avatarColor: '#667eea',
                onClick: () => { if (data.group_id) openGroup(data.group_id, data.group_name); }
            });
        });
    });
}

// ── Истории (Stories) ────────────────────────────────────────────────────────

let _storyType = 'text';
let _storyMediaUrl = null;
let _storyMediaType = 'text';

function setStoryType(type) {
    _storyType = type;
    document.getElementById('story-text-area').style.display = type === 'text' ? 'block' : 'none';
    document.getElementById('story-media-area').style.display = type !== 'text' ? 'block' : 'none';
    ['text','image','video'].forEach(t => {
        const btn = document.getElementById('story-type-' + t);
        if (btn) btn.className = 'btn ' + (t === type ? 'btn-primary' : 'btn-secondary');
    });
    _storyMediaUrl = null;
}

function showCreateStoryModal() {
    setStoryType('text');
    document.getElementById('story-content').value = '';
    document.getElementById('story-char-count').textContent = '0/500';
    document.getElementById('create-story-modal').classList.add('active');
}

document.addEventListener('DOMContentLoaded', function() {
    const ta = document.getElementById('story-content');
    if (ta) ta.addEventListener('input', function() {
        document.getElementById('story-char-count').textContent = this.value.length + '/500';
    });
});

function previewStoryMedia(event) {
    const file = event.target.files[0];
    if (!file) return;
    const preview = document.getElementById('story-media-preview');
    const formData = new FormData();
    formData.append('file', file);
    fetch('/story/upload', { method: 'POST', body: formData })
        .then(r => r.json())
        .then(data => {
            if (data.error) { alert(data.error); return; }
            _storyMediaUrl = data.media_url;
            _storyMediaType = data.media_type;
            if (data.media_type === 'image') {
                preview.innerHTML = `<img src="${data.media_url}" style="max-width:100%;border-radius:8px;max-height:200px;object-fit:cover;">`;
            } else {
                preview.innerHTML = `<video src="${data.media_url}" controls style="max-width:100%;border-radius:8px;max-height:200px;"></video>`;
            }
        }).catch(() => alert('Ошибка загрузки файла'));
}

async function submitStory() {
    let content = '';
    let mediaType = 'text';
    let mediaUrl = '';

    if (_storyType === 'text') {
        content = document.getElementById('story-content').value.trim();
        if (!content) { alert('Введите текст истории'); return; }
        mediaType = 'text';
    } else {
        if (!_storyMediaUrl) { alert('Выберите файл'); return; }
        mediaUrl = _storyMediaUrl;
        mediaType = _storyMediaType;
        content = mediaUrl;
    }

    const resp = await fetch('/story/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, media_url: mediaUrl, media_type: mediaType })
    });
    const data = await resp.json();
    if (data.error) { alert(data.error); return; }
    document.getElementById('create-story-modal').classList.remove('active');
    loadStories();
}

async function loadStories() {
    const container = document.getElementById('stories-list');
    if (!container) return;
    try {
        const resp = await fetch('/stories');
        const data = await resp.json();
        if (!data.stories || !data.stories.length) {
            container.innerHTML = '';
            return;
        }
        // Группируем по пользователю
        const byUser = {};
        data.stories.forEach(s => {
            if (!byUser[s.user.id]) byUser[s.user.id] = { user: s.user, stories: [] };
            byUser[s.user.id].stories.push(s);
        });
        container.innerHTML = Object.values(byUser).map(({ user, stories }) => {
            const avatarStyle = user.avatar_url
                ? `background-image:url('${user.avatar_url}');background-size:cover;background-position:center;background-color:${user.avatar_color};`
                : `background:${user.avatar_color};`;
            const avatarContent = user.avatar_url ? '' : `<span style="color:white;font-weight:700;font-size:18px;">${user.avatar_letter}</span>`;
            return `
                <div onclick="viewUserStories(${JSON.stringify(stories).replace(/"/g,'&quot;')}, '${escapeHtml(user.display_name)}')"
                    style="flex-shrink:0;cursor:pointer;text-align:center;">
                    <div style="width:52px;height:52px;border-radius:50%;${avatarStyle}display:flex;align-items:center;justify-content:center;border:2.5px solid #667eea;box-sizing:border-box;">
                        ${avatarContent}
                    </div>
                    <div style="font-size:10px;color:var(--text-secondary);margin-top:3px;white-space:nowrap;max-width:56px;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(user.display_name)}</div>
                </div>
            `;
        }).join('');
    } catch(e) {}
}

let _storyViewIndex = 0;
let _storyViewList = [];

function viewUserStories(stories, userName) {
    _storyViewList = stories;
    _storyViewIndex = 0;
    renderStoryView();
    document.getElementById('story-view-modal').classList.add('active');
    // Отмечаем просмотр
    fetch('/story/' + stories[0].id + '/view', { method: 'POST' }).catch(() => {});
}

function renderStoryView() {
    const story = _storyViewList[_storyViewIndex];
    if (!story) return;
    const total = _storyViewList.length;
    const progress = Array.from({length: total}, (_, i) =>
        `<div style="flex:1;height:3px;border-radius:2px;background:${i <= _storyViewIndex ? 'white' : 'rgba(255,255,255,0.3)'};"></div>`
    ).join('');

    let mediaHtml = '';
    if (story.media_type === 'image') {
        mediaHtml = `<img src="${story.content}" style="width:100%;border-radius:12px;max-height:400px;object-fit:cover;">`;
    } else if (story.media_type === 'video') {
        mediaHtml = `<video src="${story.content}" controls autoplay style="width:100%;border-radius:12px;max-height:400px;"></video>`;
    } else {
        mediaHtml = `<div style="background:linear-gradient(135deg,#667eea,#764ba2);border-radius:12px;padding:40px 24px;text-align:center;min-height:200px;display:flex;align-items:center;justify-content:center;">
            <p style="color:white;font-size:18px;font-weight:500;line-height:1.5;margin:0;">${escapeHtml(story.content)}</p>
        </div>`;
    }

    document.getElementById('story-view-content').innerHTML = `
        <div style="display:flex;gap:4px;margin-bottom:12px;">${progress}</div>
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;">
            <div style="width:36px;height:36px;border-radius:50%;background:${story.user.avatar_color};display:flex;align-items:center;justify-content:center;color:white;font-weight:700;font-size:14px;flex-shrink:0;">
                ${story.user.avatar_letter}
            </div>
            <div>
                <div style="color:white;font-weight:600;font-size:14px;">${escapeHtml(story.user.display_name)}</div>
                <div style="color:rgba(255,255,255,0.7);font-size:12px;">${story.created_at}</div>
            </div>
        </div>
        ${mediaHtml}
        <div style="display:flex;justify-content:space-between;margin-top:12px;">
            <button onclick="prevStory()" style="background:rgba(255,255,255,0.2);border:none;color:white;padding:8px 16px;border-radius:8px;cursor:pointer;${_storyViewIndex === 0 ? 'opacity:0.3;' : ''}">
                <i class="fas fa-chevron-left"></i>
            </button>
            <span style="color:rgba(255,255,255,0.7);font-size:13px;align-self:center;">${_storyViewIndex + 1} / ${total}</span>
            <button onclick="nextStory()" style="background:rgba(255,255,255,0.2);border:none;color:white;padding:8px 16px;border-radius:8px;cursor:pointer;${_storyViewIndex === total - 1 ? 'opacity:0.3;' : ''}">
                <i class="fas fa-chevron-right"></i>
            </button>
        </div>
    `;
}

function nextStory() {
    if (_storyViewIndex < _storyViewList.length - 1) {
        _storyViewIndex++;
        renderStoryView();
        fetch('/story/' + _storyViewList[_storyViewIndex].id + '/view', { method: 'POST' }).catch(() => {});
    }
}

function prevStory() {
    if (_storyViewIndex > 0) {
        _storyViewIndex--;
        renderStoryView();
    }
}

// Загружаем истории при старте
document.addEventListener('DOMContentLoaded', loadStories);


// ═══════════════════════════════════════════════════════════════════════════════
// ГОЛОСОВЫЕ СООБЩЕНИЯ
// ═══════════════════════════════════════════════════════════════════════════════

let _voiceRecorder = null;
let _voiceChunks = [];
let _voiceStartTime = 0;
let _voiceTimerInterval = null;

async function startVoiceRecord() {
    if (_voiceRecorder) return;
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        _voiceChunks = [];
        _voiceStartTime = Date.now();
        _voiceRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
        _voiceRecorder.ondataavailable = e => { if (e.data.size > 0) _voiceChunks.push(e.data); };
        _voiceRecorder.onstop = _uploadVoice;
        _voiceRecorder.start();
        const btn = document.getElementById('voice-btn');
        if (btn) { btn.style.background = '#e53e3e'; btn.style.color = 'white'; }
        // Timer
        _voiceTimerInterval = setInterval(() => {
            const sec = Math.floor((Date.now() - _voiceStartTime) / 1000);
            const m = String(Math.floor(sec / 60)).padStart(2, '0');
            const s = String(sec % 60).padStart(2, '0');
            const inp = document.getElementById('message-input');
            if (inp) inp.placeholder = `🔴 ${m}:${s} — отпустите для отправки`;
        }, 500);
    } catch (e) {
        showToast('Нет доступа к микрофону', 'error');
    }
}

function stopVoiceRecord() {
    if (!_voiceRecorder || _voiceRecorder.state === 'inactive') return;
    _voiceRecorder.stop();
    _voiceRecorder.stream.getTracks().forEach(t => t.stop());
    _voiceRecorder = null;
    clearInterval(_voiceTimerInterval);
    const btn = document.getElementById('voice-btn');
    if (btn) { btn.style.background = ''; btn.style.color = ''; }
    const inp = document.getElementById('message-input');
    if (inp) inp.placeholder = 'Введите сообщение...';
}

function cancelVoiceRecord() {
    if (!_voiceRecorder) return;
    // Останавливаем без отправки — очищаем chunks перед stop
    _voiceChunks = [];
    if (_voiceRecorder.state !== 'inactive') {
        _voiceRecorder.stop();
        _voiceRecorder.stream.getTracks().forEach(t => t.stop());
    }
    _voiceRecorder = null;
    clearInterval(_voiceTimerInterval);
    const btn = document.getElementById('voice-btn');
    if (btn) { btn.style.background = ''; btn.style.color = ''; }
    const inp = document.getElementById('message-input');
    if (inp) inp.placeholder = 'Введите сообщение...';
    showToast('Запись отменена', 'info');
}

function stopVideoCircleRecord() {
    // Закрываем видео рекордер если открыт
    const modal = document.getElementById('video-modal');
    if (modal) {
        // Триггерим отправку через существующую кнопку
        const sendBtn = modal.querySelector('.send-video-btn, [onclick*="sendVideoCircle"], [onclick*="stopRecording"]');
        if (sendBtn) sendBtn.click();
        else modal.remove();
    }
}

function playVoiceMsg(url, uid, btn) {
    const audio = document.getElementById(uid);
    if (!audio) return;
    if (audio.paused) {
        document.querySelectorAll('audio[id^="voice_"]').forEach(a => { if (a.id !== uid) { a.pause(); a.currentTime = 0; } });
        audio.play();
        btn.innerHTML = '<i class="fas fa-pause"></i>';
        audio._progressInterval = setInterval(() => {
            const bar = document.getElementById(uid + '_bar');
            const durEl = document.getElementById(uid + '_dur');
            if (bar && audio.duration) {
                const pct = (audio.currentTime / audio.duration * 100).toFixed(1);
                bar.style.setProperty('--progress', pct + '%');
            }
            if (durEl && audio.duration) {
                const rem = Math.floor(audio.duration - audio.currentTime);
                durEl.textContent = String(Math.floor(rem/60)).padStart(2,'0') + ':' + String(rem%60).padStart(2,'0');
            }
        }, 200);
    } else {
        audio.pause();
        btn.innerHTML = '<i class="fas fa-play"></i>';
        clearInterval(audio._progressInterval);
    }
}

// Переключение скорости воспроизведения ГС
function cycleVoiceSpeed(uid, btn) {
    const audio = document.getElementById(uid);
    if (!audio) return;
    const speeds = [1, 1.5, 2, 0.5];
    const cur = audio.playbackRate || 1;
    const idx = speeds.indexOf(cur);
    const next = speeds[(idx + 1) % speeds.length];
    audio.playbackRate = next;
    btn.textContent = next + 'x';
}

function resetVoiceBtn(uid) {
    const audio = document.getElementById(uid);
    if (audio) { clearInterval(audio._progressInterval); audio.currentTime = 0; }
    const bar = document.getElementById(uid + '_bar');
    if (bar) bar.style.setProperty('--progress', '0%');
    const btn = document.querySelector(`button[onclick*="${uid}"]`);
    if (btn) btn.innerHTML = '<i class="fas fa-play"></i>';
}

async function _uploadVoice() {
    if (!_voiceChunks.length) return;
    const blob = new Blob(_voiceChunks, { type: 'audio/webm' });
    const duration = Math.floor((Date.now() - _voiceStartTime) / 1000);
    if (duration < 1) return;
    if (!currentChatUserId) { showToast('Откройте чат перед записью', 'error'); return; }
    const fd = new FormData();
    fd.append('audio', blob, 'voice.webm');
    fd.append('receiver_id', currentChatUserId);
    fd.append('duration', duration);
    try {
        const resp = await fetch('/send/voice', { method: 'POST', body: fd });
        const data = await resp.json();
        if (!data.success) showToast(data.error || 'Ошибка отправки', 'error');
    } catch (e) { showToast('Ошибка загрузки голосового', 'error'); }
}

// ═══════════════════════════════════════════════════════════════════════════════
// ПРЕДПРОСМОТР ССЫЛОК
// ═══════════════════════════════════════════════════════════════════════════════

let _linkPreviewTimeout = null;
let _lastPreviewUrl = null;

function initLinkPreview() {
    const inp = document.getElementById('message-input');
    if (!inp) return;
    inp.addEventListener('input', () => {
        clearTimeout(_linkPreviewTimeout);
        _linkPreviewTimeout = setTimeout(() => {
            const text = inp.value;
            const urlMatch = text.match(/https?:\/\/[^\s]+/);
            if (urlMatch && urlMatch[0] !== _lastPreviewUrl) {
                _lastPreviewUrl = urlMatch[0];
                fetchLinkPreview(urlMatch[0]);
            } else if (!urlMatch) {
                hideLinkPreview();
                _lastPreviewUrl = null;
            }
        }, 600);
    });
}

async function fetchLinkPreview(url) {
    try {
        const resp = await fetch('/api/link_preview?url=' + encodeURIComponent(url));
        const data = await resp.json();
        if (data.title || data.description) showLinkPreview(data);
        else hideLinkPreview();
    } catch (e) { hideLinkPreview(); }
}

function showLinkPreview(data) {
    let el = document.getElementById('link-preview-bar');
    if (!el) {
        el = document.createElement('div');
        el.id = 'link-preview-bar';
        el.style.cssText = 'position:absolute;bottom:60px;left:0;right:0;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:10px;padding:10px 12px;display:flex;gap:10px;align-items:flex-start;z-index:50;margin:0 8px;';
        const form = document.getElementById('message-form');
        if (form) form.style.position = 'relative';
        const container = document.querySelector('.message-input-container');
        if (container) container.appendChild(el);
    }
    const img = data.image ? `<img src="${data.image}" style="width:56px;height:56px;object-fit:cover;border-radius:6px;flex-shrink:0;" onerror="this.style.display='none'">` : '';
    el.innerHTML = `
        ${img}
        <div style="flex:1;min-width:0;">
            <div style="font-size:12px;color:#667eea;margin-bottom:2px;">${escapeHtml(data.site_name || new URL(data.url).hostname)}</div>
            <div style="font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(data.title || '')}</div>
            ${data.description ? `<div style="font-size:12px;color:var(--text-secondary);overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">${escapeHtml(data.description)}</div>` : ''}
        </div>
        <button onclick="hideLinkPreview()" style="background:none;border:none;color:var(--text-secondary);cursor:pointer;font-size:16px;padding:0;">&times;</button>
    `;
    el.style.display = 'flex';
}

function hideLinkPreview() {
    const el = document.getElementById('link-preview-bar');
    if (el) el.style.display = 'none';
    _lastPreviewUrl = null;
}

// ═══════════════════════════════════════════════════════════════════════════════
// БЛОКИРОВКА ПОЛЬЗОВАТЕЛЕЙ
// ═══════════════════════════════════════════════════════════════════════════════

let _currentBlockTargetId = null;

async function blockUser(userId) {
    const resp = await fetch(`/user/${userId}/block`, { method: 'POST' });
    const data = await resp.json();
    if (data.success || data.already) {
        showToast('Пользователь заблокирован', 'success');
        return true;
    }
    return false;
}

async function unblockUser(userId) {
    await fetch(`/user/${userId}/unblock`, { method: 'POST' });
    showToast('Пользователь разблокирован', 'success');
}

async function checkBlockStatus(userId) {
    try {
        const resp = await fetch(`/user/${userId}/block_status`);
        return await resp.json();
    } catch (e) { return { i_blocked: false, they_blocked: false }; }
}

function unblockFromModal() {
    if (_currentBlockTargetId) {
        unblockUser(_currentBlockTargetId);
        document.getElementById('blocked-warning-modal').classList.remove('active');
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
// LAST SEEN — статус вместо Offline (formatLastSeen уже определена выше)
// ═══════════════════════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════════════════════
// СЕКРЕТНЫЕ ЧАТЫ
// ═══════════════════════════════════════════════════════════════════════════════

let _secretChatId = null;
let _secretChatOtherId = null;

async function openSecretChat(userId, userName) {
    _secretChatOtherId = userId;
    const resp = await fetch(`/secret/start/${userId}`, { method: 'POST' });
    const data = await resp.json();
    if (!data.success) { showToast(data.error || 'Ошибка', 'error'); return; }
    _secretChatId = data.chat_id;
    document.getElementById('secret-chat-modal').classList.add('active');
    loadSecretMessages();
}

async function loadSecretMessages() {
    if (!_secretChatId) return;
    const resp = await fetch(`/secret/${_secretChatId}/messages`);
    const data = await resp.json();
    const container = document.getElementById('secret-messages-container');
    if (!data.messages || !data.messages.length) {
        container.innerHTML = '<div style="text-align:center;padding:30px;color:#718096;"><i class="fas fa-lock" style="font-size:32px;margin-bottom:10px;display:block;color:#667eea;"></i>Сообщения зашифрованы</div>';
        return;
    }
    const myId = parseInt(document.body.dataset.userId);
    container.innerHTML = data.messages.map(m => {
        const isMine = m.sender_id === myId;
        return `<div style="display:flex;justify-content:${isMine ? 'flex-end' : 'flex-start'};margin-bottom:8px;">
            <div style="max-width:75%;background:${isMine ? '#667eea' : '#1a1a2e'};color:white;border-radius:12px;padding:8px 12px;font-size:14px;">
                <div>${escapeHtml(m.content)}</div>
                <div style="font-size:10px;color:rgba(255,255,255,0.6);margin-top:4px;text-align:right;">
                    ${m.timestamp}${m.self_destruct ? ` · 🔥${m.self_destruct}с` : ''}
                </div>
            </div>
        </div>`;
    }).join('');
    container.scrollTop = container.scrollHeight;
}

async function sendSecretMessage() {
    if (!_secretChatId) return;
    const inp = document.getElementById('secret-msg-input');
    const content = inp.value.trim();
    if (!content) return;
    const selfDestruct = parseInt(document.getElementById('secret-destruct').value) || 0;
    inp.value = '';
    const resp = await fetch(`/secret/${_secretChatId}/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, self_destruct: selfDestruct })
    });
    const data = await resp.json();
    if (data.success) loadSecretMessages();
}

async function closeAndDeleteSecretChat() {
    if (!_secretChatId) return;
    if (!confirm('Удалить все сообщения секретного чата?')) return;
    await fetch(`/secret/${_secretChatId}/close`, { method: 'POST' });
    _secretChatId = null;
    document.getElementById('secret-chat-modal').classList.remove('active');
    showToast('Секретный чат удалён', 'success');
}

function closeSecretChat() {
    document.getElementById('secret-chat-modal').classList.remove('active');
}

// Socket: входящий секретный чат
function setupSecretChatSocketHandlers() {
    socket.on('secret_chat_invite', data => {
        showToast(`🔒 ${data.from_user.username} приглашает в секретный чат`, 'info');
    });
    socket.on('secret_message', data => {
        if (_secretChatId === data.chat_id) loadSecretMessages();
        else showToast(`🔒 Новое секретное сообщение от ${data.sender_name}`, 'info');
    });
    socket.on('secret_chat_closed', data => {
        if (_secretChatId === data.chat_id) {
            document.getElementById('secret-chat-modal').classList.remove('active');
            showToast('Секретный чат был закрыт', 'info');
            _secretChatId = null;
        }
    });

    socket.on('chat_history_cleared', data => {
        // Собеседник удалил историю у обоих
        if (currentChatUserId && currentChatUserId == data.by_user_id) {
            const container = document.getElementById('messages-container');
            if (container) container.innerHTML = '';
        }
    });
}

// ═══════════════════════════════════════════════════════════════════════════════
// МАГАЗИН СТИКЕРОВ
// ═══════════════════════════════════════════════════════════════════════════════

async function showStickerShop() {
    document.getElementById('sticker-shop-modal').classList.add('active');
    const body = document.getElementById('sticker-shop-body');
    body.innerHTML = '<div style="text-align:center;padding:20px;"><i class="fas fa-spinner fa-spin"></i></div>';
    const resp = await fetch('/stickers/shop');
    const data = await resp.json();
    if (!data.packs || !data.packs.length) {
        body.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-secondary);">Паков стикеров пока нет</div>';
        return;
    }
    body.innerHTML = data.packs.map(p => `
        <div style="border:1px solid var(--border-color);border-radius:12px;padding:14px;margin-bottom:10px;">
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
                <div style="font-weight:600;flex:1;">${escapeHtml(p.name)}</div>
                <div style="font-size:12px;color:var(--text-secondary);">${p.sticker_count} стикеров</div>
                <button onclick="toggleStickerPackAdd(${p.id}, this, ${p.added})"
                    style="padding:6px 14px;border-radius:8px;border:none;cursor:pointer;font-size:13px;font-weight:600;background:${p.added ? '#e53e3e' : '#667eea'};color:white;">
                    ${p.added ? 'Удалить' : 'Добавить'}
                </button>
            </div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;">
                ${p.preview.map(url => `<img src="${url}" style="width:48px;height:48px;object-fit:contain;border-radius:6px;background:var(--bg-primary);">`).join('')}
            </div>
        </div>
    `).join('');
}

async function toggleStickerPackAdd(packId, btn, isAdded) {
    const url = isAdded ? `/stickers/pack/${packId}/remove` : `/stickers/pack/${packId}/add`;
    await fetch(url, { method: 'POST' });
    if (isAdded) {
        btn.textContent = 'Добавить'; btn.style.background = '#667eea';
        btn.onclick = () => toggleStickerPackAdd(packId, btn, false);
    } else {
        btn.textContent = 'Удалить'; btn.style.background = '#e53e3e';
        btn.onclick = () => toggleStickerPackAdd(packId, btn, true);
    }
    // Перезагружаем панель стикеров
    if (typeof loadUserStickers === 'function') loadUserStickers();
}

// ═══════════════════════════════════════════════════════════════════════════════
// МАГАЗИН ПОДАРКОВ
// ═══════════════════════════════════════════════════════════════════════════════

let _giftRecipientId = null;
let _giftRecipientName = null;

async function showGiftShop() {
    if (!currentChatUserId) { showToast('Сначала откройте чат', 'error'); return; }
    _giftRecipientId = currentChatUserId;
    _giftRecipientName = document.getElementById('chat-username')?.textContent || '';
    document.getElementById('gift-shop-modal').classList.add('active');
    // Recipient info
    const avatarEl = document.getElementById('gift-recipient-avatar');
    const nameEl = document.getElementById('gift-recipient-name');
    if (avatarEl) { avatarEl.style.background = '#667eea'; avatarEl.textContent = (_giftRecipientName[0] || '?').toUpperCase(); }
    if (nameEl) nameEl.textContent = _giftRecipientName;
    // Load gifts
    const body = document.getElementById('gift-shop-body');
    body.innerHTML = '<div style="text-align:center;padding:20px;grid-column:1/-1;"><i class="fas fa-spinner fa-spin"></i></div>';
    const resp = await fetch('/gifts/shop');
    const data = await resp.json();
    document.getElementById('gift-balance').textContent = data.balance || 0;
    const rarityColors = { common: '#718096', rare: '#3182ce', epic: '#805ad5', legendary: '#d69e2e' };
    body.innerHTML = (data.gifts || []).map(g => `
        <div style="border:1px solid var(--border-color);border-radius:12px;padding:14px;text-align:center;cursor:pointer;transition:transform 0.15s;"
             onmouseover="this.style.transform='scale(1.03)'" onmouseout="this.style.transform=''"
             onclick="sendGift(${g.id}, '${escapeHtml(g.name)}', '${g.emoji}', ${g.price})">
            <div style="font-size:40px;margin-bottom:6px;">${g.emoji}</div>
            <div style="font-weight:600;font-size:13px;margin-bottom:4px;">${escapeHtml(g.name)}</div>
            <div style="font-size:11px;color:${rarityColors[g.rarity] || '#718096'};margin-bottom:6px;font-weight:600;">${g.rarity}</div>
            <div style="font-size:13px;color:#667eea;font-weight:700;">${g.price} ✨</div>
        </div>
    `).join('');
}

async function sendGift(giftTypeId, name, emoji, price) {
    if (!confirm(`Отправить подарок "${name}" за ${price} ✨?`)) return;
    const resp = await fetch('/gifts/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ gift_type_id: giftTypeId, recipient_id: _giftRecipientId })
    });
    const data = await resp.json();
    if (data.success) {
        document.getElementById('gift-shop-modal').classList.remove('active');
        document.getElementById('gift-balance').textContent = data.new_balance;
        showToast(`Подарок ${emoji} отправлен!`, 'success');
    } else {
        showToast(data.error || 'Ошибка', 'error');
    }
}

async function showGiftInfo(giftTypeId, userGiftId) {
    const resp = await fetch(`/gifts/info/${giftTypeId}`);
    const g = await resp.json();
    const rarityColors = { common: '#718096', rare: '#3182ce', epic: '#805ad5', legendary: '#d69e2e' };
    const rarityLabels = { common: 'Обычный', rare: 'Редкий', epic: 'Эпический', legendary: 'Легендарный' };
    document.getElementById('gift-view-emoji').textContent = g.emoji;
    document.getElementById('gift-view-name').textContent = g.name;
    document.getElementById('gift-view-rarity').innerHTML = `<span style="color:${rarityColors[g.rarity]};font-weight:700;">${rarityLabels[g.rarity] || g.rarity}</span>`;
    document.getElementById('gift-view-desc').textContent = g.description || '';
    document.getElementById('gift-view-price').textContent = `${g.price} ✨ искр`;
    document.getElementById('gift-view-total').textContent = `Отправлено всего: ${g.total_sent} раз`;
    const addBtn = document.getElementById('gift-view-add-btn');
    if (userGiftId) {
        addBtn.innerHTML = `<button class="btn btn-primary" onclick="toggleGiftDisplay(${userGiftId}, this)">Добавить в профиль</button>`;
    } else { addBtn.innerHTML = ''; }
    document.getElementById('gift-view-modal').classList.add('active');
}

async function toggleGiftDisplay(giftId, btn) {
    const resp = await fetch(`/gifts/${giftId}/display`, { method: 'POST' });
    const data = await resp.json();
    if (data.success) {
        btn.textContent = data.displayed ? 'Убрать из профиля' : 'Добавить в профиль';
        showToast(data.displayed ? 'Добавлено в профиль' : 'Убрано из профиля', 'success');
    }
}

// Рендер подарка в сообщении
function renderGiftMessage(msg) {
    const g = msg.gift;
    if (!g) return '';
    const rarityColors = { common: '#718096', rare: '#3182ce', epic: '#805ad5', legendary: '#d69e2e' };
    const rarityLabels = { common: 'Обычный', rare: 'Редкий', epic: 'Эпический', legendary: 'Легендарный' };
    // Кнопка "Добавить в профиль" только у получателя
    const addBtn = (!msg.is_mine && g.user_gift_id)
        ? `<button onclick="showGiftInfo(${g.id}, ${g.user_gift_id})"
                style="padding:7px 18px;background:#667eea;color:white;border:none;border-radius:8px;font-size:13px;cursor:pointer;font-weight:600;">
                Просмотреть / Добавить в профиль
            </button>`
        : `<button onclick="showGiftInfo(${g.id}, null)"
                style="padding:7px 18px;background:#667eea;color:white;border:none;border-radius:8px;font-size:13px;cursor:pointer;font-weight:600;">
                Просмотреть
            </button>`;
    return `
        <div style="text-align:center;padding:16px 20px;background:linear-gradient(135deg,rgba(102,126,234,0.1),rgba(118,75,162,0.1));border-radius:16px;border:1px solid rgba(102,126,234,0.2);min-width:200px;">
            <div style="font-size:48px;margin-bottom:8px;">${g.emoji}</div>
            <div style="font-size:16px;font-weight:700;margin-bottom:4px;">🎁 Подарок</div>
            <div style="font-size:13px;color:var(--text-secondary);margin-bottom:4px;">${escapeHtml(g.name)}</div>
            <div style="font-size:12px;color:${rarityColors[g.rarity] || '#718096'};font-weight:600;margin-bottom:8px;">${rarityLabels[g.rarity] || g.rarity}</div>
            <div style="font-size:12px;color:#667eea;margin-bottom:10px;">Стоимость: ${g.price} ✨ искр</div>
            ${addBtn}
        </div>
    `;
}

// ═══════════════════════════════════════════════════════════════════════════════
// ИНИЦИАЛИЗАЦИЯ НОВЫХ ФИЧ
// ═══════════════════════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
    initLinkPreview();
});

// ═══════════════════════════════════════════════════════════════════════════════
// ЧЕРНОВИКИ СООБЩЕНИЙ
// ═══════════════════════════════════════════════════════════════════════════════

function _saveDraft() {
    const inp = document.getElementById('message-input');
    if (!inp) return;
    const val = inp.value;
    const key = currentGroupId ? 'group_' + currentGroupId : (currentChatUserId ? 'user_' + currentChatUserId : null);
    if (!key) return;
    if (val.trim()) {
        localStorage.setItem('draft_' + key, val);
    } else {
        localStorage.removeItem('draft_' + key);
    }
}

function _restoreDraft(key) {
    const inp = document.getElementById('message-input');
    if (!inp) return;
    const saved = localStorage.getItem('draft_' + key);
    inp.value = saved || '';
    inp.style.height = 'auto';
    inp.style.height = Math.min(inp.scrollHeight, 160) + 'px';
    if (typeof _updateBtns === 'function') _updateBtns();
    // Показываем индикатор черновика
    const indicator = document.getElementById('_draft-indicator');
    if (saved && saved.trim()) {
        if (!indicator) {
            const el = document.createElement('div');
            el.id = '_draft-indicator';
            el.style.cssText = 'font-size:11px;color:#e67e22;padding:2px 8px;';
            el.textContent = '✏️ Черновик';
            const form = document.getElementById('message-form');
            if (form) form.parentNode.insertBefore(el, form);
        }
    } else if (indicator) {
        indicator.remove();
    }
}

// Автосохранение черновика при вводе
document.addEventListener('DOMContentLoaded', () => {
    const inp = document.getElementById('message-input');
    if (inp) {
        inp.addEventListener('input', () => {
            const key = currentGroupId ? 'group_' + currentGroupId : (currentChatUserId ? 'user_' + currentChatUserId : null);
            if (!key) return;
            if (inp.value.trim()) {
                localStorage.setItem('draft_' + key, inp.value);
                const ind = document.getElementById('_draft-indicator');
                if (ind) ind.remove();
            } else {
                localStorage.removeItem('draft_' + key);
            }
        });
    }
});

// ═══════════════════════════════════════════════════════════════════════════════
// ПОИСК В ГРУППАХ
// ═══════════════════════════════════════════════════════════════════════════════

function showGroupSearch() {
    if (!currentGroupId) return;
    let panel = document.getElementById('_group-search-panel');
    if (panel) { panel.remove(); return; }
    panel = document.createElement('div');
    panel.id = '_group-search-panel';
    panel.style.cssText = 'position:absolute;top:60px;left:0;right:0;background:var(--bg-primary);border-bottom:1px solid var(--border-color);padding:10px 16px;z-index:100;display:flex;gap:8px;align-items:center;';
    panel.innerHTML = `
        <input id="_gsearch-input" type="text" placeholder="Поиск в группе..." style="flex:1;padding:8px 12px;border:1.5px solid var(--border-color);border-radius:20px;font-size:14px;background:var(--bg-secondary);color:var(--text-primary);">
        <button onclick="document.getElementById('_group-search-panel').remove()" style="background:none;border:none;color:var(--text-secondary);font-size:18px;cursor:pointer;padding:4px;">✕</button>
    `;
    const chatArea = document.getElementById('chat-area') || document.querySelector('.chat-area');
    if (chatArea) { chatArea.style.position = 'relative'; chatArea.appendChild(panel); }
    const inp = document.getElementById('_gsearch-input');
    if (inp) {
        inp.focus();
        let _st = null;
        inp.addEventListener('input', () => {
            clearTimeout(_st);
            _st = setTimeout(() => _doGroupSearch(inp.value), 400);
        });
    }
}

async function _doGroupSearch(q) {
    if (!q || q.length < 2) { _clearGroupSearchResults(); return; }
    const r = await fetch(`/search/messages?group_id=${currentGroupId}&q=${encodeURIComponent(q)}`);
    const d = await r.json();
    _showGroupSearchResults(d.results || []);
}

function _showGroupSearchResults(results) {
    let box = document.getElementById('_gsearch-results');
    if (!box) {
        box = document.createElement('div');
        box.id = '_gsearch-results';
        box.style.cssText = 'position:absolute;top:110px;left:0;right:0;background:var(--bg-primary);border-bottom:1px solid var(--border-color);max-height:240px;overflow-y:auto;z-index:99;';
        const chatArea = document.getElementById('chat-area') || document.querySelector('.chat-area');
        if (chatArea) chatArea.appendChild(box);
    }
    if (!results.length) { box.innerHTML = '<div style="padding:12px 16px;color:var(--text-secondary);font-size:13px;">Ничего не найдено</div>'; return; }
    box.innerHTML = results.map(r => `
        <div onclick="scrollToMsg(${r.id})" style="padding:10px 16px;cursor:pointer;border-bottom:1px solid var(--border-color);hover:background:var(--bg-secondary);">
            <div style="font-size:12px;color:var(--text-secondary);">${escapeHtml(r.sender)} · ${r.timestamp}</div>
            <div style="font-size:14px;color:var(--text-primary);margin-top:2px;">${escapeHtml((r.content||'').slice(0,100))}</div>
        </div>
    `).join('');
}

function _clearGroupSearchResults() {
    const box = document.getElementById('_gsearch-results');
    if (box) box.innerHTML = '';
}

// ═══════════════════════════════════════════════════════════════════════════════
// ПРОЧИТАННОСТЬ В ГРУППАХ
// ═══════════════════════════════════════════════════════════════════════════════

// Отмечаем последнее видимое сообщение как прочитанное
function _markVisibleGroupMessages() {
    if (!currentGroupId) return;
    const container = document.getElementById('messages-container');
    if (!container) return;
    const msgs = container.querySelectorAll('[data-message-id][data-is-group="1"]');
    if (!msgs.length) return;
    const lastMsg = msgs[msgs.length - 1];
    const msgId = lastMsg.dataset.messageId;
    if (!msgId) return;
    fetch(`/groups/${currentGroupId}/mark_read_msg/${msgId}`, { method: 'POST' }).catch(() => {});
}

// Показать кто прочитал сообщение
async function showMessageReaders(msgId, groupId) {
    const r = await fetch(`/groups/${groupId}/messages/${msgId}/readers`);
    const d = await r.json();
    const readers = d.readers || [];
    const modal = document.createElement('div');
    modal.className = 'modal active';
    modal.innerHTML = `
        <div class="modal-content" style="max-width:360px;">
            <div class="modal-header">
                <h3>Прочитали (${readers.length})</h3>
                <button class="modal-close" onclick="this.closest('.modal').remove()">✕</button>
            </div>
            <div class="modal-body" style="max-height:300px;overflow-y:auto;">
                ${readers.length ? readers.map(u => `
                    <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border-color);">
                        <div style="width:36px;height:36px;border-radius:50%;background:${u.avatar_color};display:flex;align-items:center;justify-content:center;color:#fff;font-weight:600;font-size:14px;flex-shrink:0;">
                            ${u.avatar_url ? `<img src="${u.avatar_url}" style="width:36px;height:36px;border-radius:50%;object-fit:cover;">` : escapeHtml(u.avatar_letter)}
                        </div>
                        <span style="font-size:14px;">${escapeHtml(u.name)}</span>
                    </div>
                `).join('') : '<div style="color:var(--text-secondary);font-size:14px;">Никто ещё не прочитал</div>'}
            </div>
        </div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
}

// Обновляем счётчик прочитавших при получении события
if (typeof socket !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => {
        if (window.socket) {
            window.socket.on('group_message_read', data => {
                const el = document.querySelector(`[data-message-id="${data.message_id}"] .msg-read-count`);
                if (el) el.textContent = data.count;
            });
        }
    });
}

// ═══════════════════════════════════════════════════════════════════════════════
// РАСПИСАНИЕ СООБЩЕНИЙ
// ═══════════════════════════════════════════════════════════════════════════════

function showScheduleModal() {
    if (!currentChatUserId && !currentGroupId) { showError('Сначала откройте чат'); return; }
    const modal = document.createElement('div');
    modal.className = 'modal active';
    const inp = document.getElementById('message-input');
    const text = inp ? inp.value.trim() : '';
    // Минимальное время — через 1 минуту
    const minDt = new Date(Date.now() + 60000);
    const minStr = minDt.toISOString().slice(0, 16);
    modal.innerHTML = `
        <div class="modal-content" style="max-width:400px;">
            <div class="modal-header">
                <h3>⏰ Запланировать отправку</h3>
                <button class="modal-close" onclick="this.closest('.modal').remove()">✕</button>
            </div>
            <div class="modal-body" style="display:flex;flex-direction:column;gap:12px;">
                <textarea id="_sched-text" rows="3" style="padding:10px;border:1.5px solid var(--border-color);border-radius:10px;font-size:14px;resize:vertical;background:var(--bg-secondary);color:var(--text-primary);" placeholder="Текст сообщения...">${escapeHtml(text)}</textarea>
                <label style="font-size:13px;color:var(--text-secondary);">Дата и время отправки</label>
                <input type="datetime-local" id="_sched-dt" min="${minStr}" style="padding:10px;border:1.5px solid var(--border-color);border-radius:10px;font-size:14px;background:var(--bg-secondary);color:var(--text-primary);">
                <div id="_sched-list" style="margin-top:4px;"></div>
                <div style="display:flex;gap:8px;">
                    <button class="btn btn-primary" onclick="_submitSchedule()" style="flex:1;">Запланировать</button>
                    <button class="btn btn-secondary" onclick="_loadScheduledList()" style="flex:1;">Мои запланированные</button>
                </div>
            </div>
        </div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
}

async function _submitSchedule() {
    const text = document.getElementById('_sched-text')?.value.trim();
    const dt = document.getElementById('_sched-dt')?.value;
    if (!text) { showError('Введите текст'); return; }
    if (!dt) { showError('Выберите время'); return; }
    const body = { content: text, send_at: new Date(dt).toISOString() };
    if (currentChatUserId) body.receiver_id = currentChatUserId;
    else if (currentGroupId) body.group_id = currentGroupId;
    const r = await fetch('/message/schedule', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
    const d = await r.json();
    if (d.success) {
        showToast('Сообщение запланировано', 'success');
        document.querySelector('.modal.active')?.remove();
        const inp = document.getElementById('message-input');
        if (inp) { inp.value = ''; inp.style.height = 'auto'; }
    } else {
        showError(d.error || 'Ошибка');
    }
}

async function _loadScheduledList() {
    const r = await fetch('/message/schedule/list');
    const d = await r.json();
    const box = document.getElementById('_sched-list');
    if (!box) return;
    const items = d.scheduled || [];
    if (!items.length) { box.innerHTML = '<div style="color:var(--text-secondary);font-size:13px;">Нет запланированных</div>'; return; }
    box.innerHTML = '<div style="font-size:13px;font-weight:600;margin-bottom:6px;">Запланированные:</div>' + items.map(m => `
        <div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border-color);">
            <div style="flex:1;font-size:13px;">${escapeHtml((m.content||'').slice(0,60))} <span style="color:var(--text-secondary);">· ${new Date(m.send_at).toLocaleString('ru-RU')}</span></div>
            <button onclick="_cancelScheduled(${m.id},this)" style="background:#e53e3e;color:#fff;border:none;border-radius:6px;padding:3px 8px;font-size:12px;cursor:pointer;">Отмена</button>
        </div>
    `).join('');
}

async function _cancelScheduled(id, btn) {
    const r = await fetch(`/message/schedule/${id}/cancel`, { method: 'POST' });
    const d = await r.json();
    if (d.success) { btn.closest('div[style]').remove(); showToast('Отменено', 'info'); }
}

// ═══════════════════════════════════════════════════════════════════════════════
// АВТООТВЕТ
// ═══════════════════════════════════════════════════════════════════════════════

async function showAutoReplySettings() {
    const r = await fetch('/user/auto-reply');
    const d = await r.json();
    const modal = document.createElement('div');
    modal.className = 'modal active';
    modal.innerHTML = `
        <div class="modal-content" style="max-width:400px;">
            <div class="modal-header">
                <h3>🤖 Автоответ</h3>
                <button class="modal-close" onclick="this.closest('.modal').remove()">✕</button>
            </div>
            <div class="modal-body" style="display:flex;flex-direction:column;gap:12px;">
                <p style="font-size:13px;color:var(--text-secondary);">Когда вам пишут, автоматически отправляется это сообщение (не чаще раза в 5 минут).</p>
                <textarea id="_autoreply-text" rows="3" maxlength="200" style="padding:10px;border:1.5px solid var(--border-color);border-radius:10px;font-size:14px;resize:vertical;background:var(--bg-secondary);color:var(--text-primary);" placeholder="Например: Сейчас недоступен, отвечу позже...">${escapeHtml(d.text || '')}</textarea>
                <div style="display:flex;gap:8px;">
                    <button class="btn btn-primary" onclick="_saveAutoReply()" style="flex:1;">Сохранить</button>
                    <button class="btn btn-secondary" onclick="_clearAutoReply()" style="flex:1;">Отключить</button>
                </div>
            </div>
        </div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
}

async function _saveAutoReply() {
    const text = document.getElementById('_autoreply-text')?.value.trim();
    const r = await fetch('/user/auto-reply', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({text}) });
    const d = await r.json();
    if (d.success) { showToast(d.text ? 'Автоответ включён' : 'Автоответ отключён', 'success'); document.querySelector('.modal.active')?.remove(); }
}

async function _clearAutoReply() {
    await fetch('/user/auto-reply', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({text: ''}) });
    showToast('Автоответ отключён', 'info');
    document.querySelector('.modal.active')?.remove();
}

// ═══════════════════════════════════════════════════════════════════════════════
// ПЕРЕВОД СООБЩЕНИЙ
// ═══════════════════════════════════════════════════════════════════════════════

async function translateMessage(msgId, isGroup) {
    const msgEl = document.querySelector(`[data-message-id="${msgId}"]`);
    if (!msgEl) return;
    const textEl = msgEl.querySelector('.message-content, .message-text');
    if (!textEl) return;
    const text = textEl.textContent.trim();
    if (!text) return;

    // Показываем индикатор загрузки
    let transEl = msgEl.querySelector('._translation');
    if (transEl) { transEl.remove(); return; } // toggle off
    transEl = document.createElement('div');
    transEl.className = '_translation';
    transEl.style.cssText = 'margin-top:6px;padding:6px 10px;background:var(--bg-tertiary);border-radius:8px;font-size:13px;color:var(--text-secondary);border-left:3px solid var(--primary);';
    transEl.textContent = '⏳ Перевод...';
    msgEl.appendChild(transEl);

    try {
        const r = await fetch('/translate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text, lang: 'ru'})
        });
        const d = await r.json();
        if (d.translated) {
            transEl.innerHTML = `<span style="font-size:11px;color:var(--text-muted);">🌐 Перевод</span><br>${escapeHtml(d.translated)}`;
        } else {
            transEl.textContent = '❌ ' + (d.error || 'Ошибка перевода');
        }
    } catch(e) {
        transEl.textContent = '❌ Ошибка перевода';
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
// ЭКСПОРТ ЧАТА
// ═══════════════════════════════════════════════════════════════════════════════

function exportCurrentChat() {
    if (currentChatUserId) {
        window.location.href = `/chat/${currentChatUserId}/export`;
    } else if (currentGroupId) {
        window.location.href = `/groups/${currentGroupId}/export`;
    } else {
        showError('Сначала откройте чат');
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
// АНИМИРОВАННЫЕ СТИКЕРЫ (Lottie / WebP анимация)
// ═══════════════════════════════════════════════════════════════════════════════

function _isAnimatedSticker(url) {
    if (!url) return false;
    const u = url.toLowerCase();
    return u.endsWith('.json') || u.endsWith('.tgs') || u.includes('lottie');
}

function _renderSticker(container, url) {
    if (_isAnimatedSticker(url)) {
        // Lottie анимация
        if (window.lottie) {
            container.innerHTML = '';
            window.lottie.loadAnimation({
                container,
                renderer: 'svg',
                loop: true,
                autoplay: true,
                path: url
            });
        } else {
            // Fallback — загружаем Lottie динамически
            const script = document.createElement('script');
            script.src = 'https://cdnjs.cloudflare.com/ajax/libs/bodymovin/5.12.2/lottie.min.js';
            script.onload = () => _renderSticker(container, url);
            document.head.appendChild(script);
        }
    } else {
        // Обычный стикер (PNG/WebP/GIF)
        container.innerHTML = `<img src="${url}" style="width:120px;height:120px;object-fit:contain;">`;
    }
}

// Патчим рендер стикеров чтобы поддерживать анимированные
document.addEventListener('DOMContentLoaded', () => {
    const observer = new MutationObserver(mutations => {
        mutations.forEach(m => {
            m.addedNodes.forEach(node => {
                if (node.nodeType !== 1) return;
                node.querySelectorAll && node.querySelectorAll('.sticker-message img[data-src]').forEach(img => {
                    const url = decodeURIComponent(img.getAttribute('data-src') || '');
                    if (_isAnimatedSticker(url)) {
                        const container = img.parentElement;
                        _renderSticker(container, url);
                    }
                });
            });
        });
    });
    const mc = document.getElementById('messages-container');
    if (mc) observer.observe(mc, { childList: true, subtree: true });
});

// ═══════════════════════════════════════════════════════════════════════════════
// ГРУППОВЫЕ ЗВОНКИ
// ═══════════════════════════════════════════════════════════════════════════════

let _gcPeers = {}; // {userId: RTCPeerConnection}
let _gcLocalStream = null;
let _gcGroupId = null;
let _gcIsVideo = false;

async function startGroupCall(isVideo) {
    if (!currentGroupId) return;
    _gcGroupId = currentGroupId;
    _gcIsVideo = isVideo;

    try {
        _gcLocalStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: isVideo });
    } catch(e) {
        showError('Нет доступа к ' + (isVideo ? 'камере/микрофону' : 'микрофону'));
        return;
    }

    _showGroupCallModal();

    const r = await fetch(`/groups/${_gcGroupId}/call/start`, { method: 'POST' });
    const d = await r.json();
    if (!d.success) { showError(d.error || 'Ошибка'); return; }

    // Запрашиваем SID участников через socket
    socket.emit('group_call_request_peers', { group_id: _gcGroupId });
}
window.startGroupCall = startGroupCall;

async function leaveGroupCall() {
    if (_gcGroupId) {
        await fetch(`/groups/${_gcGroupId}/call/leave`, { method: 'POST' });
    }
    Object.values(_gcPeers).forEach(pc => pc.close());
    _gcPeers = {};
    if (_gcLocalStream) { _gcLocalStream.getTracks().forEach(t => t.stop()); _gcLocalStream = null; }
    document.getElementById('group-call-modal')?.remove();
    _gcGroupId = null;
}
window.leaveGroupCall = leaveGroupCall;

function _showGroupCallModal() {
    document.getElementById('group-call-modal')?.remove();
    const modal = document.createElement('div');
    modal.id = 'group-call-modal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:99999;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;';
    modal.innerHTML = `
        <div style="color:#fff;font-size:18px;font-weight:600;">Групповой звонок</div>
        <div id="gc-participants" style="display:flex;flex-wrap:wrap;gap:12px;justify-content:center;max-width:600px;"></div>
        <div id="gc-videos" style="display:flex;flex-wrap:wrap;gap:8px;justify-content:center;"></div>
        <div style="display:flex;gap:12px;margin-top:8px;">
            <button onclick="_gcToggleMute(this)" style="width:52px;height:52px;border-radius:50%;background:#4a5568;border:none;color:#fff;font-size:18px;cursor:pointer;" title="Микрофон">
                <i class="fas fa-microphone"></i>
            </button>
            ${_gcIsVideo ? `<button onclick="_gcToggleVideo(this)" style="width:52px;height:52px;border-radius:50%;background:#4a5568;border:none;color:#fff;font-size:18px;cursor:pointer;" title="Камера"><i class="fas fa-video"></i></button>` : ''}
            <button onclick="leaveGroupCall()" style="width:52px;height:52px;border-radius:50%;background:#e53e3e;border:none;color:#fff;font-size:18px;cursor:pointer;" title="Завершить">
                <i class="fas fa-phone-slash"></i>
            </button>
        </div>`;
    document.body.appendChild(modal);
    _gcUpdateParticipants();

    // Локальное видео
    if (_gcIsVideo && _gcLocalStream) {
        const v = document.createElement('video');
        v.srcObject = _gcLocalStream; v.autoplay = true; v.muted = true;
        v.style.cssText = 'width:160px;height:120px;border-radius:12px;object-fit:cover;border:2px solid #667eea;';
        document.getElementById('gc-videos')?.appendChild(v);
    }
}

function _gcUpdateParticipants() {
    const box = document.getElementById('gc-participants');
    if (!box) return;
    const peers = Object.keys(_gcPeers);
    box.innerHTML = peers.length
        ? peers.map(uid => `<div style="background:#2d3748;border-radius:12px;padding:10px 16px;color:#fff;font-size:13px;">👤 ${uid}</div>`).join('')
        : '<div style="color:#a0aec0;font-size:14px;">Ожидание участников...</div>';
}

function _gcToggleMute(btn) {
    if (!_gcLocalStream) return;
    const track = _gcLocalStream.getAudioTracks()[0];
    if (!track) return;
    track.enabled = !track.enabled;
    btn.style.background = track.enabled ? '#4a5568' : '#e53e3e';
    btn.querySelector('i').className = track.enabled ? 'fas fa-microphone' : 'fas fa-microphone-slash';
}

function _gcToggleVideo(btn) {
    if (!_gcLocalStream) return;
    const track = _gcLocalStream.getVideoTracks()[0];
    if (!track) return;
    track.enabled = !track.enabled;
    btn.style.background = track.enabled ? '#4a5568' : '#e53e3e';
    btn.querySelector('i').className = track.enabled ? 'fas fa-video' : 'fas fa-video-slash';
}

async function _gcConnectToPeer(userId) {
    if (_gcPeers[userId]) return;
    const pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });
    _gcPeers[userId] = pc;

    _gcLocalStream?.getTracks().forEach(t => pc.addTrack(t, _gcLocalStream));

    pc.ontrack = (e) => {
        if (!_gcIsVideo) return;
        const v = document.createElement('video');
        v.srcObject = e.streams[0]; v.autoplay = true;
        v.style.cssText = 'width:160px;height:120px;border-radius:12px;object-fit:cover;border:2px solid #48bb78;';
        document.getElementById('gc-videos')?.appendChild(v);
    };

    pc.onicecandidate = (e) => {
        if (e.candidate) {
            socket.emit('group_call_signal', { target_sid: userId, signal: e.candidate, signal_type: 'ice' });
        }
    };

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    socket.emit('group_call_signal', { target_sid: userId, signal: offer, signal_type: 'offer' });
    _gcUpdateParticipants();
}

// Socket.IO обработчики групповых звонков
document.addEventListener('DOMContentLoaded', () => {
    if (!window.socket) return;

    socket.on('group_call_user_joined', (data) => {
        if (data.group_id !== _gcGroupId) {
            // Показываем уведомление если мы в этой группе
            if (currentGroupId === data.group_id) {
                showToast(`📞 ${data.name} начал групповой звонок`, 'info');
                _showGroupCallBanner(data.group_id, data.name);
            }
            return;
        }
        _gcConnectToPeer(data.user_id);
    });

    socket.on('group_call_user_left', (data) => {
        if (data.group_id !== _gcGroupId) return;
        const pc = _gcPeers[data.user_id];
        if (pc) { pc.close(); delete _gcPeers[data.user_id]; }
        _gcUpdateParticipants();
    });

    socket.on('group_call_signal', async (data) => {
        const { from_user_id, signal, signal_type } = data;
        if (!_gcGroupId) return;
        let pc = _gcPeers[from_user_id];
        if (!pc) {
            pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });
            _gcPeers[from_user_id] = pc;
            _gcLocalStream?.getTracks().forEach(t => pc.addTrack(t, _gcLocalStream));
            pc.ontrack = (e) => {
                if (!_gcIsVideo) return;
                const v = document.createElement('video');
                v.srcObject = e.streams[0]; v.autoplay = true;
                v.style.cssText = 'width:160px;height:120px;border-radius:12px;object-fit:cover;border:2px solid #48bb78;';
                document.getElementById('gc-videos')?.appendChild(v);
            };
            pc.onicecandidate = (e) => {
                if (e.candidate) socket.emit('group_call_signal', { target_sid: from_user_id, signal: e.candidate, signal_type: 'ice' });
            };
            _gcUpdateParticipants();
        }
        if (signal_type === 'offer') {
            await pc.setRemoteDescription(new RTCSessionDescription(signal));
            const answer = await pc.createAnswer();
            await pc.setLocalDescription(answer);
            socket.emit('group_call_signal', { target_sid: from_user_id, signal: answer, signal_type: 'answer' });
        } else if (signal_type === 'answer') {
            await pc.setRemoteDescription(new RTCSessionDescription(signal));
        } else if (signal_type === 'ice') {
            try { await pc.addIceCandidate(new RTCIceCandidate(signal)); } catch(e) {}
        }
    });

    socket.on('group_call_peers_list', (data) => {
        (data.participants || []).forEach(uid => _gcConnectToPeer(uid));
    });
});

function _showGroupCallBanner(groupId, initiatorName) {
    document.getElementById('_gc-banner')?.remove();
    const banner = document.createElement('div');
    banner.id = '_gc-banner';
    banner.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);background:#2d3748;color:#fff;border-radius:14px;padding:12px 20px;display:flex;align-items:center;gap:12px;z-index:99998;box-shadow:0 4px 20px rgba(0,0,0,0.4);font-size:14px;';
    banner.innerHTML = `
        <i class="fas fa-phone" style="color:#48bb78;"></i>
        <span>Групповой звонок от ${escapeHtml(initiatorName)}</span>
        <button onclick="startGroupCall(false);document.getElementById('_gc-banner')?.remove();" style="background:#48bb78;border:none;color:#fff;border-radius:8px;padding:6px 14px;font-size:13px;cursor:pointer;font-weight:600;">Войти</button>
        <button onclick="this.closest('#_gc-banner').remove();" style="background:#e53e3e;border:none;color:#fff;border-radius:8px;padding:6px 10px;font-size:13px;cursor:pointer;">✕</button>`;
    document.body.appendChild(banner);
    setTimeout(() => banner.remove(), 30000);
}

// ═══════════════════════════════════════════════════════════════════════════════
// LIVE-ТРАНСЛЯЦИИ В КАНАЛАХ
// ═══════════════════════════════════════════════════════════════════════════════

let _streamLocalStream = null;
let _streamPeers = {}; // {viewer_sid: RTCPeerConnection}
let _streamGroupId = null;
let _streamIsBroadcaster = false;
let _streamViewerPc = null;

// ── Broadcaster ───────────────────────────────────────────────────────────────

async function showStartStreamModal() {
    if (!currentGroupId || !_currentGroupData?.is_channel || !_currentGroupData?.is_admin) {
        showError('Трансляции доступны только администраторам каналов');
        return;
    }
    // Проверяем нет ли уже активной трансляции
    const r = await fetch(`/groups/${currentGroupId}/stream/status`);
    const d = await r.json();
    if (d.active) {
        _joinStreamAsViewer(currentGroupId, d.stream.title);
        return;
    }
    const modal = document.createElement('div');
    modal.className = 'modal active';
    modal.innerHTML = `
        <div class="modal-content" style="max-width:400px;">
            <div class="modal-header">
                <h3>🔴 Начать трансляцию</h3>
                <button class="modal-close" onclick="this.closest('.modal').remove()">✕</button>
            </div>
            <div class="modal-body" style="display:flex;flex-direction:column;gap:12px;">
                <input id="_stream-title" type="text" maxlength="100" placeholder="Название трансляции..."
                    style="padding:10px;border:1.5px solid var(--border-color);border-radius:10px;font-size:14px;background:var(--bg-secondary);color:var(--text-primary);">
                <div id="_stream-preview" style="width:100%;aspect-ratio:16/9;background:#000;border-radius:10px;overflow:hidden;display:flex;align-items:center;justify-content:center;color:#666;">
                    <span>Предпросмотр камеры...</span>
                </div>
                <button class="btn btn-primary" onclick="_startBroadcast()">🔴 Начать эфир</button>
            </div>
        </div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    // Предпросмотр камеры
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
        _streamLocalStream = stream;
        const v = document.createElement('video');
        v.srcObject = stream; v.autoplay = true; v.muted = true;
        v.style.cssText = 'width:100%;height:100%;object-fit:cover;';
        document.getElementById('_stream-preview').innerHTML = '';
        document.getElementById('_stream-preview').appendChild(v);
    } catch(e) { document.getElementById('_stream-preview').innerHTML = '<span style="color:#e53e3e;">Нет доступа к камере</span>'; }
}
window.showStartStreamModal = showStartStreamModal;

async function _startBroadcast() {
    const title = document.getElementById('_stream-title')?.value.trim() || 'Прямой эфир';
    if (!_streamLocalStream) { showError('Нет доступа к камере'); return; }
    document.querySelector('.modal.active')?.remove();

    const r = await fetch(`/groups/${currentGroupId}/stream/start`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ title })
    });
    const d = await r.json();
    if (!d.success) { showError(d.error || 'Ошибка'); return; }

    _streamGroupId = currentGroupId;
    _streamIsBroadcaster = true;
    _showBroadcastUI(title);
}

function _showBroadcastUI(title) {
    document.getElementById('_stream-modal')?.remove();
    const modal = document.createElement('div');
    modal.id = '_stream-modal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.92);z-index:99999;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;';
    modal.innerHTML = `
        <div style="color:#e53e3e;font-size:14px;font-weight:700;letter-spacing:1px;">🔴 В ЭФИРЕ — ${escapeHtml(title)}</div>
        <video id="_stream-local-video" autoplay muted style="width:min(640px,90vw);aspect-ratio:16/9;background:#000;border-radius:12px;object-fit:cover;"></video>
        <div id="_stream-viewers" style="color:#a0aec0;font-size:13px;">Зрителей: 0</div>
        <div style="display:flex;gap:10px;">
            <button onclick="_streamToggleMute(this)" style="width:48px;height:48px;border-radius:50%;background:#4a5568;border:none;color:#fff;font-size:16px;cursor:pointer;"><i class="fas fa-microphone"></i></button>
            <button onclick="_streamToggleVideo(this)" style="width:48px;height:48px;border-radius:50%;background:#4a5568;border:none;color:#fff;font-size:16px;cursor:pointer;"><i class="fas fa-video"></i></button>
            <button onclick="_stopBroadcast()" style="width:48px;height:48px;border-radius:50%;background:#e53e3e;border:none;color:#fff;font-size:16px;cursor:pointer;"><i class="fas fa-stop"></i></button>
        </div>`;
    document.body.appendChild(modal);
    const v = document.getElementById('_stream-local-video');
    if (v && _streamLocalStream) v.srcObject = _streamLocalStream;
}

async function _stopBroadcast() {
    if (_streamGroupId) await fetch(`/groups/${_streamGroupId}/stream/stop`, { method: 'POST' });
    Object.values(_streamPeers).forEach(pc => pc.close());
    _streamPeers = {};
    if (_streamLocalStream) { _streamLocalStream.getTracks().forEach(t => t.stop()); _streamLocalStream = null; }
    document.getElementById('_stream-modal')?.remove();
    _streamGroupId = null; _streamIsBroadcaster = false;
}
window._stopBroadcast = _stopBroadcast;

function _streamToggleMute(btn) {
    const t = _streamLocalStream?.getAudioTracks()[0];
    if (!t) return;
    t.enabled = !t.enabled;
    btn.style.background = t.enabled ? '#4a5568' : '#e53e3e';
    btn.querySelector('i').className = t.enabled ? 'fas fa-microphone' : 'fas fa-microphone-slash';
}
function _streamToggleVideo(btn) {
    const t = _streamLocalStream?.getVideoTracks()[0];
    if (!t) return;
    t.enabled = !t.enabled;
    btn.style.background = t.enabled ? '#4a5568' : '#e53e3e';
    btn.querySelector('i').className = t.enabled ? 'fas fa-video' : 'fas fa-video-slash';
}

// ── Viewer ────────────────────────────────────────────────────────────────────

async function _joinStreamAsViewer(groupId, title) {
    _streamGroupId = groupId;
    _streamIsBroadcaster = false;
    socket.emit('stream_viewer_join', { group_id: groupId });

    document.getElementById('_stream-modal')?.remove();
    const modal = document.createElement('div');
    modal.id = '_stream-modal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.92);z-index:99999;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;';
    modal.innerHTML = `
        <div style="color:#fff;font-size:16px;font-weight:600;">📺 ${escapeHtml(title || 'Прямой эфир')}</div>
        <video id="_stream-remote-video" autoplay playsinline style="width:min(640px,90vw);aspect-ratio:16/9;background:#111;border-radius:12px;object-fit:cover;"></video>
        <div style="color:#a0aec0;font-size:13px;" id="_stream-status">Подключение...</div>
        <button onclick="_leaveStream()" style="padding:10px 24px;border-radius:10px;background:#e53e3e;border:none;color:#fff;font-size:14px;cursor:pointer;">Выйти</button>`;
    document.body.appendChild(modal);
}

function _leaveStream() {
    if (_streamGroupId) socket.emit('stream_viewer_leave', { group_id: _streamGroupId });
    if (_streamViewerPc) { _streamViewerPc.close(); _streamViewerPc = null; }
    document.getElementById('_stream-modal')?.remove();
    _streamGroupId = null;
}
window._leaveStream = _leaveStream;

// ── Socket.IO обработчики трансляций ─────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    if (!window.socket) return;

    // Broadcaster получает нового зрителя — создаёт offer
    socket.on('stream_new_viewer', async (data) => {
        if (!_streamIsBroadcaster || !_streamLocalStream) return;
        const { viewer_sid } = data;
        const pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });
        _streamPeers[viewer_sid] = pc;
        _streamLocalStream.getTracks().forEach(t => pc.addTrack(t, _streamLocalStream));
        pc.onicecandidate = e => {
            if (e.candidate) socket.emit('stream_signal', { target_sid: viewer_sid, signal: e.candidate, signal_type: 'ice' });
        };
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        socket.emit('stream_signal', { target_sid: viewer_sid, signal: offer, signal_type: 'offer' });
        // Обновляем счётчик
        const cnt = document.getElementById('_stream-viewers');
        if (cnt) cnt.textContent = `Зрителей: ${Object.keys(_streamPeers).length}`;
    });

    // Viewer получает offer от broadcaster
    socket.on('stream_signal', async (data) => {
        const { from_sid, signal, signal_type } = data;
        if (_streamIsBroadcaster) {
            // Broadcaster получает answer/ice от viewer
            const pc = _streamPeers[from_sid];
            if (!pc) return;
            if (signal_type === 'answer') await pc.setRemoteDescription(new RTCSessionDescription(signal));
            else if (signal_type === 'ice') { try { await pc.addIceCandidate(new RTCIceCandidate(signal)); } catch(e) {} }
        } else {
            // Viewer получает offer/ice от broadcaster
            if (signal_type === 'offer') {
                const pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });
                _streamViewerPc = pc;
                pc.ontrack = e => {
                    const v = document.getElementById('_stream-remote-video');
                    if (v) { v.srcObject = e.streams[0]; }
                    const st = document.getElementById('_stream-status');
                    if (st) st.textContent = '🔴 Прямой эфир';
                };
                pc.onicecandidate = e => {
                    if (e.candidate) socket.emit('stream_signal', { target_sid: from_sid, signal: e.candidate, signal_type: 'ice' });
                };
                await pc.setRemoteDescription(new RTCSessionDescription(signal));
                const answer = await pc.createAnswer();
                await pc.setLocalDescription(answer);
                socket.emit('stream_signal', { target_sid: from_sid, signal: answer, signal_type: 'answer' });
            } else if (signal_type === 'ice' && _streamViewerPc) {
                try { await _streamViewerPc.addIceCandidate(new RTCIceCandidate(signal)); } catch(e) {}
            }
        }
    });

    // Уведомление о начале трансляции
    socket.on('stream_started', (data) => {
        if (currentGroupId !== data.group_id) return;
        _showStreamBanner(data.group_id, data.title, data.broadcaster_name);
        // Показываем кнопку трансляции в шапке
        const btn = document.getElementById('_stream-live-btn');
        if (btn) { btn.style.display = 'flex'; btn.style.color = '#e53e3e'; }
    });

    socket.on('stream_stopped', (data) => {
        if (currentGroupId !== data.group_id) return;
        document.getElementById('_stream-banner')?.remove();
        if (!_streamIsBroadcaster) _leaveStream();
        const btn = document.getElementById('_stream-live-btn');
        if (btn) btn.style.display = 'none';
    });
});

function _showStreamBanner(groupId, title, broadcasterName) {
    document.getElementById('_stream-banner')?.remove();
    const banner = document.createElement('div');
    banner.id = '_stream-banner';
    banner.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);background:#1a202c;color:#fff;border-radius:14px;padding:12px 20px;display:flex;align-items:center;gap:12px;z-index:99998;box-shadow:0 4px 20px rgba(0,0,0,0.5);font-size:14px;border:1px solid #e53e3e;';
    banner.innerHTML = `
        <span style="color:#e53e3e;font-weight:700;font-size:12px;letter-spacing:1px;">🔴 LIVE</span>
        <span>${escapeHtml(broadcasterName)} — ${escapeHtml(title)}</span>
        <button onclick="_joinStreamAsViewer(${groupId},'${escapeHtml(title)}');document.getElementById('_stream-banner')?.remove();"
            style="background:#e53e3e;border:none;color:#fff;border-radius:8px;padding:6px 14px;font-size:13px;cursor:pointer;font-weight:600;">Смотреть</button>
        <button onclick="this.closest('#_stream-banner').remove();" style="background:none;border:none;color:#a0aec0;font-size:16px;cursor:pointer;">✕</button>`;
    document.body.appendChild(banner);
}
