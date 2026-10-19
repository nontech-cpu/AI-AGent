/**
 * PeculiarAI - Centralized API Client
 * Wraps all Fetch requests to the verified FastAPI backend endpoints.
 * Handles timeouts, network failure, error formatting, and headers.
 */
class PeculiarAPI {
  constructor(baseUrl = window.CONFIG?.API_BASE_URL || '') {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
  }

  /**
   * Core request helper with timeout and standardized error handling
   */
  async request(endpoint, options = {}) {
    const url = `${this.baseUrl}${endpoint}`;
    const timeoutMs = options.timeout || window.CONFIG?.REQUEST_TIMEOUT_MS || 30000;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    const headers = {
      'Accept': 'application/json',
      'ngrok-skip-browser-warning': 'true', // Required when testing via ngrok
      ...(options.headers || {})
    };

    if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(options.body);
    }

    try {
      const response = await fetch(url, {
        ...options,
        headers,
        mode: 'cors',
        signal: controller.signal
      });

      clearTimeout(timer);

      // Check if response is JSON
      const contentType = response.headers.get('content-type') || '';
      let data = null;

      if (contentType.includes('application/json')) {
        data = await response.json();
      } else {
        const text = await response.text();
        data = { rawText: text };
      }

      if (!response.ok) {
        const errorMessage = data?.detail || data?.message || `HTTP Error ${response.status}: ${response.statusText}`;
        const error = new Error(errorMessage);
        error.status = response.status;
        error.data = data;
        throw error;
      }

      return data;
    } catch (err) {
      clearTimeout(timer);
      if (err.name === 'AbortError') {
        const timeoutError = new Error('Request timed out. Please check your network connection and try again.');
        timeoutError.isTimeout = true;
        throw timeoutError;
      }
      throw err;
    }
  }

  /**
   * 1. GET /health
   * Checks if backend service is available
   */
  async checkHealth() {
    return await this.request('/health', { method: 'GET' });
  }

  /**
   * 2. POST /web/verification/start
   * @param {string} phone - E.164 formatted phone number (e.g. +919876543210)
   * @returns {Promise<{verification_id: string, telegram_url: string, expires_in_seconds: number}>}
   */
  async startVerification(phone) {
    if (!phone || typeof phone !== 'string') {
      throw new Error('Valid phone number is required.');
    }
    return await this.request('/web/verification/start', {
      method: 'POST',
      body: { phone: phone.trim() }
    });
  }

  /**
   * 3. POST /web/verification/verify
   * @param {string} verificationId - ID received from startVerification
   * @param {string} otp - 6-digit OTP code received via Telegram bot
   * @returns {Promise<{verified: boolean}>}
   */
  async verifyOtp(verificationId, otp) {
    if (!verificationId) {
      throw new Error('Verification session expired or invalid.');
    }
    if (!otp || String(otp).trim().length < 4) {
      throw new Error('Please enter the verification code sent by the Telegram bot.');
    }
    return await this.request('/web/verification/verify', {
      method: 'POST',
      body: {
        verification_id: verificationId.trim(),
        otp: String(otp).trim()
      }
    });
  }

  /**
   * 4. POST /web/chat
   * @param {string} message - User message text
   * @param {string} [sessionId] - Optional session identifier
   * @returns {Promise<{reply: string}>}
   */
  async sendChatMessage(message, sessionId = '') {
    if (!message || !message.trim()) {
      throw new Error('Message cannot be empty.');
    }
    const payload = { message: message.trim() };
    if (sessionId) {
      payload.session_id = sessionId;
    }
    return await this.request('/web/chat', {
      method: 'POST',
      body: payload
    });
  }

  /**
   * Helper to get Google OAuth callback URL
   */
  getGoogleCallbackUrl() {
    return `${this.baseUrl}/google/callback`;
  }

  /**
   * 5. POST /web/email-link/start
   * @param {string} email - Email address to verify
   * @param {string} verificationId - Active verification ID from Telegram OTP
   * @returns {Promise<{recipient?: string, subject?: string, body?: string, gmail_url?: string}>}
   */
  async startEmailLink(email, verificationId) {
    if (!email || !email.includes('@')) {
      throw new Error('Please enter a valid email address.');
    }
    if (!verificationId) {
      throw new Error('Please complete Telegram verification first.');
    }
    return await this.request('/web/email-link/start', {
      method: 'POST',
      body: {
        email: email.trim(),
        verification_id: verificationId.trim()
      }
    });
  }

  /**
   * 6. POST /web/email-link/status
   * @param {string} verificationId - Active verification ID
   * @param {string} [email] - Optional specific email address being verified
   * @returns {Promise<{linked: boolean, verified: boolean, email?: string, status?: string}>}
   */
  async getEmailLinkStatus(verificationId, email = null) {
    if (!verificationId) {
      throw new Error('Verification ID is required.');
    }
    const body = { verification_id: verificationId.trim() };
    if (email && typeof email === 'string' && email.trim()) {
      body.email = email.trim();
    }
    return await this.request('/web/email-link/status', {
      method: 'POST',
      body
    });
  }

  /**
   * Backward-compatible alias for checkEmailLinkStatus
   */
  async checkEmailLinkStatus(email, verificationId) {
    return await this.getEmailLinkStatus(verificationId, email);
  }

  /**
   * 7. POST /web/email-link/unlink
   * @param {string} verificationId - Active verification ID
   */
  async unlinkEmail(verificationId) {
    if (!verificationId) {
      throw new Error('Verification ID is required.');
    }
    return await this.request('/web/email-link/unlink', {
      method: 'POST',
      body: { verification_id: verificationId.trim() }
    });
  }

  /**
   * 8. POST /web/google/status
   * @param {string} verificationId - Active verification ID
   * @returns {Promise<{connected: boolean, email?: string, message?: string}>}
   */
  async getGoogleStatus(verificationId) {
    if (!verificationId) {
      throw new Error('Verification ID is required.');
    }
    return await this.request('/web/google/status', {
      method: 'POST',
      body: { verification_id: verificationId.trim() }
    });
  }

  /**
   * 9. POST /web/google/start
   * @param {string} verificationId - Active verification ID
   * @param {string} [returnUrl] - Safe redirect return URL
   * @returns {Promise<{auth_url?: string, already_connected?: boolean, email?: string}>}
   */
  async startGoogleConnect(verificationId, returnUrl = null) {
    if (!verificationId) {
      throw new Error('Verification ID is required.');
    }
    const body = { verification_id: verificationId.trim() };
    if (returnUrl) {
      body.return_url = returnUrl;
    }
    return await this.request('/web/google/start', {
      method: 'POST',
      body
    });
  }

  /**
   * 10. POST /web/google/disconnect
   * @param {string} verificationId - Active verification ID
   * @returns {Promise<{disconnected: boolean}>}
   */
  async disconnectGoogle(verificationId) {
    if (!verificationId) {
      throw new Error('Verification ID is required.');
    }
    return await this.request('/web/google/disconnect', {
      method: 'POST',
      body: { verification_id: verificationId.trim() }
    });
  }
}

// Instantiate global API client
window.api = new PeculiarAPI();
