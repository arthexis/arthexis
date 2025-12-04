(function () {
  const ready = (callback) => {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', callback, { once: true });
    } else {
      callback();
    }
  };

  const formatTime = (value) => {
    if (!value) {
      return '';
    }
    try {
      const date = new Date(value);
      if (!Number.isNaN(date.getTime())) {
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      }
    } catch (error) {
      return '';
    }
    return '';
  };

  ready(() => {
    const widget = document.getElementById('chat-widget');
    if (!widget) {
      return;
    }

    const toggle = widget.querySelector('[data-chat-toggle]');
    const overlay = widget.querySelector('[data-chat-overlay]');
    const drawer = widget.querySelector('[data-chat-drawer]');
    const closeBtn = widget.querySelector('[data-chat-close]');
    const messages = widget.querySelector('[data-chat-messages]');
    const statusEl = widget.querySelector('[data-chat-status]');
    const form = widget.querySelector('[data-chat-form]');
    const input = widget.querySelector('[data-chat-input]');
    const submitButton = form ? form.querySelector('button[type="submit"]') : null;
    const unreadBadge = widget.querySelector('[data-chat-unread]');
    const sessionHint = widget.querySelector('[data-chat-session-hint]');
    const toggleLabel = toggle.querySelector('.chat-toggle-label');

    if (!toggle || !overlay || !drawer || !messages || !form || !input || !statusEl) {
      return;
    }

    const strings = {
      connected: widget.dataset.connectedText || 'Connected',
      connecting: widget.dataset.connectingText || 'Connecting…',
      disconnected: widget.dataset.disconnectedText || 'Disconnected',
      unread: widget.dataset.unreadText || 'New',
      open: widget.dataset.openText || 'Need help?',
      close: widget.dataset.closeText || 'Close chat',
      session: widget.dataset.sessionLabel || 'Session',
      empty: widget.dataset.emptyText || '',
      error: widget.dataset.errorText || '',
      staffJoin: widget.dataset.staffJoinText || '',
      staffLeave: widget.dataset.staffLeaveText || '',
      visitorJoin: widget.dataset.visitorJoinText || '',
      visitorLeave: widget.dataset.visitorLeaveText || '',
    };

    const storageKey = widget.dataset.sessionStorageKey || 'pages.chat.session';
    const socketPath = widget.dataset.path || '/ws/pages/chat/';
    let socket = null;
    let reconnectTimer = null;
    let intentionallyClosed = false;
    let sessionId = window.localStorage.getItem(storageKey) || null;
    let unread = 0;
    let drawerOpen = false;
    let placeholderEl = null;
    let previousFocus = null;

    const resetPlaceholder = () => {
      if (placeholderEl) {
        placeholderEl.remove();
        placeholderEl = null;
      }
    };

    const ensurePlaceholder = () => {
      if (messages.children.length === 0 && strings.empty) {
        placeholderEl = document.createElement('div');
        placeholderEl.className = 'chat-meta text-muted';
        placeholderEl.dataset.placeholder = 'true';
        placeholderEl.textContent = strings.empty;
        messages.appendChild(placeholderEl);
      }
    };

    const scrollToBottom = () => {
      messages.scrollTop = messages.scrollHeight;
    };

    const setStatus = (state) => {
      let label = '';
      let isConnected = false;
      switch (state) {
        case 'connected':
          label = strings.connected;
          isConnected = true;
          break;
        case 'connecting':
          label = strings.connecting;
          break;
        default:
          label = strings.disconnected;
          break;
      }
      statusEl.textContent = label;
      if (submitButton) {
        submitButton.disabled = !isConnected;
        submitButton.setAttribute('aria-disabled', isConnected ? 'false' : 'true');
      }
    };

    const updateSessionHint = (id) => {
      if (!sessionHint) {
        return;
      }
      if (!id) {
        sessionHint.textContent = '';
        return;
      }
      sessionHint.textContent = `${strings.session} ${id.slice(0, 8).toUpperCase()}`;
    };

    const resetUnread = () => {
      unread = 0;
      if (unreadBadge) {
        unreadBadge.hidden = true;
      }
    };

    const addUnread = () => {
      if (drawerOpen || !unreadBadge) {
        return;
      }
      unread += 1;
      unreadBadge.hidden = false;
      unreadBadge.textContent = unread > 1 ? `${strings.unread} (${unread})` : strings.unread;
    };

    const appendNotice = (text) => {
      if (!text) {
        return;
      }
      resetPlaceholder();
      const notice = document.createElement('div');
      notice.className = 'chat-meta text-muted';
      notice.textContent = text;
      messages.appendChild(notice);
      scrollToBottom();
    };

    const appendMessage = (message) => {
      if (!message || typeof message !== 'object') {
        return;
      }
      const content = (message.content || '').toString();
      const author = (message.author || '').toString();
      const time = formatTime(message.created);
      const avatar = (message.avatar || '').toString();
      const item = document.createElement('div');
      item.className = 'chat-message';
      if (message.from_staff) {
        item.classList.add('staff');
      }
      resetPlaceholder();
      if (avatar) {
        const avatarImg = document.createElement('img');
        avatarImg.className = 'chat-avatar';
        avatarImg.src = avatar;
        avatarImg.alt = author || '';
        avatarImg.loading = 'lazy';
        item.appendChild(avatarImg);
      }
      const body = document.createElement('div');
      body.className = 'chat-body';
      const meta = document.createElement('div');
      meta.className = 'chat-meta';
      meta.textContent = time ? `${author} · ${time}` : author;
      const bubble = document.createElement('div');
      bubble.className = 'chat-bubble';
      bubble.textContent = content;
      body.appendChild(meta);
      body.appendChild(bubble);
      item.appendChild(body);
      messages.appendChild(item);
      scrollToBottom();
      addUnread();
    };

    const handlePresence = (payload) => {
      if (!payload || typeof payload !== 'object') {
        return;
      }
      const isStaff = Boolean(payload.staff);
      const event = payload.event;
      if (event === 'join') {
        appendNotice(isStaff ? strings.staffJoin : strings.visitorJoin);
      } else if (event === 'leave') {
        appendNotice(isStaff ? strings.staffLeave : strings.visitorLeave);
      }
    };

    const openDrawer = () => {
      if (drawerOpen) {
        return;
      }
      drawerOpen = true;
      previousFocus = document.activeElement;
      widget.classList.add('open');
      toggle.setAttribute('aria-expanded', 'true');
      if (toggleLabel) {
        toggleLabel.textContent = strings.close;
      }
      resetUnread();
      overlay.removeAttribute('hidden');
      window.requestAnimationFrame(() => {
        overlay.classList.add('show');
        drawer.classList.add('open');
        document.body.classList.add('chat-open');
        window.requestAnimationFrame(() => {
          try {
            input.focus({ preventScroll: false });
          } catch (error) {
            input.focus();
          }
          scrollToBottom();
        });
      });
    };

    const closeDrawer = () => {
      if (!drawerOpen) {
        return;
      }
      drawerOpen = false;
      widget.classList.remove('open');
      overlay.classList.remove('show');
      drawer.classList.remove('open');
      document.body.classList.remove('chat-open');
      toggle.setAttribute('aria-expanded', 'false');
      if (toggleLabel) {
        toggleLabel.textContent = strings.open;
      }
      const focusTarget = previousFocus && typeof previousFocus.focus === 'function' ? previousFocus : toggle;
      previousFocus = null;
      window.setTimeout(() => {
        overlay.setAttribute('hidden', '');
        if (focusTarget) {
          try {
            focusTarget.focus({ preventScroll: true });
          } catch (error) {
            focusTarget.focus();
          }
        }
      }, 200);
    };

    const clearStoredSession = () => {
      sessionId = null;
      updateSessionHint(null);
      try {
        window.localStorage.removeItem(storageKey);
      } catch (error) {
        /* ignore storage errors */
      }
    };

    const buildSocketUrl = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
      const host = window.location.host;
      const params = new URLSearchParams();
      if (sessionId) {
        params.set('session', sessionId);
      }
      const query = params.toString();
      return `${protocol}${host}${socketPath}${query ? (socketPath.includes('?') ? '&' : '?') + query : ''}`;
    };

    const connect = () => {
      if (socket && socket.readyState === WebSocket.OPEN) {
        return;
      }
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      setStatus('connecting');
      intentionallyClosed = false;
      try {
        socket = new WebSocket(buildSocketUrl());
      } catch (error) {
        setStatus('disconnected');
        return;
      }
      socket.addEventListener('open', () => {
        setStatus('connected');
        resetPlaceholder();
      });
      socket.addEventListener('message', (event) => {
        let payload;
        try {
          payload = JSON.parse(event.data);
        } catch (error) {
          return;
        }
        if (!payload || typeof payload !== 'object') {
          return;
        }
        switch (payload.type) {
          case 'history':
            messages.innerHTML = '';
            sessionId = payload.session || null;
            if (sessionId) {
              try {
                window.localStorage.setItem(storageKey, sessionId);
              } catch (error) {
                /* ignore storage errors */
              }
            }
            updateSessionHint(sessionId);
            if (Array.isArray(payload.messages)) {
              payload.messages.forEach((entry) => appendMessage(entry));
              if (payload.messages.length === 0) {
                ensurePlaceholder();
              }
            } else {
              ensurePlaceholder();
            }
            break;
          case 'message':
            appendMessage(payload);
            break;
          case 'presence':
            handlePresence(payload);
            break;
          default:
            break;
        }
      });
      socket.addEventListener('close', (event) => {
        if (event && typeof event.code === 'number' && event.code >= 4400 && event.code < 4500) {
          clearStoredSession();
        }
        setStatus('disconnected');
        if (!intentionallyClosed) {
          reconnectTimer = window.setTimeout(connect, 2500);
        }
      });
      socket.addEventListener('error', () => {
        setStatus('disconnected');
      });
    };

    toggle.addEventListener('click', () => {
      if (drawerOpen) {
        closeDrawer();
      } else {
        openDrawer();
      }
    });

    if (closeBtn) {
      closeBtn.addEventListener('click', closeDrawer);
    }

    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) {
        closeDrawer();
      }
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && drawerOpen) {
        event.preventDefault();
        closeDrawer();
      }
    });

    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const value = input.value.trim();
      if (!value) {
        return;
      }
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        statusEl.textContent = strings.error || strings.disconnected;
        window.setTimeout(() => setStatus('disconnected'), 3000);
        return;
      }
      try {
        socket.send(
          JSON.stringify({
            type: 'message',
            content: value,
          })
        );
        input.value = '';
      } catch (error) {
        statusEl.textContent = strings.error || strings.disconnected;
        window.setTimeout(() => setStatus('disconnected'), 3000);
      }
    });

    toggle.setAttribute('aria-expanded', 'false');
    if (toggleLabel) {
      toggleLabel.textContent = strings.open;
    }
    ensurePlaceholder();
    setStatus('connecting');
    connect();

    window.addEventListener('beforeunload', () => {
      intentionallyClosed = true;
      if (socket) {
        try {
          socket.close();
        } catch (error) {
          /* ignore */
        }
      }
    });
  });
})();
