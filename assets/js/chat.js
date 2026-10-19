/**
 * PeculiarAI - Chat Controller
 * Manages conversation state, message rendering, markdown formatting,
 * keyboard shortcuts, copy actions, and error handling for /web/chat.
 */

document.addEventListener('DOMContentLoaded', () => {
  const chatMessages = document.getElementById('chat-messages');
  const chatInput = document.getElementById('chat-input');
  const sendBtn = document.getElementById('send-btn');
  const clearChatBtn = document.getElementById('clear-chat-btn');
  const typingIndicator = document.getElementById('typing-indicator');
  const emptyState = document.getElementById('empty-state');

  // Generate or retrieve persistent session ID for the user
  let sessionId = localStorage.getItem('peculiar_session_id');
  if (!sessionId) {
    sessionId = 'sess_' + Math.random().toString(36).substring(2, 12) + Date.now().toString(36);
    localStorage.setItem('peculiar_session_id', sessionId);
  }

  // Load saved chat history
  let history = [];
  try {
    const raw = localStorage.getItem(window.CONFIG?.STORAGE_KEYS?.CHAT_HISTORY);
    if (raw) history = JSON.parse(raw);
  } catch (e) {
    history = [];
  }

  // Render initial history
  if (history.length > 0) {
    if (emptyState) emptyState.style.display = 'none';
    history.forEach(item => appendMessage(item.role, item.text, false));
    scrollToBottom();
  }

  // Auto-resize textarea
  if (chatInput) {
    chatInput.addEventListener('input', () => {
      chatInput.style.height = 'auto';
      chatInput.style.height = `${Math.min(chatInput.scrollHeight, 160)}px`;
      if (sendBtn) sendBtn.disabled = !chatInput.value.trim();
    });

    // Enter to Send, Shift+Enter for new line
    chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (chatInput.value.trim()) {
          sendMessage();
        }
      }
    });
  }

  if (sendBtn) {
    sendBtn.addEventListener('click', sendMessage);
  }

  if (clearChatBtn) {
    clearChatBtn.addEventListener('click', () => {
      if (confirm('Are you sure you want to clear your current conversation?')) {
        history = [];
        localStorage.removeItem(window.CONFIG?.STORAGE_KEYS?.CHAT_HISTORY);
        if (chatMessages) {
          chatMessages.innerHTML = '';
          if (emptyState) {
            chatMessages.appendChild(emptyState);
            emptyState.style.display = 'flex';
          }
        }
      }
    });
  }

  async function sendMessage() {
    const text = chatInput.value.trim();
    if (!text) return;

    if (emptyState) emptyState.style.display = 'none';

    // 1. Append user message
    appendMessage('user', text, true);
    chatInput.value = '';
    chatInput.style.height = 'auto';
    sendBtn.disabled = true;

    // 2. Show typing indicator
    showTyping(true);
    scrollToBottom();

    try {
      // 3. Send to actual API endpoint POST /web/chat
      const response = await window.api.sendChatMessage(text, sessionId);
      showTyping(false);

      const replyText = response?.reply || response?.text || 'No response from assistant.';
      appendMessage('assistant', replyText, true);
    } catch (err) {
      showTyping(false);
      appendErrorMessage(err.message || 'Unable to connect to assistant backend.', text);
    } finally {
      scrollToBottom();
      chatInput.focus();
    }
  }

  function appendMessage(role, text, save = true) {
    if (!chatMessages) return;

    const bubble = document.createElement('div');
    bubble.className = `message-bubble message-${role}`;

    const formattedContent = parseMarkdown(text);
    const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    bubble.innerHTML = `
      <div class="message-content">${formattedContent}</div>
      <div class="message-meta">
        <span>${role === 'user' ? 'You' : 'PeculiarAI'} • ${timeStr}</span>
        ${role === 'assistant' ? `<button class="copy-btn btn-sm btn-secondary" title="Copy response">Copy</button>` : ''}
      </div>
    `;

    // Copy event listener
    const copyBtn = bubble.querySelector('.copy-btn');
    if (copyBtn) {
      copyBtn.addEventListener('click', () => {
        navigator.clipboard.writeText(text).then(() => {
          copyBtn.textContent = 'Copied!';
          setTimeout(() => copyBtn.textContent = 'Copy', 2000);
        });
      });
    }

    chatMessages.appendChild(bubble);

    if (save) {
      history.push({ role, text, timestamp: Date.now() });
      try {
        localStorage.setItem(window.CONFIG.STORAGE_KEYS.CHAT_HISTORY, JSON.stringify(history));
      } catch (e) {
        // Handle storage quota if exceeded
      }
    }
  }

  function appendErrorMessage(errorMsg, originalText) {
    if (!chatMessages) return;

    const bubble = document.createElement('div');
    bubble.className = 'message-bubble message-assistant';
    bubble.style.borderColor = 'rgba(239, 68, 68, 0.4)';
    bubble.style.background = 'rgba(239, 68, 68, 0.08)';

    bubble.innerHTML = `
      <div class="message-content" style="color: #fca5a5;">
        <strong>⚠️ Connection Issue:</strong> ${escapeHtml(errorMsg)}
      </div>
      <div class="message-meta" style="margin-top: 0.75rem;">
        <button class="retry-btn btn btn-sm btn-secondary">🔄 Retry</button>
      </div>
    `;

    const retryBtn = bubble.querySelector('.retry-btn');
    if (retryBtn) {
      retryBtn.addEventListener('click', () => {
        bubble.remove();
        chatInput.value = originalText;
        sendMessage();
      });
    }

    chatMessages.appendChild(bubble);
  }

  function showTyping(show) {
    if (!typingIndicator) return;
    typingIndicator.style.display = show ? 'flex' : 'none';
  }

  function scrollToBottom() {
    if (chatMessages) {
      chatMessages.scrollTop = chatMessages.scrollHeight;
    }
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  /**
   * Safe Markdown parser (prevents raw XSS injection while formatting rich text)
   */
  function parseMarkdown(text) {
    if (!text) return '';
    let escaped = escapeHtml(text);

    // Code blocks ```code```
    escaped = escaped.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>');
    // Inline code `code`
    escaped = escaped.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Bold **text**
    escaped = escaped.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // Italic *text*
    escaped = escaped.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    // Line breaks
    escaped = escaped.replace(/\n/g, '<br>');

    return escaped;
  }
});
