/**
 * PeculiarAI — Authentication & Telegram OTP Controller
 * Matches the updated multi-step verification flow and redirects to connect.html
 */

document.addEventListener('DOMContentLoaded', () => {
  // Pre-warm backend service in the background as soon as page loads
  if (window.api && typeof window.api.checkHealth === 'function') {
    window.api.checkHealth().catch(() => {});
  }

  const phoneForm = document.getElementById('phone-form');
  const otpStep = document.getElementById('otp-step');
  const verifyForm = document.getElementById('verify-form');
  const countryCodeSelect = document.getElementById('country-code');
  const phoneInput = document.getElementById('phone-input');
  const otpInput = document.getElementById('otp-input');
  const sendCodeBtn = document.getElementById('send-code-btn');
  const verifyCodeBtn = document.getElementById('verify-code-btn');
  const telegramBotLink = document.getElementById('telegram-bot-link');
  const countdownEl = document.getElementById('countdown-timer');
  const resendBtn = document.getElementById('resend-code-btn');
  const authAlert = document.getElementById('auth-alert');
  const stepTag = document.getElementById('step-tag');
  const authSubtitle = document.getElementById('auth-subtitle');

  let currentVerificationId = localStorage.getItem(window.CONFIG?.STORAGE_KEYS?.VERIFICATION_ID) || null;
  let countdownInterval = null;

  function showAlert(message, type = 'error') {
    if (!authAlert) return;
    authAlert.className = `alert-box alert-${type}`;
    authAlert.innerHTML = `<span>${type === 'error' ? '⚠️' : '✅'}</span> <div>${escapeHtml(message)}</div>`;
    authAlert.style.display = 'flex';
  }

  function hideAlert() {
    if (!authAlert) return;
    authAlert.style.display = 'none';
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function startCountdown(seconds = 600) {
    clearInterval(countdownInterval);
    let remaining = seconds;
    if (countdownEl) countdownEl.textContent = `Code expires in ${formatTime(remaining)}`;
    if (resendBtn) resendBtn.disabled = true;

    countdownInterval = setInterval(() => {
      remaining--;
      if (countdownEl) countdownEl.textContent = `Code expires in ${formatTime(remaining)}`;
      if (remaining <= 0) {
        clearInterval(countdownInterval);
        if (resendBtn) resendBtn.disabled = false;
        showAlert('Verification code expired. Please request a new code.', 'error');
      }
    }, 1000);
  }

  function formatTime(sec) {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  }

  // Handle Step 1: Request Code on Telegram
  if (phoneForm) {
    phoneForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      hideAlert();

      const rawNumber = phoneInput.value.trim().replace(/\D/g, '');
      const countryCode = countryCodeSelect ? countryCodeSelect.value : '+91';

      if (!rawNumber || rawNumber.length < 7) {
        showAlert('Please enter a valid phone number.', 'error');
        phoneInput.focus();
        return;
      }

      const fullPhoneNumber = countryCode.startsWith('+') ? `${countryCode}${rawNumber}` : `+${countryCode}${rawNumber}`;

      // Reset stale session/email data if phone number changed or logging in as another user
      const previousPhone = localStorage.getItem(window.CONFIG?.STORAGE_KEYS?.PHONE_NUMBER);
      if (previousPhone && previousPhone !== fullPhoneNumber) {
        localStorage.removeItem('peculiar_linked_email');
        localStorage.removeItem('peculiar_pending_email');
        localStorage.removeItem(window.CONFIG?.STORAGE_KEYS?.IS_VERIFIED);
      }

      let serverTimer = setTimeout(() => {
        if (sendCodeBtn && sendCodeBtn.disabled) {
          sendCodeBtn.innerHTML = 'Connecting to secure server... (please wait)';
        }
      }, 3000);

      try {
        sendCodeBtn.disabled = true;
        sendCodeBtn.innerHTML = 'Connecting to Telegram...';

        const result = await window.api.startVerification(fullPhoneNumber);
        currentVerificationId = result.verification_id;

        localStorage.setItem(window.CONFIG.STORAGE_KEYS.VERIFICATION_ID, currentVerificationId);
        localStorage.setItem(window.CONFIG.STORAGE_KEYS.PHONE_NUMBER, fullPhoneNumber);

        if (telegramBotLink && result.telegram_url) {
          telegramBotLink.href = result.telegram_url;
        }

        // Transition from Phone Form to OTP Verification Form
        phoneForm.style.display = 'none';
        if (otpStep) {
          otpStep.classList.add('active');
        }
        if (stepTag) {
          stepTag.textContent = 'STEP 2 OF 2 · VERIFICATION';
        }
        if (authSubtitle) {
          authSubtitle.textContent = 'Check your Telegram bot chat for the 6-digit verification code.';
        }

        startCountdown(result.expires_in_seconds || 600);
        showAlert('Verification requested! Open your Telegram bot to get the OTP code.', 'success');
        if (otpInput) otpInput.focus();

      } catch (err) {
        showAlert(err.message || 'Failed to start verification. Please verify your connection.');
      } finally {
        clearTimeout(serverTimer);
        sendCodeBtn.disabled = false;
        sendCodeBtn.textContent = 'Get code on Telegram →';
      }
    });
  }

  // Handle Step 2: Verify OTP
  if (verifyForm) {
    verifyForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      hideAlert();

      const otp = otpInput ? otpInput.value.trim() : '';

      if (!otp || otp.length < 4) {
        showAlert('Please enter the 6-digit code received from the Telegram bot.', 'error');
        if (otpInput) otpInput.focus();
        return;
      }

      if (!currentVerificationId) {
        showAlert('Verification session expired. Please start again.', 'error');
        if (otpStep) otpStep.classList.remove('active');
        if (phoneForm) phoneForm.style.display = 'block';
        return;
      }

      try {
        verifyCodeBtn.disabled = true;
        verifyCodeBtn.innerHTML = 'Verifying Code...';

        const result = await window.api.verifyOtp(currentVerificationId, otp);

        if (result && (result.verified === true || result.status === 'success')) {
          localStorage.setItem(window.CONFIG.STORAGE_KEYS.IS_VERIFIED, 'true');
          showAlert('Verification successful! Taking you to the next step...', 'success');
          
          // Proceed to Page 3: connect.html (One last step. Start the chat.)
          setTimeout(() => {
            window.location.href = 'connect.html';
          }, 800);
        } else {
          showAlert('Invalid verification code. Please check your Telegram message and try again.', 'error');
        }
      } catch (err) {
        showAlert(err.message || 'Verification failed. Make sure you entered the correct code from Telegram.');
      } finally {
        verifyCodeBtn.disabled = false;
        verifyCodeBtn.textContent = 'Verify & continue →';
      }
    });
  }

  // Resend code handler
  if (resendBtn) {
    resendBtn.addEventListener('click', () => {
      if (otpStep) otpStep.classList.remove('active');
      if (phoneForm) {
        phoneForm.style.display = 'block';
        phoneForm.requestSubmit();
      }
    });
  }
});
