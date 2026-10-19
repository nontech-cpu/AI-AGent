/**
 * PeculiarAI - Core Application Utilities
 * Navigation, Theme Toggle, Health Check, Session Management
 */

document.addEventListener('DOMContentLoaded', () => {
  // 1. Initialize Theme (Dark by default, persists in localStorage)
  const savedTheme = localStorage.getItem(window.CONFIG?.STORAGE_KEYS?.THEME || 'peculiar_theme') || 'dark';
  document.documentElement.setAttribute('data-theme', savedTheme);

  const themeToggle = document.getElementById('theme-toggle');
  if (themeToggle) {
    themeToggle.addEventListener('click', () => {
      const current = document.documentElement.getAttribute('data-theme') || 'dark';
      const next = current === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem(window.CONFIG?.STORAGE_KEYS?.THEME || 'peculiar_theme', next);
      updateThemeIcon(themeToggle, next);
    });
    updateThemeIcon(themeToggle, savedTheme);
  }

  // 2. Mobile Menu Toggle
  const mobileBtn = document.getElementById('mobile-menu-btn');
  const navLinks = document.querySelector('.nav-links');
  if (mobileBtn && navLinks) {
    mobileBtn.addEventListener('click', () => {
      navLinks.classList.toggle('open');
      const expanded = navLinks.classList.contains('open');
      mobileBtn.setAttribute('aria-expanded', expanded);
    });
  }

  // 3. Periodic Backend Health Check
  checkBackendHealth();
  setInterval(checkBackendHealth, 30000);

  // 4. Update Navigation Links depending on authentication status
  updateAuthNav();
});

function updateThemeIcon(btn, theme) {
  if (!btn) return;
  btn.innerHTML = theme === 'dark' ? '☀️' : '🌙';
  btn.setAttribute('title', `Switch to ${theme === 'dark' ? 'Light' : 'Dark'} Mode`);
}

async function checkBackendHealth() {
  const statusDots = document.querySelectorAll('.backend-status-dot');
  const statusTexts = document.querySelectorAll('.backend-status-text');
  
  if (!statusDots.length && !statusTexts.length) return;

  try {
    const res = await window.api.checkHealth();
    const isOk = res && (res.status === 'ok' || res.status === 'healthy');
    statusDots.forEach(dot => {
      dot.classList.remove('offline');
      dot.classList.add(isOk ? 'online' : 'offline');
    });
    statusTexts.forEach(txt => {
      txt.textContent = isOk ? 'Systems Online' : 'Degraded';
    });
  } catch (err) {
    statusDots.forEach(dot => {
      dot.classList.add('offline');
      dot.classList.remove('online');
    });
    statusTexts.forEach(txt => {
      txt.textContent = 'Backend Reconnecting';
    });
  }
}

function updateAuthNav() {
  const isVerified = localStorage.getItem(window.CONFIG?.STORAGE_KEYS?.IS_VERIFIED);
  const authLink = document.getElementById('nav-auth-link');
  if (!authLink) return;

  if (isVerified === 'true') {
    authLink.textContent = 'Dashboard';
    authLink.href = 'dashboard.html';
  } else {
    authLink.textContent = 'Sign In';
    authLink.href = 'login.html';
  }
}

window.app = {
  isLoggedIn: () => localStorage.getItem(window.CONFIG?.STORAGE_KEYS?.IS_VERIFIED) === 'true',
  getPhone: () => localStorage.getItem(window.CONFIG?.STORAGE_KEYS?.PHONE_NUMBER) || '',
  logout: () => {
    localStorage.removeItem(window.CONFIG?.STORAGE_KEYS?.IS_VERIFIED);
    localStorage.removeItem(window.CONFIG?.STORAGE_KEYS?.VERIFICATION_ID);
    localStorage.removeItem(window.CONFIG?.STORAGE_KEYS?.PHONE_NUMBER);
    window.location.href = 'login.html';
  }
};
