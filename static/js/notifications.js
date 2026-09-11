// ============================================
// NOTIFICATION JAVASCRIPT
// ============================================

document.addEventListener('DOMContentLoaded', function() {
    const notificationToggle = document.getElementById('notificationToggle');
    const notificationDropdown = document.getElementById('notificationDropdown');
    const notificationList = document.getElementById('notificationList');
    const notificationBadge = document.getElementById('notificationBadge');
    const markAllReadBtn = document.getElementById('markAllReadBtn');
    
    let isOpen = false;
    let isPolling = false;
    
    // ============================================
    // TOGGLE NOTIFICATION DROPDOWN
    // ============================================
    
    function toggleDropdown() {
        isOpen = !isOpen;
        notificationDropdown.classList.toggle('open', isOpen);
        
        if (isOpen) {
            loadNotifications();
        }
    }
    
    if (notificationToggle) {
        notificationToggle.addEventListener('click', function(e) {
            e.stopPropagation();
            toggleDropdown();
        });
    }
    
    // Close dropdown when clicking outside
    document.addEventListener('click', function(e) {
        const wrapper = document.getElementById('notificationWrapper');
        if (wrapper && !wrapper.contains(e.target)) {
            if (isOpen) {
                isOpen = false;
                notificationDropdown.classList.remove('open');
            }
        }
    });
    
    // ============================================
    // LOAD NOTIFICATIONS
    // ============================================
    
    function loadNotifications() {
        fetch('/notifications/api/get?limit=10')
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    console.error(data.error);
                    return;
                }
                
                renderNotifications(data.notifications);
                updateBadge(data.unread_count);
                updateMarkAllButton(data.unread_count);
            })
            .catch(err => console.error('Error loading notifications:', err));
    }
    
    // ============================================
    // RENDER NOTIFICATIONS
    // ============================================
    
    function renderNotifications(notifications) {
        if (!notificationList) return;
        
        if (!notifications || notifications.length === 0) {
            notificationList.innerHTML = `
                <div class="notification-empty">
                    <i class="fas fa-bell-slash"></i>
                    <p>No notifications yet</p>
                </div>
            `;
            return;
        }
        
        let html = '';
        notifications.forEach(function(n) {
            const isUnread = n.is_read === 0;
            const timeAgo = getTimeAgo(n.created_at);
            
            html += `
                <div class="notification-item ${isUnread ? 'unread' : ''}" 
                     data-id="${n.id}"
                     onclick="handleNotificationClick(${n.id}, '${n.link || ''}')">
                    <span class="n-icon">${n.icon || '🔔'}</span>
                    <div class="n-content">
                        <div class="n-title">${escapeHtml(n.title)}</div>
                        <div class="n-body">${escapeHtml(n.body)}</div>
                        <div class="n-time">${timeAgo}</div>
                    </div>
                    ${isUnread ? `<button class="n-mark-read" onclick="event.stopPropagation(); markNotificationRead(${n.id})">✓</button>` : ''}
                </div>
            `;
        });
        
        notificationList.innerHTML = html;
    }
    
    // ============================================
    // UPDATE BADGE
    // ============================================
    
    function updateBadge(count) {
        if (notificationBadge) {
            if (count > 0) {
                notificationBadge.textContent = count > 99 ? '99+' : count;
                notificationBadge.style.display = 'flex';
            } else {
                notificationBadge.style.display = 'none';
            }
        }
    }
    
    // ============================================
    // UPDATE MARK ALL BUTTON
    // ============================================
    
    function updateMarkAllButton(count) {
        if (markAllReadBtn) {
            markAllReadBtn.style.display = count > 0 ? 'block' : 'none';
        }
    }
    
    // ============================================
    // HANDLE NOTIFICATION CLICK
    // ============================================
    
    window.handleNotificationClick = function(id, link) {
        // Mark as read
        fetch('/notifications/api/mark-read', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: id })
        }).then(() => {
            // Close dropdown
            isOpen = false;
            notificationDropdown.classList.remove('open');
            
            // Navigate to link
            if (link) {
                window.location.href = link;
            }
        }).catch(() => {
            // If error, still navigate
            if (link) {
                window.location.href = link;
            }
        });
    };
    
    // ============================================
    // MARK NOTIFICATION READ
    // ============================================
    
    window.markNotificationRead = function(id) {
        fetch('/notifications/api/mark-read', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: id })
        }).then(response => response.json())
        .then(data => {
            if (data.success) {
                // Reload notifications
                loadNotifications();
            }
        });
    };
    
    // ============================================
    // MARK ALL AS READ
    // ============================================
    
    if (markAllReadBtn) {
        markAllReadBtn.addEventListener('click', function() {
            fetch('/notifications/api/mark-all-read', {
                method: 'POST'
            }).then(response => response.json())
            .then(data => {
                if (data.success) {
                    loadNotifications();
                    updateBadge(0);
                    updateMarkAllButton(0);
                }
            });
        });
    }
    
    // ============================================
    // GET TIME AGO
    // ============================================
    
    function getTimeAgo(dateString) {
        const now = new Date();
        const date = new Date(dateString);
        const diff = Math.floor((now - date) / 1000); // seconds
        
        if (diff < 60) return 'Just now';
        if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
        if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
        if (diff < 604800) return Math.floor(diff / 86400) + 'd ago';
        return date.toLocaleDateString();
    }
    
    // ============================================
    // ESCAPE HTML
    // ============================================
    
    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    // ============================================
    // POLL FOR NEW NOTIFICATIONS
    // ============================================
    
    function pollNotifications() {
        if (isPolling) return;
        isPolling = true;
        
        fetch('/notifications/api/unread-count')
            .then(response => response.json())
            .then(data => {
                if (data.count !== undefined) {
                    updateBadge(data.count);
                    updateMarkAllButton(data.count);
                }
                isPolling = false;
            })
            .catch(err => {
                console.error('Error polling notifications:', err);
                isPolling = false;
            });
    }
    
    // Start polling every 15 seconds
    setInterval(pollNotifications, 15000);
    
    // Initial load after 2 seconds
    setTimeout(pollNotifications, 2000);
    
    // Also poll when page becomes visible again
    document.addEventListener('visibilitychange', function() {
        if (!document.hidden) {
            pollNotifications();
        }
    });
    
    // ============================================
    // BROWSER NOTIFICATION (for live quiz starts)
    // ============================================
    
    function requestNotificationPermission() {
        if ('Notification' in window && Notification.permission === 'default') {
            Notification.requestPermission();
        }
    }
    
    // Request permission on first interaction
    document.addEventListener('click', function() {
        requestNotificationPermission();
    }, { once: true });
    
    // Show browser notification (called from server via SSE/WebSocket)
    window.showBrowserNotification = function(title, body, link, icon) {
        if (!('Notification' in window) || Notification.permission !== 'granted') {
            return;
        }
        
        const notification = new Notification(title, {
            body: body,
            icon: icon || '/static/images/logo.png',
            tag: 'live-quiz',
            requireInteraction: true
        });
        
        notification.onclick = function() {
            window.focus();
            if (link) {
                window.location.href = link;
            }
            notification.close();
        };
        
        // Auto-close after 10 seconds
        setTimeout(function() {
            notification.close();
        }, 10000);
    };
});