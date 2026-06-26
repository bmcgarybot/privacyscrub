/* ============================================
   PrivacyScrub — Walkthrough Engine
   Guided onboarding + contextual help system
   ============================================ */

class Walkthrough {
  constructor(pageKey, steps) {
    this.pageKey = pageKey;
    this.steps = steps;
    this.currentStep = 0;
    this.active = false;
    this.overlay = null;
    this.spotlight = null;
    this.tooltip = null;
    this._keyHandler = this._handleKey.bind(this);
    this._resizeHandler = this._handleResize.bind(this);
  }

  /* --- Storage helpers --- */
  static _storageKey(pageKey) {
    return 'privacyscrub_wt_' + pageKey;
  }

  static isCompleted(pageKey) {
    try { return localStorage.getItem(Walkthrough._storageKey(pageKey)) === 'done'; }
    catch { return false; }
  }

  static markCompleted(pageKey) {
    try { localStorage.setItem(Walkthrough._storageKey(pageKey), 'done'); } catch {}
  }

  static resetAll() {
    try {
      Object.keys(localStorage).forEach(k => {
        if (k.startsWith('privacyscrub_wt_')) localStorage.removeItem(k);
      });
    } catch {}
  }

  /* --- Public API --- */
  start() {
    if (this.active || !this.steps.length) return;
    this.active = true;
    this.currentStep = 0;
    this._createDOM();
    this._showStep();
    document.addEventListener('keydown', this._keyHandler);
    window.addEventListener('resize', this._resizeHandler);
  }

  stop() {
    if (!this.active) return;
    this.active = false;
    this._removeDOM();
    Walkthrough.markCompleted(this.pageKey);
    document.removeEventListener('keydown', this._keyHandler);
    window.removeEventListener('resize', this._resizeHandler);
  }

  next() {
    if (this.currentStep < this.steps.length - 1) {
      this.currentStep++;
      this._showStep();
    } else {
      this.stop();
    }
  }

  prev() {
    if (this.currentStep > 0) {
      this.currentStep--;
      this._showStep();
    }
  }

  /* --- DOM management --- */
  _createDOM() {
    // Overlay background
    this.overlayBg = document.createElement('div');
    this.overlayBg.className = 'wt-overlay-bg';
    this.overlayBg.addEventListener('click', () => this.stop());

    // Spotlight
    this.spotlight = document.createElement('div');
    this.spotlight.className = 'wt-spotlight wt-spotlight-pulse';

    // Tooltip
    this.tooltip = document.createElement('div');
    this.tooltip.className = 'wt-tooltip';

    document.body.appendChild(this.overlayBg);
    document.body.appendChild(this.spotlight);
    document.body.appendChild(this.tooltip);
  }

  _removeDOM() {
    if (this.overlayBg) { this.overlayBg.remove(); this.overlayBg = null; }
    if (this.spotlight) { this.spotlight.remove(); this.spotlight = null; }
    if (this.tooltip) { this.tooltip.remove(); this.tooltip = null; }
  }

  /* --- Step rendering --- */
  _showStep() {
    const step = this.steps[this.currentStep];
    const target = step.target ? document.querySelector(step.target) : null;
    const isWelcome = !step.target;

    // Tooltip class management
    this.tooltip.className = 'wt-tooltip';

    if (isWelcome) {
      // Welcome/centered step — hide spotlight
      this.spotlight.style.display = 'none';
      this.tooltip.classList.add('wt-welcome');
    } else if (target) {
      // Scroll target into view
      target.scrollIntoView({ behavior: 'smooth', block: 'center' });

      // Wait for scroll to finish then position
      setTimeout(() => this._positionOnTarget(target, step.position || 'bottom'), 350);
    } else {
      // Target not found — treat like welcome
      this.spotlight.style.display = 'none';
      this.tooltip.classList.add('wt-welcome');
    }

    // Build tooltip content
    const isLast = this.currentStep === this.steps.length - 1;
    const isFirst = this.currentStep === 0;

    this.tooltip.innerHTML = `
      ${isWelcome && step.icon ? `<span class="wt-welcome-icon">${step.icon}</span>` : ''}
      <div class="wt-step-badge">Step ${this.currentStep + 1} of ${this.steps.length}</div>
      <div class="wt-title">${step.title}</div>
      <div class="wt-description">${step.description}</div>
      <div class="wt-dots">
        ${this.steps.map((_, i) =>
          `<span class="wt-dot ${i === this.currentStep ? 'active' : (i < this.currentStep ? 'completed' : '')}"></span>`
        ).join('')}
      </div>
      <div class="wt-buttons">
        <button class="wt-btn wt-btn-skip" onclick="window.__wt.stop()">Skip tour</button>
        <div class="wt-btn-group">
          ${!isFirst ? '<button class="wt-btn wt-btn-prev" onclick="window.__wt.prev()"><i class="fa-solid fa-chevron-left"></i> Back</button>' : ''}
          ${!isLast
            ? '<button class="wt-btn wt-btn-next" onclick="window.__wt.next()">Next <i class="fa-solid fa-chevron-right"></i></button>'
            : '<button class="wt-btn wt-btn-finish" onclick="window.__wt.stop()"><i class="fa-solid fa-check"></i> Got it!</button>'}
        </div>
      </div>
    `;

    // Make tooltip visible after a small delay for transition
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        this.tooltip.classList.add('visible');
      });
    });
  }

  _positionOnTarget(target, preferredPos) {
    const rect = target.getBoundingClientRect();
    const pad = 8;

    // Position spotlight
    this.spotlight.style.display = 'block';
    this.spotlight.style.top = (rect.top - pad) + 'px';
    this.spotlight.style.left = (rect.left - pad) + 'px';
    this.spotlight.style.width = (rect.width + pad * 2) + 'px';
    this.spotlight.style.height = (rect.height + pad * 2) + 'px';

    // Position tooltip
    const tw = this.tooltip.offsetWidth;
    const th = this.tooltip.offsetHeight;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const gap = 16;

    let top, left;
    let pos = preferredPos;

    // Check if preferred position fits; if not, flip
    if (pos === 'bottom' && rect.bottom + gap + th > vh) pos = 'top';
    if (pos === 'top' && rect.top - gap - th < 0) pos = 'bottom';
    if (pos === 'right' && rect.right + gap + tw > vw) pos = 'left';
    if (pos === 'left' && rect.left - gap - tw < 0) pos = 'right';

    switch (pos) {
      case 'bottom':
        top = rect.bottom + gap;
        left = rect.left + rect.width / 2 - tw / 2;
        break;
      case 'top':
        top = rect.top - th - gap;
        left = rect.left + rect.width / 2 - tw / 2;
        break;
      case 'left':
        top = rect.top + rect.height / 2 - th / 2;
        left = rect.left - tw - gap;
        break;
      case 'right':
        top = rect.top + rect.height / 2 - th / 2;
        left = rect.right + gap;
        break;
    }

    // Clamp to viewport
    left = Math.max(12, Math.min(left, vw - tw - 12));
    top = Math.max(12, Math.min(top, vh - th - 12));

    this.tooltip.classList.add('wt-pos-' + pos);
    this.tooltip.style.top = top + 'px';
    this.tooltip.style.left = left + 'px';
  }

  /* --- Keyboard nav --- */
  _handleKey(e) {
    if (!this.active) return;
    if (e.key === 'Escape') { this.stop(); e.preventDefault(); }
    if (e.key === 'ArrowRight' || e.key === 'Enter') { this.next(); e.preventDefault(); }
    if (e.key === 'ArrowLeft') { this.prev(); e.preventDefault(); }
  }

  /* --- Resize handler --- */
  _handleResize() {
    if (!this.active) return;
    const step = this.steps[this.currentStep];
    if (step.target) {
      const target = document.querySelector(step.target);
      if (target) this._positionOnTarget(target, step.position || 'bottom');
    }
  }
}

/* --- Auto-init helper --- */
function initWalkthrough(pageKey, steps) {
  const wt = new Walkthrough(pageKey, steps);
  window.__wt = wt;

  document.addEventListener('DOMContentLoaded', () => {
    // Auto-launch on first visit
    if (!Walkthrough.isCompleted(pageKey)) {
      setTimeout(() => wt.start(), 600);
    }
  });

  return wt;
}

/* --- Help button click handler --- */
function launchWalkthrough() {
  if (window.__wt) {
    window.__wt.currentStep = 0;
    window.__wt.start();
  }
}
