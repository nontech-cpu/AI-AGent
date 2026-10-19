/**
 * PeculiarAI - Global Configuration
 * 
 * Configure the API base URL here. 
 * In development, points to the local or ngrok testing backend.
 * In production, change to your production backend URL (e.g., https://api.peculiarai.shapebytes.com).
 */
const CONFIG = {
  // Configurable Backend Base URL (supports local dev, override, and production backend)
  API_BASE_URL: (function() {
    if (typeof window !== 'undefined' && window.__PECULIAR_API_BASE_URL__) {
      return window.__PECULIAR_API_BASE_URL__;
    }
    if (typeof localStorage !== 'undefined' && localStorage.getItem('peculiar_api_base_url')) {
      return localStorage.getItem('peculiar_api_base_url');
    }
    const isLocal = typeof window !== 'undefined' && (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1');
    if (isLocal) {
      return 'http://127.0.0.1:8000';
    }
    // When deployed on Netlify or public domain, use your live Render backend URL
    return 'https://peculiar-ai-backend.onrender.com';
  })(),

  // App Metadata
  APP_NAME: 'PeculiarAI',
  APP_TAGLINE: 'Your Intelligent Multi-Channel AI Assistant',
  PRODUCTION_DOMAIN: 'https://peculiarai.shapebytes.com',
  TELEGRAM_BOT_USERNAME: 'PeculiarAI_Verify_Bot', // Verification bot username
  TELEGRAM_VERIFY_BOT_URL: 'https://t.me/PeculiarAI_Verify_Bot',
  TELEGRAM_MAIN_BOT_USERNAME: 'PeculiarAIBOT',
  TELEGRAM_MAIN_BOT_URL: 'https://t.me/PeculiarAIBOT?start=website_chat',
  SUPPORT_EMAIL: 'support@peculiarai.shapebytes.com',

  // Google OAuth Verification Scope List (Accurately reflects current integrations)
  GOOGLE_SCOPES: [
    {
      id: 'calendar.events',
      name: 'Google Calendar Events',
      scope: 'https://www.googleapis.com/auth/calendar.events',
      purpose: 'Create, view, and schedule calendar appointments requested by the user.',
      channel: 'Telegram Assistant'
    },
    {
      id: 'gmail.compose',
      name: 'Gmail Compose',
      scope: 'https://www.googleapis.com/auth/gmail.compose',
      purpose: 'Draft and send emails on behalf of the user upon explicit confirmation.',
      channel: 'Telegram Assistant'
    },
    {
      id: 'openid',
      name: 'OpenID Connect',
      scope: 'openid',
      purpose: 'Verify user identity securely.',
      channel: 'Core Authentication'
    },
    {
      id: 'email',
      name: 'Email Address',
      scope: 'https://www.googleapis.com/auth/userinfo.email',
      purpose: 'Identify the connected Google account.',
      channel: 'Core Authentication'
    }
  ],

  // Storage Keys
  STORAGE_KEYS: {
    VERIFICATION_ID: 'peculiar_verification_id',
    PHONE_NUMBER: 'peculiar_user_phone',
    IS_VERIFIED: 'peculiar_is_verified',
    CHAT_HISTORY: 'peculiar_chat_history',
    THEME: 'peculiar_theme'
  },

  // Request timeout in milliseconds
  REQUEST_TIMEOUT_MS: 30000
};

// Freeze to prevent runtime tampering
Object.freeze(CONFIG);
window.CONFIG = CONFIG;
