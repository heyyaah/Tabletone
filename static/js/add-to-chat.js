// Функция показа модального окна добавления в чат
async function showAddToChatModal() {
    if (!currentChatUserId) return;
    
    // Создаем модальное окно
    const modal = document.createElement('div');
    modal.className = 'modal active';
    modal.id = 'add-to-chat-modal';
    modal.innerHTML = `
        <div class="modal-content" style="max-width: 500px;">
            <div class="modal-header">
                <h2><i class="fas fa-user-plus"></i> Добавить в чат</h2>
                <button class="close-btn" onclick="this.closest('.modal').remove()">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="modal-body">
                <p style="margin-bottom: 15px; color: #a0aec0;">Выберите пользователей для создания группы</p>
                
                <div class="search-box" style="margin-bottom: 15px;">
                    <i class="fas fa-search"></i>
                    <input type="text" id="add-user-search" placeholder="Поиск пользователей..." onkeyup="searchUsersToAdd(this.value)">
                </div>
                
                <div id="selected-users-list" style="display: none; margin-bottom: 15px; padding: 10px; background: var(--bg-secondary); border-radius: 8px;">
                    <h4 style="font-size: 14px; margin-bottom: 8px;">Выбрано:</h4>
                    <div id="selected-users-chips" style="display: flex; gap: 8px; flex-wrap: wrap;"></div>
                </div>
                
                <div id="users-to-add-list" style="max-height: 300px; overflow-y: auto;">
                    <div style="text-align: center; padding: 20px; color: #a0aec0;">
                        <i class="fas fa-search"></i>
                        <p>Начните поиск пользователей</p>
                    </div>
                </div>
                
                <div style="margin-top: 15px;">
                    <input type="text" id="group-name-input" placeholder="Название группы" style="width: 100%; padding: 10px; border: 1px solid var(--border-color); border-radius: 8px; background: var(--bg-primary); color: var(--text-primary); margin-bottom: 10px;">
                    <button class="btn btn-primary" onclick="createGroupFromChat()" style="width: 100%;" disabled id="create-group-btn">
                        <i class="fas fa-check"></i> Создать группу
                    </button>
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
}

let selectedUsersToAdd = [];

// Поиск пользователей для добавления
async function searchUsersToAdd(query) {
    if (!query || query.length < 2) {
        document.getElementById('users-to-add-list').innerHTML = `
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
        
        // Фильтруем текущего пользователя и собеседника
        const currentUserId = parseInt(document.body.getAttribute('data-user-id'));
        const filteredUsers = users.filter(u => u.id !== currentUserId && u.id !== currentChatUserId);
        
        displayUsersToAdd(filteredUsers);
    } catch (error) {
        console.error('Ошибка поиска:', error);
    }
}

// Отображение пользователей для добавления
function displayUsersToAdd(users) {
    const container = document.getElementById('users-to-add-list');
    
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
        const isSelected = selectedUsersToAdd.some(u => u.id === user.id);
        const avatarStyle = user.avatar_url 
            ? `background-image: url('${user.avatar_url}'); background-size: cover; background-position: center;`
            : `background: ${user.avatar_color}`;
        const avatarContent = user.avatar_url ? '' : user.avatar_letter;
        
        html += `
            <div class="user-item" style="display: flex; align-items: center; padding: 10px; border-bottom: 1px solid var(--border-color); cursor: pointer; ${isSelected ? 'background: var(--bg-secondary);' : ''}" onclick="toggleUserSelection(${user.id}, '${escapeHtml(user.display_name || user.username)}', '${user.avatar_color}', '${user.avatar_letter}', '${user.avatar_url || ''}')">
                <div class="chat-avatar" style="${avatarStyle} width: 40px; height: 40px; font-size: 16px; margin-right: 12px;">
                    ${avatarContent}
                </div>
                <div style="flex: 1;">
                    <div style="font-weight: 600;">${escapeHtml(user.display_name || user.username)}</div>
                    <div style="font-size: 12px; color: #a0aec0;">@${escapeHtml(user.username)}</div>
                </div>
                <div>
                    ${isSelected ? '<i class="fas fa-check-circle" style="color: var(--primary-color);"></i>' : '<i class="far fa-circle" style="color: #a0aec0;"></i>'}
                </div>
            </div>
        `;
    });
    
    container.innerHTML = html;
}

// Переключение выбора пользователя
function toggleUserSelection(userId, displayName, avatarColor, avatarLetter, avatarUrl) {
    const index = selectedUsersToAdd.findIndex(u => u.id === userId);
    
    if (index > -1) {
        selectedUsersToAdd.splice(index, 1);
    } else {
        selectedUsersToAdd.push({ id: userId, displayName, avatarColor, avatarLetter, avatarUrl });
    }
    
    updateSelectedUsersDisplay();
    
    // Обновляем список
    const searchInput = document.getElementById('add-user-search');
    searchUsersToAdd(searchInput.value);
}

// Обновление отображения выбранных пользователей
function updateSelectedUsersDisplay() {
    const container = document.getElementById('selected-users-list');
    const chipsContainer = document.getElementById('selected-users-chips');
    const createBtn = document.getElementById('create-group-btn');
    
    if (selectedUsersToAdd.length === 0) {
        container.style.display = 'none';
        createBtn.disabled = true;
        return;
    }
    
    container.style.display = 'block';
    createBtn.disabled = false;
    
    let html = '';
    selectedUsersToAdd.forEach(user => {
        html += `
            <div style="display: flex; align-items: center; gap: 6px; padding: 4px 10px; background: var(--primary-color); color: white; border-radius: 16px; font-size: 13px;">
                <span>${escapeHtml(user.displayName)}</span>
                <button type="button" onclick="toggleUserSelection(${user.id}, '${escapeHtml(user.displayName)}', '${user.avatarColor}', '${user.avatarLetter}', '${user.avatarUrl}')" style="background: none; border: none; color: white; cursor: pointer; padding: 0; display: flex; align-items: center;">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        `;
    });
    
    chipsContainer.innerHTML = html;
}

// Создание группы из чата
async function createGroupFromChat() {
    const groupNameInput = document.getElementById('group-name-input');
    const groupName = groupNameInput.value.trim();
    
    if (!groupName) {
        showError('Введите название группы');
        return;
    }
    
    if (selectedUsersToAdd.length === 0) {
        showError('Выберите хотя бы одного пользователя');
        return;
    }
    
    try {
        // Добавляем текущего собеседника в список участников
        const memberIds = [currentChatUserId, ...selectedUsersToAdd.map(u => u.id)];
        
        const response = await fetch('/groups/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: groupName,
                description: '',
                is_channel: false,
                member_ids: memberIds
            })
        });
        
        if (!response.ok) throw new Error('Ошибка создания группы');
        
        const data = await response.json();
        
        if (data.success) {
            // Закрываем модальное окно
            document.getElementById('add-to-chat-modal').remove();
            
            // Обновляем список чатов
            await loadAllChats();
            
            // Открываем новую группу
            openGroup(data.group.id, data.group.name);
            
            showError('Группа создана!', 'success');
        }
    } catch (error) {
        console.error('Ошибка создания группы:', error);
        showError('Не удалось создать группу');
    }
}

// Показываем кнопку добавления только в личных чатах
function updateAddToChatButton() {
    const addBtn = document.getElementById('add-to-chat-btn');
    if (addBtn) {
        addBtn.style.display = currentChatUserId && !currentGroupId ? 'block' : 'none';
    }
}

window.showAddToChatModal = showAddToChatModal;
window.searchUsersToAdd = searchUsersToAdd;
window.toggleUserSelection = toggleUserSelection;
window.createGroupFromChat = createGroupFromChat;
