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
        messageInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                messageForm.dispatchEvent(new Event('submit'));
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
    // В канале удалять могут только админы; в группе — только свои
    const canDelete = isFav
        ? false
        : (isChannel ? (isMine && _currentGroupData && _currentGroupData.is_admin) : (isMine && !isDeleted));
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
    // Просто экранируем текст — кнопки теперь в msg.bot_buttons
    return escapeHtml(text || '').replace(/\n/g, '<br>');
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

async function _doSendText(content) {
    if (!content || (!currentChatUserId && !currentGroupId)) return;
    const input = document.getElementById('message-input');
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
                <div class="chat-item user-chat-item" data-user-id="${chat.id}" data-username="${escapeHtml(chat.display_name || chat.username)}" data-avatar-color="${chat.avatar_color}" data-avatar-letter="${chat.avatar_letter}">
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
    
    // Создаем новый обработчик
    const clickHandler = function(e) {
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
        if (user.is_online) {
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
    currentChatUserId = userId;
    _favoritesOpen = false;
    openedChats.add(userId);

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
            const callBtn = document.getElementById('call-btn');
            if (callBtn) callBtn.style.display = userData.is_bot ? 'none' : 'flex';
            const videoCallBtn = document.getElementById('video-call-btn');
            if (videoCallBtn) videoCallBtn.style.display = userData.is_bot ? 'none' : 'flex';
            // Кнопка очистки истории — всегда видна в личных чатах
            const clearBtn = document.getElementById('clear-history-btn');
            if (clearBtn) clearBtn.style.display = 'flex';
        }
    } catch (error) {
        console.error('Error loading user info:', error);
    }
    
    // Загружаем сообщения
    await loadMessages(userId);
    
    // Показываем форму сообщений (это обычный чат, не канал)
    updateMessageInputVisibility(false, true);
    
    // Показываем кнопку добавления в чат
    currentGroupId = null;  // Сбрасываем ID группы

    // Скрываем кнопки группы
    const addMemberBtn = document.getElementById('add-member-btn');
    if (addMemberBtn) addMemberBtn.style.display = 'none';
    const groupSettingsBtn = document.getElementById('group-settings-btn');
    if (groupSettingsBtn) groupSettingsBtn.style.display = 'none';
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
                if (data.other_user.is_online) {
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
    // Стикер
    else if (msg.message_type === 'sticker' || (msg.content && msg.content.startsWith('[sticker]'))) {
        const stickerUrl = msg.content ? msg.content.replace('[sticker]', '') : msg.media_url;
        const packId = msg.sticker_pack_id || '';
        content = `<div class="sticker-message" data-sticker-url="${encodeURIComponent(stickerUrl)}" data-pack-id="${packId}" onclick="_onStickerClick(this)">
            <img data-src="${encodeURIComponent(stickerUrl)}" alt="Стикер" style="width:120px;height:120px;object-fit:contain;cursor:pointer;border-radius:8px;" title="Нажмите, чтобы посмотреть пак">
        </div>`;
    }
    // Видео кружочек
    else if (msg.message_type === 'video_note' && msg.media_url) {
        content = `
            <div class="video-message" onclick="playVideoMessage('${msg.media_url}')">
                <video src="${msg.media_url}" preload="metadata"></video>
                <div class="video-play-btn">
                    <i class="fas fa-play"></i>
                </div>
            </div>
        `;
    }
    // Изображение
    else if (msg.message_type === 'image' && msg.media_url) {
        content = `
            <img src="${msg.media_url}" class="message-image" onclick="viewImage('${msg.media_url}')" alt="Изображение" loading="lazy">
        `;
    }
    // Обычное текстовое сообщение
    else {
        const editedText = msg.edited_at ? ` <span class="edited-text">(изм.)</span>` : '';
        if (msg.message_type === 'poll' && msg.poll_id) {
            content = `<div class="poll-widget" id="poll-widget-${msg.poll_id}"><div class="poll-loading"><i class="fas fa-spinner fa-spin"></i> Загрузка опроса...</div></div>`;
            setTimeout(() => loadAndShowPoll(msg.poll_id, `poll-widget-${msg.poll_id}`), 50);
        } else {
            content = `<div class="message-content">${renderBotContent(msg.content)}${editedText}</div>`;
        }
    }

    return `
        <div class="message ${messageClass}" data-message-id="${msg.id}" data-is-mine="${msg.is_mine ? '1' : '0'}" data-is-deleted="${msg.is_deleted ? '1' : '0'}">
            ${msg.reply_to ? `<div class="reply-preview" onclick="scrollToMsg(${msg.reply_to.id})"><span class="reply-sender">${escapeHtml(msg.reply_to.sender_name)}</span><span class="reply-text">${escapeHtml((msg.reply_to.content||'').slice(0,80))}</span></div>` : ''}
            ${content}
            ${renderBotButtons(msg.bot_buttons)}
            <div class="message-time">${msg.timestamp_iso ? formatMsgTime(msg.timestamp_iso) : msg.timestamp}</div>
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

// Анимация для уведомлений
const style = document.createElement('style');
style.textContent = `
    @keyframes slideOutRight {
        from { transform: translateX(0); opacity: 1; }
        to { transform: translateX(100%); opacity: 0; }
    }
`;
document.head.appendChild(style);

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
            <p style="margin:0 0 20px;color:#718096;font-size:14px;">Сообщения будут удалены только у вас. Собеседник их не потеряет.</p>
            <div style="display:flex;gap:10px;justify-content:center;">
                <button id="clear-confirm-btn" style="background:#e53e3e;color:#fff;border:none;border-radius:8px;padding:10px 22px;font-size:14px;font-weight:600;cursor:pointer;">Очистить</button>
                <button onclick="this.closest('[style*=fixed]').remove()" style="background:#e2e8f0;color:#4a5568;border:none;border-radius:8px;padding:10px 22px;font-size:14px;font-weight:600;cursor:pointer;">Отмена</button>
            </div>
        </div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);
    document.getElementById('clear-confirm-btn').onclick = async () => {
        modal.remove();
        try {
            const r = await fetch(`/chat/${currentChatUserId}/clear`, { method: 'POST' });
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
    currentChatUserId = userId;
    currentGroupId = null; // Сбрасываем текущую группу
    openedChats.add(userId); // Помечаем чат как открытый — бейдж не показываем

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
    
    const messageInput = document.getElementById('message-input');
    const content = messageInput.value.trim();
    
    if (!content) return;

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
        return;
    }
    
    if (!currentChatUserId && !currentGroupId) {
        showError('Выберите чат для отправки сообщения');
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
        } else if (data.success) {
            messageInput.value = '';
            _cancelReply();
        } else if (!response.ok) {
            showError(data.error || 'Не удалось отправить сообщение');
        }
    } catch (error) {
        showError('Не удалось отправить сообщение');
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
    const videoBtn = document.getElementById('video-circle-btn');
    if (videoBtn) {
        videoBtn.addEventListener('click', openVideoRecorder);
    }
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
    
    // Закрытие по клику вне изображения
    modal.addEventListener('click', function(e) {
        if (e.target === modal) {
            modal.remove();
        }
    });
}

window.handleImageSelect = handleImageSelect;
window.viewImage = viewImage;


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
    currentChatUserId = null;
    _favoritesOpen = false;
    currentGroupId = groupId;
    
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
        content = `<div class="sticker-message" data-sticker-url="${encodeURIComponent(stickerUrl)}" data-pack-id="${packId}" onclick="_onStickerClick(this)">
            <img data-src="${encodeURIComponent(stickerUrl)}" alt="Стикер" style="width:120px;height:120px;object-fit:contain;cursor:pointer;border-radius:8px;" title="Нажмите, чтобы посмотреть пак">
        </div>`;
    }
    // Обычное текстовое сообщение
    else {
        const editedText = msg.edited_at ? ` <span class="edited-text">(изм.)</span>` : '';
        if (msg.message_type === 'poll' && msg.poll_id) {
            content = `<div class="poll-widget" id="poll-widget-${msg.poll_id}"><div class="poll-loading"><i class="fas fa-spinner fa-spin"></i> Загрузка опроса...</div></div>`;
            setTimeout(() => loadAndShowPoll(msg.poll_id, `poll-widget-${msg.poll_id}`), 50);
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
                    <div style="display: flex; gap: 10px; color: #a0aec0; font-size: 14px; justify-content: center;">
                        <span><i class="fas fa-calendar"></i> На сайте с ${data.created_at}</span>
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
    } catch (error) {
        console.error('Ошибка загрузки информации о пользователе:', error);
        showError('Не удалось загрузить информацию');
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
    let el = document.getElementById('typing-indicator');
    if (!el) {
        el = document.createElement('div');
        el.id = 'typing-indicator';
        el.className = 'typing-indicator';
        const container = document.getElementById('messages-container');
        if (container) container.appendChild(el);
    }
    el.innerHTML = `<span class="typing-dots"><span></span><span></span><span></span></span> <span>${escapeHtml(name)} печатает...</span>`;
    el.style.display = 'flex';
    scrollToBottom();
}

function hideTypingIndicator(userId) {
    const el = document.getElementById('typing-indicator');
    if (el) el.style.display = 'none';
}

document.addEventListener('DOMContentLoaded', function() {
    setupTypingIndicator();
    // Socket.IO typing handlers
    const waitForSocket = setInterval(() => {
        if (window.socket) {
            clearInterval(waitForSocket);
            window.socket.on('user_typing', function(data) {
                if (data.chat_type === 'private' && currentChatUserId) {
                    showTypingIndicator(data.name);
                } else if (data.chat_type === 'group' && currentGroupId === data.group_id) {
                    showTypingIndicator(data.name);
                }
            });
            window.socket.on('user_stop_typing', function(data) {
                if (data.chat_type === 'private' && currentChatUserId) hideTypingIndicator();
                else if (data.chat_type === 'group' && currentGroupId === data.group_id) hideTypingIndicator();
            });
        }
    }, 500);
});

// ============================================
// ОПРОСЫ
// ============================================

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
    const r = await fetch(`/sparks/react/${msgId}`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({amount}) });
    const d = await r.json();
    if (d.error) { showError(d.error); return; }
    showError(`Отправлено ${amount} ✨`, 'success');
    const bar = document.getElementById(`spark-bar-${msgId}`);
    if (bar) bar.innerHTML = `<span class="spark-total">✨ ${d.total}</span>`;
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
            const el = document.querySelector(`[data-msg-id="${msgId}"]`);
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
    panel.style.cssText = 'position:absolute;bottom:60px;right:0;width:340px;max-height:420px;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,0.22);z-index:1000;display:flex;flex-direction:column;overflow:hidden;';
    panel.innerHTML = `
        <div id="emoji-cat-bar" style="display:flex;align-items:center;gap:2px;padding:6px 8px;border-bottom:1px solid var(--border-color);overflow-x:auto;flex-shrink:0;scrollbar-width:none;">
            ${EMOJI_CATEGORIES.map((c,i) => `<button data-cat="${i}" onclick="_emojiSwitchCat(${i})" title="${c.label}" style="background:none;border:none;cursor:pointer;font-size:20px;padding:4px 5px;border-radius:8px;flex-shrink:0;opacity:${i===0?1:0.5};transition:opacity .15s;">${c.icon}</button>`).join('')}
        </div>
        <div id="sticker-panel-body" style="overflow-y:auto;flex:1;padding:8px;"></div>
        <div style="display:flex;border-top:1px solid var(--border-color);flex-shrink:0;">
            <button id="ep-tab-emoji" onclick="_emojiTabSwitch('emoji')" style="flex:1;padding:10px;border:none;background:var(--primary-color);color:#fff;font-size:13px;font-weight:600;cursor:pointer;border-radius:0 0 0 16px;">Эмодзи</button>
            <button id="ep-tab-stickers" onclick="_emojiTabSwitch('stickers')" style="flex:1;padding:10px;border:none;background:var(--bg-secondary);color:var(--text-secondary);font-size:13px;font-weight:600;cursor:pointer;border-radius:0 0 16px 0;">Стикеры</button>
        </div>
    `;

    const inputArea = document.querySelector('.message-input-container') || document.querySelector('.input-area') || document.querySelector('#message-input')?.parentElement;
    if (inputArea) {
        inputArea.style.position = 'relative';
        inputArea.appendChild(panel);
    } else {
        document.body.appendChild(panel);
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
                const img = document.createElement('img');
                img.src = s.image_url;
                img.style.cssText = 'width:60px;height:60px;object-fit:contain;cursor:pointer;border-radius:8px;padding:2px;transition:background .15s;';
                img.title = 'Нажмите чтобы отправить';
                img.addEventListener('mouseover', () => img.style.background = 'var(--hover-color)');
                img.addEventListener('mouseout', () => img.style.background = '');
                img.addEventListener('click', () => sendStickerFromPanel(s.id));
                grid.appendChild(img);
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
            const img = document.createElement('img');
            img.src = s.image_url;
            img.style.cssText = 'width:72px;height:72px;object-fit:contain;border-radius:8px;';
            grid.appendChild(img);
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
