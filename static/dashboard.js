/* ============================================
   PrivacyScrub — Dashboard JavaScript
   Vanilla JS — No frameworks
   ============================================ */

'use strict';

// ==========================================
// Toast Notification System
// ==========================================
const Toast = {
  container: null,

  init() {
    this.container = document.getElementById('toast-container');
    if (!this.container) {
      this.container = document.createElement('div');
      this.container.id = 'toast-container';
      this.container.className = 'toast-container';
      document.body.appendChild(this.container);
    }
  },

  show(type, title, message, duration = 5000) {
    if (!this.container) this.init();

    const icons = {
      success: 'fa-circle-check',
      error: 'fa-circle-xmark',
      warning: 'fa-triangle-exclamation',
      info: 'fa-circle-info'
    };

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
      <i class="fa-solid ${icons[type] || icons.info} toast-icon"></i>
      <div class="toast-content">
        <div class="toast-title">${title}</div>
        ${message ? `<div class="toast-message">${message}</div>` : ''}
      </div>
      <button class="toast-close" onclick="Toast.dismiss(this.parentElement)">
        <i class="fa-solid fa-xmark"></i>
      </button>
    `;

    this.container.appendChild(toast);

    if (duration > 0) {
      setTimeout(() => this.dismiss(toast), duration);
    }

    return toast;
  },

  dismiss(toast) {
    if (!toast || toast.classList.contains('removing')) return;
    toast.classList.add('removing');
    setTimeout(() => toast.remove(), 400);
  },

  success(title, message) { return this.show('success', title, message); },
  error(title, message) { return this.show('error', title, message); },
  warning(title, message) { return this.show('warning', title, message); },
  info(title, message) { return this.show('info', title, message); }
};

// ==========================================
// Modal System
// ==========================================
const Modal = {
  open(modalId) {
    const overlay = document.getElementById(modalId);
    if (overlay) {
      overlay.classList.add('active');
      document.body.style.overflow = 'hidden';
    }
  },

  close(modalId) {
    const overlay = document.getElementById(modalId);
    if (overlay) {
      overlay.classList.remove('active');
      document.body.style.overflow = '';
    }
  },

  closeAll() {
    document.querySelectorAll('.modal-overlay.active').forEach(m => {
      m.classList.remove('active');
    });
    document.body.style.overflow = '';
  },

  confirm(title, message, onConfirm, onCancel) {
    const id = 'modal-confirm-' + Date.now();
    const overlay = document.createElement('div');
    overlay.id = id;
    overlay.className = 'modal-overlay active';
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-header">
          <h3>${title}</h3>
          <button class="modal-close" onclick="Modal.close('${id}'); this.closest('.modal-overlay').remove();">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
        <div class="modal-body">
          <p>${message}</p>
        </div>
        <div class="modal-footer">
          <button class="btn btn-ghost" id="${id}-cancel">Cancel</button>
          <button class="btn btn-primary" id="${id}-confirm">Confirm</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    document.body.style.overflow = 'hidden';

    overlay.querySelector(`#${id}-cancel`).addEventListener('click', () => {
      overlay.remove();
      document.body.style.overflow = '';
      if (onCancel) onCancel();
    });

    overlay.querySelector(`#${id}-confirm`).addEventListener('click', () => {
      overlay.remove();
      document.body.style.overflow = '';
      if (onConfirm) onConfirm();
    });

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) {
        overlay.remove();
        document.body.style.overflow = '';
        if (onCancel) onCancel();
      }
    });
  }
};

// ==========================================
// Tab System
// ==========================================
const Tabs = {
  init() {
    document.querySelectorAll('[data-tab-group]').forEach(group => {
      const groupName = group.dataset.tabGroup;
      const tabs = group.querySelectorAll('.tab');
      tabs.forEach(tab => {
        tab.addEventListener('click', () => {
          const target = tab.dataset.tab;
          this.switchTo(groupName, target);
        });
      });
    });
  },

  switchTo(groupName, tabName) {
    const group = document.querySelector(`[data-tab-group="${groupName}"]`);
    if (!group) return;

    group.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    const activeTab = group.querySelector(`[data-tab="${tabName}"]`);
    if (activeTab) activeTab.classList.add('active');

    document.querySelectorAll(`[data-tab-content="${groupName}"]`).forEach(content => {
      content.classList.remove('active');
    });
    const activeContent = document.getElementById(`tab-${groupName}-${tabName}`);
    if (activeContent) activeContent.classList.add('active');
  }
};

// ==========================================
// Privacy Score Gauge
// ==========================================
const ScoreGauge = {
  animate(elementId, score, maxScore = 100) {
    const gauge = document.getElementById(elementId);
    if (!gauge) return;

    const fill = gauge.querySelector('.gauge-fill');
    const valueEl = gauge.querySelector('.gauge-value');
    if (!fill || !valueEl) return;

    const circumference = 2 * Math.PI * 100; // r=100
    const percentage = Math.min(score / maxScore, 1);
    const offset = circumference * (1 - percentage);

    // Set color class based on score
    fill.classList.remove('score-low', 'score-mid', 'score-high');
    if (score < 40) {
      fill.classList.add('score-low');
    } else if (score < 70) {
      fill.classList.add('score-mid');
    } else {
      fill.classList.add('score-high');
    }

    // Animate
    requestAnimationFrame(() => {
      fill.style.strokeDashoffset = offset;
    });

    // Counter animation
    this.animateCounter(valueEl, 0, score, 2000);
  },

  animateCounter(element, start, end, duration) {
    const startTime = performance.now();
    const range = end - start;

    function update(currentTime) {
      const elapsed = currentTime - startTime;
      const progress = Math.min(elapsed / duration, 1);
      // Ease out cubic
      const eased = 1 - Math.pow(1 - progress, 3);
      const current = Math.round(start + range * eased);
      element.textContent = current;

      if (progress < 1) {
        requestAnimationFrame(update);
      }
    }

    requestAnimationFrame(update);
  }
};

// ==========================================
// Chart.js Initialization
// ==========================================
const Charts = {
  instances: {},

  defaults() {
    if (typeof Chart === 'undefined') return;

    Chart.defaults.color = '#a0a0b0';
    Chart.defaults.borderColor = 'rgba(255, 255, 255, 0.06)';
    Chart.defaults.font.family = "'Inter', -apple-system, BlinkMacSystemFont, sans-serif";
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.plugins.legend.labels.padding = 16;
    Chart.defaults.plugins.tooltip.backgroundColor = '#16213e';
    Chart.defaults.plugins.tooltip.borderColor = 'rgba(255, 255, 255, 0.1)';
    Chart.defaults.plugins.tooltip.borderWidth = 1;
    Chart.defaults.plugins.tooltip.cornerRadius = 8;
    Chart.defaults.plugins.tooltip.padding = 12;
  },

  threatTimeline(canvasId, data) {
    if (typeof Chart === 'undefined') return;
    this.defaults();

    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    if (this.instances[canvasId]) this.instances[canvasId].destroy();

    const labels = data?.labels || ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const exposures = data?.exposures || [12, 15, 18, 14, 22, 19, 16, 20, 24, 18, 15, 13];
    const removals = data?.removals || [0, 2, 5, 8, 10, 14, 16, 18, 21, 24, 26, 28];

    this.instances[canvasId] = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: 'Exposures Found',
            data: exposures,
            borderColor: '#ff6b6b',
            backgroundColor: 'rgba(255, 107, 107, 0.1)',
            fill: true,
            tension: 0.4,
            pointRadius: 4,
            pointHoverRadius: 6,
            borderWidth: 2
          },
          {
            label: 'Removals Completed',
            data: removals,
            borderColor: '#00d4aa',
            backgroundColor: 'rgba(0, 212, 170, 0.1)',
            fill: true,
            tension: 0.4,
            pointRadius: 4,
            pointHoverRadius: 6,
            borderWidth: 2
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { position: 'top' }
        },
        scales: {
          y: {
            beginAtZero: true,
            grid: { color: 'rgba(255, 255, 255, 0.04)' },
            ticks: { stepSize: 5 }
          },
          x: {
            grid: { display: false }
          }
        }
      }
    });
  },

  categoryBreakdown(canvasId, data) {
    if (typeof Chart === 'undefined') return;
    this.defaults();

    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    if (this.instances[canvasId]) this.instances[canvasId].destroy();

    const labels = data?.labels || ['People Search', 'Background Check', 'B2B/Lead Gen', 'Marketing', 'Financial', 'Real Estate', 'Social'];
    const values = data?.values || [48, 25, 35, 62, 12, 15, 18];
    const colors = ['#ff6b6b', '#ffd93d', '#4ecdc4', '#00d4aa', '#ff8a5c', '#9b59b6', '#3498db'];

    this.instances[canvasId] = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels,
        datasets: [{
          data: values,
          backgroundColor: colors.map(c => c + '33'),
          borderColor: colors,
          borderWidth: 2,
          hoverOffset: 8
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '65%',
        plugins: {
          legend: {
            position: 'right',
            labels: { padding: 12, font: { size: 12 } }
          }
        }
      }
    });
  },

  familyComparison(canvasId, data) {
    if (typeof Chart === 'undefined') return;
    this.defaults();

    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    if (this.instances[canvasId]) this.instances[canvasId].destroy();

    const labels = data?.labels || ['You', 'Spouse', 'Child 1', 'Child 2'];
    const scores = data?.scores || [72, 65, 88, 91];

    this.instances[canvasId] = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'Privacy Score',
          data: scores,
          backgroundColor: scores.map(s => s >= 70 ? 'rgba(0, 212, 170, 0.6)' : s >= 40 ? 'rgba(255, 217, 61, 0.6)' : 'rgba(255, 107, 107, 0.6)'),
          borderColor: scores.map(s => s >= 70 ? '#00d4aa' : s >= 40 ? '#ffd93d' : '#ff6b6b'),
          borderWidth: 2,
          borderRadius: 6,
          maxBarThickness: 60
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false }
        },
        scales: {
          y: {
            beginAtZero: true,
            max: 100,
            grid: { color: 'rgba(255, 255, 255, 0.04)' },
            ticks: { callback: v => v + '%' }
          },
          x: {
            grid: { display: false }
          }
        }
      }
    });
  },

  breachTimeline(canvasId, data) {
    if (typeof Chart === 'undefined') return;
    this.defaults();

    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    if (this.instances[canvasId]) this.instances[canvasId].destroy();

    const labels = data?.labels || ['2019', '2020', '2021', '2022', '2023', '2024'];
    const values = data?.values || [2, 1, 3, 2, 4, 1];

    this.instances[canvasId] = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'Breaches',
          data: values,
          backgroundColor: 'rgba(255, 107, 107, 0.5)',
          borderColor: '#ff6b6b',
          borderWidth: 2,
          borderRadius: 6
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: {
            beginAtZero: true,
            ticks: { stepSize: 1 },
            grid: { color: 'rgba(255, 255, 255, 0.04)' }
          },
          x: { grid: { display: false } }
        }
      }
    });
  }
};

// ==========================================
// API Fetch Helper
// ==========================================
const API = {
  async request(url, options = {}) {
    const defaults = {
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin'
    };

    const config = { ...defaults, ...options };
    if (config.body && typeof config.body === 'object') {
      config.body = JSON.stringify(config.body);
    }

    try {
      const response = await fetch(url, config);
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || `Request failed (${response.status})`);
      }

      return data;
    } catch (err) {
      Toast.error('Request Failed', err.message);
      throw err;
    }
  },

  get(url) { return this.request(url); },
  post(url, body) { return this.request(url, { method: 'POST', body }); },
  put(url, body) { return this.request(url, { method: 'PUT', body }); },
  delete(url) { return this.request(url, { method: 'DELETE' }); }
};

// ==========================================
// Form Submission Handler
// ==========================================
const Forms = {
  init() {
    document.querySelectorAll('form[data-ajax]').forEach(form => {
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const url = form.action || form.dataset.url;
        const method = (form.method && form.method.toUpperCase() !== 'GET') ? form.method : 'POST';
        const submitBtn = form.querySelector('[type="submit"]');
        const originalText = submitBtn ? submitBtn.innerHTML : '';

        try {
          if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<span class="spinner"></span> Processing...';
          }

          const formData = new FormData(form);
          const data = Object.fromEntries(formData.entries());

          const result = await API.request(url, { method: method.toUpperCase(), body: data });

          Toast.success('Success', result.message || 'Operation completed successfully');

          if (form.dataset.onSuccess === 'reload') {
            setTimeout(() => location.reload(), 1000);
          } else if (form.dataset.onSuccess === 'reset') {
            form.reset();
          } else if (form.dataset.redirect) {
            setTimeout(() => location.href = form.dataset.redirect, 1000);
          }
        } catch (err) {
          // Error already handled by API
        } finally {
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalText;
          }
        }
      });
    });
  }
};

// ==========================================
// Scan Progress Polling
// ==========================================
const ScanProgress = {
  intervalId: null,
  progressBar: null,
  statusText: null,

  start(scanId) {
    this.progressBar = document.getElementById('scan-progress-fill');
    this.statusText = document.getElementById('scan-status-text');

    const container = document.getElementById('scan-progress-container');
    if (container) container.style.display = 'block';

    const startBtn = document.getElementById('start-scan-btn');
    if (startBtn) startBtn.disabled = true;

    this.poll(scanId);
    this.intervalId = setInterval(() => this.poll(scanId), 3000);
  },

  async poll(scanId) {
    try {
      const data = await API.get(`/api/scan/${scanId || 'latest'}/status`);

      if (this.progressBar) {
        this.progressBar.style.width = `${data.progress || 0}%`;
        this.progressBar.classList.add('animated');
      }

      if (this.statusText) {
        this.statusText.textContent = data.status_text || `Scanning... ${data.progress || 0}%`;
      }

      const brokerCount = document.getElementById('scan-broker-count');
      if (brokerCount) brokerCount.textContent = data.brokers_checked || 0;

      const foundCount = document.getElementById('scan-found-count');
      if (foundCount) foundCount.textContent = data.found || 0;

      if (data.status === 'complete' || data.progress >= 100) {
        this.stop();
        Toast.success('Scan Complete', `Found ${data.found || 0} exposures across ${data.brokers_checked || 0} brokers`);

        const startBtn = document.getElementById('start-scan-btn');
        if (startBtn) {
          startBtn.disabled = false;
          startBtn.innerHTML = '<i class="fa-solid fa-magnifying-glass"></i> Run New Scan';
        }

        // Reload so the freshly saved results render in the table
        setTimeout(() => location.reload(), 2000);
      }
    } catch (err) {
      this.stop();
      Toast.error('Scan Status Lost', 'Could not reach the scan status endpoint. Reload the page to see results.');
    }
  },

  stop() {
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = null;
    }
  }
};

// ==========================================
// Table Search / Filter
// ==========================================
const TableFilter = {
  init() {
    document.querySelectorAll('[data-table-search]').forEach(input => {
      const tableId = input.dataset.tableSearch;
      input.addEventListener('input', () => {
        this.search(tableId, input.value);
      });
    });

    document.querySelectorAll('[data-table-filter]').forEach(select => {
      const tableId = select.dataset.tableFilter;
      const column = select.dataset.filterColumn;
      select.addEventListener('change', () => {
        this.filter(tableId, column, select.value);
      });
    });
  },

  search(tableId, query) {
    const table = document.getElementById(tableId);
    if (!table) return;

    const rows = table.querySelectorAll('tbody tr');
    const q = query.toLowerCase().trim();

    rows.forEach(row => {
      const text = row.textContent.toLowerCase();
      row.style.display = !q || text.includes(q) ? '' : 'none';
    });

    this.updateEmptyState(table);
  },

  filter(tableId, column, value) {
    const table = document.getElementById(tableId);
    if (!table) return;

    const rows = table.querySelectorAll('tbody tr');
    const colIndex = parseInt(column);

    rows.forEach(row => {
      if (!value) {
        row.style.display = '';
        return;
      }
      const cell = row.cells[colIndex];
      const cellText = cell ? cell.textContent.toLowerCase().trim() : '';
      row.style.display = cellText.includes(value.toLowerCase()) ? '' : 'none';
    });

    this.updateEmptyState(table);
  },

  updateEmptyState(table) {
    const visibleRows = table.querySelectorAll('tbody tr:not([style*="display: none"])');
    let emptyRow = table.querySelector('.empty-row');

    if (visibleRows.length === 0) {
      if (!emptyRow) {
        emptyRow = document.createElement('tr');
        emptyRow.className = 'empty-row';
        const colCount = table.querySelectorAll('thead th').length;
        emptyRow.innerHTML = `<td colspan="${colCount}" class="text-center text-muted p-3">No matching results</td>`;
        table.querySelector('tbody').appendChild(emptyRow);
      }
      emptyRow.style.display = '';
    } else if (emptyRow) {
      emptyRow.style.display = 'none';
    }
  }
};

// ==========================================
// Expandable Sections
// ==========================================
const Expandable = {
  init() {
    document.querySelectorAll('.expandable-header').forEach(header => {
      header.addEventListener('click', () => {
        const parent = header.closest('.expandable');
        if (parent) parent.classList.toggle('open');
      });
    });
  }
};

// ==========================================
// Displacement Mode
// ==========================================
const DisplacementMode = {
  isActive: false,

  toggle() {
    if (this.isActive) {
      Modal.confirm(
        'Deactivate Lockdown Mode',
        'Are you sure you want to deactivate emergency privacy lockdown? All accelerated protections will return to normal schedules.',
        () => this.deactivate(),
        null
      );
    } else {
      Modal.confirm(
        '⚠️ Activate Emergency Lockdown',
        'This will initiate emergency privacy lockdown mode. Credit freezes will be recommended, address suppression accelerated, and all pending removals prioritized. This is designed for evacuees, domestic violence survivors, or anyone in immediate need of privacy protection.',
        () => this.activate(),
        null
      );
    }
  },

  async activate() {
    try {
      await API.post('/api/displacement/activate');
      this.isActive = true;
      this.updateUI(true);
      Toast.warning('Lockdown Activated', 'Emergency privacy lockdown is now active');
    } catch (err) {
      // NEVER simulate success here. Someone in danger must not be told
      // lockdown is active when it is not.
      Toast.error('Lockdown Failed', err.message || 'Could not activate lockdown — please retry');
    }
  },

  async deactivate() {
    try {
      await API.post('/api/displacement/deactivate');
      this.isActive = false;
      this.updateUI(false);
      Toast.success('Lockdown Deactivated', 'Emergency mode has been deactivated');
    } catch (err) {
      Toast.error('Deactivation Failed', err.message || 'Could not deactivate lockdown — please retry');
    }
  },

  updateUI(active) {
    const btn = document.getElementById('lockdown-toggle-btn');
    const banner = document.getElementById('displacement-banner');
    const checklist = document.getElementById('emergency-checklist');
    const navItem = document.querySelector('.nav-item[href="/displacement"]');

    if (btn) {
      if (active) {
        btn.className = 'btn btn-warning btn-lg lockdown-btn';
        btn.innerHTML = '<i class="fa-solid fa-shield-halved"></i> Deactivate Lockdown';
      } else {
        btn.className = 'btn btn-danger btn-lg lockdown-btn';
        btn.innerHTML = '<i class="fa-solid fa-shield-virus"></i> Activate Emergency Lockdown';
      }
    }

    if (banner) banner.style.display = active ? 'flex' : 'none';
    if (checklist) checklist.style.display = active ? 'flex' : 'none';
    if (navItem) navItem.classList.toggle('displacement-active', active);
  }
};

// ==========================================
// Copy to Clipboard
// ==========================================
function copyToClipboard(text, btn) {
  navigator.clipboard.writeText(text).then(() => {
    const originalText = btn ? btn.innerHTML : '';
    if (btn) {
      btn.innerHTML = '<i class="fa-solid fa-check"></i> Copied!';
      setTimeout(() => { btn.innerHTML = originalText; }, 2000);
    }
    Toast.success('Copied', 'Text copied to clipboard');
  }).catch(() => {
    Toast.error('Copy Failed', 'Could not copy to clipboard');
  });
}

function copyCodeBlock(blockId) {
  const block = document.getElementById(blockId);
  if (block) {
    const code = block.querySelector('code') || block;
    copyToClipboard(code.textContent);
  }
}

// ==========================================
// Sidebar Toggle (Mobile)
// ==========================================
const Sidebar = {
  init() {
    const toggle = document.getElementById('menu-toggle');
    const sidebar = document.getElementById('sidebar');

    if (toggle && sidebar) {
      toggle.addEventListener('click', () => {
        sidebar.classList.toggle('open');
      });

      // Close on outside click
      document.addEventListener('click', (e) => {
        if (sidebar.classList.contains('open') &&
            !sidebar.contains(e.target) &&
            !toggle.contains(e.target)) {
          sidebar.classList.remove('open');
        }
      });
    }
  }
};

// ==========================================
// Scan Actions
// ==========================================
async function startScan(profileId) {
  // On the scanner page (progress UI present): start the scan via the API
  // and poll live progress. Elsewhere: navigate to the scanner page, which
  // auto-starts the scan on arrival.
  const onScannerPage = !!document.getElementById('scan-progress-container');

  if (!onScannerPage) {
    window.location.href = profileId
      ? '/scanner?profile_id=' + profileId + '&auto_start=1'
      : '/scanner?auto_start=1';
    return;
  }

  if (!profileId) {
    Toast.warning('Profile Required', 'Select a profile to scan');
    return;
  }

  try {
    const res = await fetch('/api/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ profile_id: parseInt(profileId, 10) })
    });
    const data = await res.json();

    if (res.status === 409 && data.batch_id) {
      // A scan is already running — resume watching it.
      Toast.info('Scan In Progress', 'Resuming live progress for the running scan');
      ScanProgress.start(data.batch_id);
      return;
    }
    if (!res.ok) {
      throw new Error(data.error || data.message || `Request failed (${res.status})`);
    }

    Toast.success('Scan Started', 'Checking brokers — results appear live below');
    ScanProgress.start(data.batch_id);
  } catch (err) {
    Toast.error('Scan Failed', err.message);
  }
}

// Auto-start / resume scans on the scanner page
document.addEventListener('DOMContentLoaded', () => {
  const container = document.getElementById('scan-progress-container');
  if (!container) return;

  // Resume a scan that's already running server-side (e.g. after reload)
  if (container.dataset.activeBatch && container.dataset.activeStatus === 'running') {
    ScanProgress.start(container.dataset.activeBatch);
    return;
  }

  // Honor ?auto_start=1 from dashboard "Run Scan" buttons
  const params = new URLSearchParams(window.location.search);
  if (params.get('auto_start') === '1') {
    const select = document.getElementById('scan-profile-select');
    const profileId = params.get('profile_id') || (select ? select.value : '');
    if (profileId) startScan(profileId);
  }
});

// ==========================================
// Opt-Out Actions
// ==========================================
async function submitOptOut(brokerId, profileId) {
  if (!profileId) {
    Toast.warning('Profile Required', 'Select a profile before submitting an opt-out');
    return;
  }
  try {
    const data = await API.post(`/api/optout/${brokerId}/submit`, { profile_id: profileId });
    if (data.submitted) {
      const suffix = data.draft ? ' (saved as draft — SMTP not configured)' : '';
      Toast.success('Opt-Out Submitted', `Removal request sent to ${data.broker_name || 'broker'}${suffix}`);
      setTimeout(() => location.reload(), 1500);
    } else if (data.manual_required) {
      Toast.warning('Manual Opt-Out Required',
        `${data.broker_name || 'This broker'} has no automated path. Opening their opt-out page…`);
      if (data.opt_out_url) {
        setTimeout(() => window.open(data.opt_out_url, '_blank'), 800);
      }
      setTimeout(() => location.reload(), 2500);
    } else {
      Toast.error('Opt-Out Failed', data.message || 'Submission did not complete');
    }
  } catch (err) {
    // Error toast already shown by API helper — do not fake success.
  }
}

async function batchOptOut(optoutIds) {
  try {
    const data = await API.post('/api/optout/batch', { optout_ids: optoutIds });
    const parts = [`${data.submitted || 0} submitted`];
    if (data.drafts) parts.push(`${data.drafts} as drafts`);
    if (data.manual_required) parts.push(`${data.manual_required} need manual action`);
    if (data.failed) parts.push(`${data.failed} failed`);
    const toast = (data.failed && !data.submitted) ? Toast.error : Toast.success;
    toast.call(Toast, 'Batch Opt-Out', parts.join(', '));
    setTimeout(() => location.reload(), 2000);
  } catch (err) {
    // Error toast already shown by API helper — do not fake success.
  }
}

function selectAllOptOuts() {
  const checkboxes = document.querySelectorAll('.optout-checkbox');
  const allChecked = Array.from(checkboxes).every(cb => cb.checked);
  checkboxes.forEach(cb => cb.checked = !allChecked);
  updateBatchActions();
}

function updateBatchActions() {
  const checked = document.querySelectorAll('.optout-checkbox:checked');
  const batchBar = document.getElementById('batch-actions');
  if (batchBar) {
    batchBar.style.display = checked.length > 0 ? 'flex' : 'none';
    const countEl = batchBar.querySelector('.batch-count');
    if (countEl) countEl.textContent = checked.length;
  }
}

function batchSubmitSelected() {
  const ids = Array.from(document.querySelectorAll('.optout-checkbox:checked'))
    .map(cb => cb.value);
  if (ids.length > 0) {
    Modal.confirm(
      'Submit Batch Opt-Outs',
      `Submit removal requests for ${ids.length} selected brokers?`,
      () => batchOptOut(ids)
    );
  }
}

// ==========================================
// Legal Letter Generator
// ==========================================
const LegalGenerator = {
  generate(type) {
    const profileSelect = document.getElementById('legal-profile-select');
    const brokerSelect = document.getElementById(`${type}-broker-select`);
    const preview = document.getElementById(`${type}-preview`);

    const profileName = profileSelect ? profileSelect.options[profileSelect.selectedIndex]?.text : 'John Doe';
    const brokerName = brokerSelect ? brokerSelect.options[brokerSelect.selectedIndex]?.text : 'Data Broker Inc.';

    const templates = {
      gdpr: this.gdprTemplate(profileName, brokerName),
      ccpa: this.ccpaTemplate(profileName, brokerName),
      state: this.stateTemplate(profileName, brokerName),
      cease: this.ceaseTemplate(profileName, brokerName)
    };

    if (preview) {
      preview.innerHTML = templates[type] || '<p>Select options to generate letter</p>';
    }
  },

  gdprTemplate(name, broker) {
    const date = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
    return `<div class="letter-date">${date}</div>
<div class="letter-recipient"><strong>To: ${broker}</strong><br>Data Protection Officer</div>
<p>Dear Data Protection Officer,</p>
<p>I am writing to exercise my right to erasure (right to be forgotten) as provided under Article 17 of the General Data Protection Regulation (GDPR).</p>
<p>I request that you erase all personal data you hold about me, <strong>${name}</strong>, without undue delay. This includes but is not limited to: names, addresses, phone numbers, email addresses, employment information, social profiles, and any other personally identifiable information.</p>
<p>Under Article 12(3) of GDPR, you are required to respond to this request within one month.</p>
<p>If you do not comply with this request within the required timeframe, I reserve the right to lodge a complaint with the relevant supervisory authority.</p>
<p>Sincerely,<br><strong>${name}</strong></p>`;
  },

  ccpaTemplate(name, broker) {
    const date = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
    return `<div class="letter-date">${date}</div>
<div class="letter-recipient"><strong>To: ${broker}</strong><br>Privacy Department</div>
<p>Dear Privacy Team,</p>
<p>Pursuant to the California Consumer Privacy Act (CCPA), Cal. Civ. Code § 1798.105, I am requesting that you delete all personal information you have collected about me, <strong>${name}</strong>.</p>
<p>Please confirm within 45 days that my data has been deleted and that you have directed any service providers to delete my data as well.</p>
<p>I also request under § 1798.120 that you do not sell my personal information to third parties.</p>
<p>Please confirm receipt of this request and your compliance at your earliest convenience.</p>
<p>Sincerely,<br><strong>${name}</strong></p>`;
  },

  stateTemplate(name, broker) {
    const date = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
    return `<div class="letter-date">${date}</div>
<div class="letter-recipient"><strong>To: ${broker}</strong></div>
<p>Dear Sir/Madam,</p>
<p>I am writing to exercise my right to delete personal data under applicable state privacy laws, including but not limited to the Virginia CDPA, Colorado CPA, Connecticut DPA, and other state privacy statutes.</p>
<p>I request that all personal information pertaining to <strong>${name}</strong> be permanently deleted from your databases and any third-party systems you have shared it with.</p>
<p>Please confirm deletion within the timeframe required by applicable law.</p>
<p>Sincerely,<br><strong>${name}</strong></p>`;
  },

  ceaseTemplate(name, broker) {
    const date = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
    return `<div class="letter-date">${date}</div>
<div class="letter-recipient"><strong>To: ${broker}</strong><br>Legal Department</div>
<p>Dear Legal Department,</p>
<p><strong>RE: CEASE AND DESIST — Unauthorized Publication of Personal Information</strong></p>
<p>I, <strong>${name}</strong>, hereby demand that you immediately cease and desist from publishing, distributing, or selling my personal information on your website and through your services.</p>
<p>Your continued publication of my personal information without my consent constitutes an invasion of privacy. I demand that you:</p>
<ol>
<li>Remove all personal information about me from your databases and website within 72 hours</li>
<li>Confirm in writing that my data has been removed</li>
<li>Take steps to prevent my data from reappearing on your platform</li>
</ol>
<p>Failure to comply may result in legal action. This letter constitutes formal notice.</p>
<p>Sincerely,<br><strong>${name}</strong></p>`;
  }
};

// ==========================================
// Breach Checker
// ==========================================
async function checkBreaches(email) {
  const btn = document.getElementById('check-breach-btn');
  const results = document.getElementById('breach-results');

  if (!email) {
    Toast.warning('Input Required', 'Please enter an email address');
    return;
  }

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Checking...';
  }

  try {
    const data = await API.post('/api/breaches/check', { email });
    if (results) {
      renderBreachResults(results, data.breaches || []);
    }
  } catch (err) {
    // Never show fabricated breach data. Surface the real reason instead
    // (most commonly: no HIBP API key configured in Settings).
    if (results) {
      results.innerHTML = `
        <div class="empty-state">
          <i class="fa-solid fa-triangle-exclamation"></i>
          <h3>Breach Check Unavailable</h3>
          <p>${err.message || 'The breach check could not be completed.'}</p>
        </div>`;
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<i class="fa-solid fa-magnifying-glass"></i> Check Breaches';
    }
  }
}

function renderBreachResults(container, breaches) {
  if (breaches.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <i class="fa-solid fa-shield-check"></i>
        <h3>No Breaches Found</h3>
        <p>This email was not found in any known data breaches.</p>
      </div>`;
    return;
  }

  container.innerHTML = breaches.map(b => `
    <div class="activity-item">
      <div class="activity-icon breach"><i class="fa-solid fa-skull-crossbones"></i></div>
      <div class="activity-content">
        <div class="activity-title">${b.name} <span class="badge severity-${b.severity}">${b.severity}</span></div>
        <div class="activity-meta">
          Breached: ${b.date} · ${b.records} records · Exposed: ${b.data_types.join(', ')}
        </div>
      </div>
    </div>
  `).join('');
}

async function checkPassword() {
  const input = document.getElementById('password-check-input');
  const result = document.getElementById('password-result');
  if (!input || !input.value) {
    Toast.warning('Input Required', 'Please enter a password to check');
    return;
  }

  // Hash with SHA-1 (k-anonymity approach)
  const encoder = new TextEncoder();
  const data = encoder.encode(input.value);
  const hashBuffer = await crypto.subtle.digest('SHA-1', data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  const hash = hashArray.map(b => b.toString(16).padStart(2, '0')).join('').toUpperCase();

  const prefix = hash.substring(0, 5);
  const suffix = hash.substring(5);

  try {
    const response = await fetch(`https://api.pwnedpasswords.com/range/${prefix}`);
    const text = await response.text();
    const found = text.split('\n').find(line => line.startsWith(suffix));

    if (result) {
      if (found) {
        const count = found.split(':')[1].trim();
        result.innerHTML = `<div class="badge danger"><i class="fa-solid fa-triangle-exclamation"></i> Compromised! Found ${parseInt(count).toLocaleString()} times in data breaches</div>`;
        result.className = 'mt-2';
      } else {
        result.innerHTML = `<div class="badge success"><i class="fa-solid fa-circle-check"></i> Not found in any known data breaches</div>`;
        result.className = 'mt-2';
      }
    }
  } catch (err) {
    Toast.error('Check Failed', 'Could not reach the password check service');
  }

  input.value = '';
}

// ==========================================
// Report Generation
// ==========================================
async function generateReport(type) {
  const btn = event.target.closest('.btn');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Generating...';
  }

  try {
    const formatSelect = document.getElementById('report-format');
    const format = formatSelect ? formatSelect.value : 'pdf';
    const profileSelect = document.getElementById('report-profile');
    const profileId = profileSelect ? parseInt(profileSelect.value, 10) : null;

    if (!profileId) {
      Toast.warning('Profile Required', 'Select a profile to generate a report for');
      return;
    }

    const data = await API.post('/api/reports/generate', {
      type: type,
      format: format,
      profile_id: profileId,
      date_from: document.getElementById('report-date-from')?.value,
      date_to: document.getElementById('report-date-to')?.value
    });

    if (data.download_url) {
      Toast.success('Report Ready', 'Your download is starting');
      window.location.href = data.download_url;
    } else {
      Toast.error('Report Failed', 'No download was produced');
    }
  } catch (err) {
    // Error toast already shown by API helper — do not fake success.
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = btn.dataset.originalText || 'Generate';
    }
  }
}

// ==========================================
// Settings
// ==========================================
async function saveSettings(section) {
  const form = document.getElementById(`settings-${section}-form`);
  if (!form) return;

  const formData = new FormData(form);
  const data = Object.fromEntries(formData.entries());

  try {
    await API.post(`/api/settings/${section}`, data);
    Toast.success('Settings Saved', `${section.charAt(0).toUpperCase() + section.slice(1)} settings updated`);
  } catch (err) {
    // Error toast already shown by API helper — do not fake success.
  }
}

async function exportAllData() {
  Modal.confirm(
    'Export All Data',
    'This will export all your data including profiles, scan results, and opt-out history as a JSON file.',
    () => {
      window.location.href = '/api/data/export';
      Toast.info('Export Started', 'Your data export will download shortly');
    }
  );
}

async function importData(input) {
  const file = input.files && input.files[0];
  input.value = ''; // allow re-selecting the same file later
  if (!file) return;

  let backup;
  try {
    backup = JSON.parse(await file.text());
  } catch (err) {
    Toast.error('Import Failed', 'That file is not valid JSON');
    return;
  }
  if (!backup || !Array.isArray(backup.profiles)) {
    Toast.error('Import Failed', 'Not a PrivacyScrub backup (missing profiles list)');
    return;
  }

  try {
    const data = await API.post('/api/data/import', backup);
    const c = data.imported || {};
    Toast.success('Import Complete',
      `${c.profiles || 0} profile(s), ${c.scan_results || 0} scan results, ` +
      `${c.optouts || 0} opt-outs, ${c.breaches || 0} breaches imported` +
      (c.skipped ? ` (${c.skipped} skipped)` : ''));
    setTimeout(() => location.reload(), 2000);
  } catch (err) {
    // Error toast already shown by API helper.
  }
}

async function deleteAllData() {
  // Typed confirmation — matches the safety bar of the settings form flow.
  const typed = prompt(
    '⚠️ This permanently deletes ALL profiles, scan history, and opt-out records.\n\n' +
    'Type DELETE (in capitals) to confirm:'
  );
  if (typed === null) return; // cancelled
  if (typed !== 'DELETE') {
    Toast.warning('Not Deleted', 'Confirmation text did not match — nothing was removed');
    return;
  }
  try {
    await API.request('/api/data/all', { method: 'DELETE', body: { confirm: 'DELETE' } });
    Toast.success('Data Deleted', 'All data has been permanently removed');
    setTimeout(() => location.href = '/', 2000);
  } catch (err) {
    // Error toast already shown by API helper.
  }
}

// ==========================================
// Account Cleanup - Service Search
// ==========================================
function searchServices(query) {
  const container = document.getElementById('services-list');
  if (!container) return;

  const items = container.querySelectorAll('.action-card');
  const q = query.toLowerCase().trim();

  items.forEach(item => {
    const text = item.textContent.toLowerCase();
    item.style.display = !q || text.includes(q) ? '' : 'none';
  });
}

function generateDeleteEmail(serviceName, supportEmail) {
  const template = `Subject: Account Deletion Request

Dear ${serviceName} Support,

I am writing to request the immediate and permanent deletion of my account and all associated data on your platform.

Please confirm that:
1. My account has been deleted
2. All personal data has been permanently erased
3. My information will not be retained for marketing or any other purposes

Please process this request within 30 days as required by applicable data protection regulations.

Thank you for your prompt attention to this matter.

Best regards`;

  const preview = document.getElementById('delete-email-preview');
  if (preview) {
    preview.textContent = template;
    preview.closest('.card').style.display = 'block';
  }

  if (supportEmail) {
    window.open(`mailto:${supportEmail}?subject=Account%20Deletion%20Request&body=${encodeURIComponent(template)}`);
  }
}

// ==========================================
// Endpoint Doc Toggle
// ==========================================
function toggleEndpoint(id) {
  const body = document.getElementById(id);
  if (body) {
    body.style.display = body.style.display === 'none' ? 'block' : 'none';
  }
}

// ==========================================
// Profile Management
// ==========================================
function addDynamicField(containerId, placeholder) {
  const container = document.getElementById(containerId);
  if (!container) return;

  const wrapper = document.createElement('div');
  wrapper.className = 'form-inline mt-1';
  wrapper.innerHTML = `
    <div class="form-group">
      <input type="text" class="form-control" name="${containerId}[]" placeholder="${placeholder}">
    </div>
    <button type="button" class="btn btn-ghost btn-icon" onclick="this.parentElement.remove()">
      <i class="fa-solid fa-trash"></i>
    </button>
  `;
  container.appendChild(wrapper);
}

// ==========================================
// Initialization
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
  Toast.init();
  Sidebar.init();
  Tabs.init();
  Forms.init();
  TableFilter.init();
  Expandable.init();

  // Close modals on overlay click
  document.addEventListener('click', (e) => {
    if (e.target.classList.contains('modal-overlay')) {
      e.target.classList.remove('active');
      document.body.style.overflow = '';
    }
  });

  // Close modals on Escape
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      Modal.closeAll();
    }
  });

  // Initialize score gauge if present
  const gauge = document.getElementById('score-gauge');
  if (gauge) {
    const score = parseInt(gauge.dataset.score || '0');
    if (score < 0) {
      // No scan run yet — show dash instead of misleading number
      const valueEl = gauge.querySelector('.gauge-value');
      if (valueEl) valueEl.textContent = '—';
    } else {
      ScoreGauge.animate('score-gauge', score);
    }
  }

  // Initialize charts if on dashboard
  if (document.getElementById('threat-timeline-chart')) {
    Charts.threatTimeline('threat-timeline-chart');
  }
  if (document.getElementById('category-breakdown-chart')) {
    Charts.categoryBreakdown('category-breakdown-chart');
  }
  if (document.getElementById('family-comparison-chart')) {
    Charts.familyComparison('family-comparison-chart');
  }
  if (document.getElementById('breach-timeline-chart')) {
    Charts.breachTimeline('breach-timeline-chart');
  }
});
