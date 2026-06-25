<p align="center">
  <img src="static/logo.png" alt="PrivacyScrub Logo" width="200"/>
  <br/>
  <strong>🛡️ PrivacyScrub</strong>
  <br/>
  <em>The largest open-source data broker removal platform. Take back your privacy.</em>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> •
  <a href="#features">Features</a> •
  <a href="#comparison">Comparison</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#api">API</a> •
  <a href="#contributing">Contributing</a> •
  <a href="#roadmap">Roadmap</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/brokers-800%2B-00d4aa?style=for-the-badge" alt="800+ Brokers"/>
  <img src="https://img.shields.io/badge/cost-FREE-00d4aa?style=for-the-badge" alt="Free"/>
  <img src="https://img.shields.io/badge/license-MIT-blue?style=for-the-badge" alt="MIT License"/>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?style=for-the-badge" alt="Python 3.10+"/>
  <img src="https://img.shields.io/badge/self--hosted-100%25%20local-purple?style=for-the-badge" alt="Self-Hosted"/>
</p>

---

**PrivacyScrub** is a self-hosted privacy removal platform with an 800+ data broker database — larger than DeleteMe, Aura, Incogni, and Optery combined. Your data never leaves your machine. No subscriptions, no cloud, no compromises.

## Why PrivacyScrub?

Every year, Americans spend $78-$249/year on privacy services that scan a fraction of the data brokers selling their information. PrivacyScrub gives you **more brokers, more features, and more legal tools** — for free.

| | PrivacyScrub | DeleteMe | Aura | Incogni | Optery |
|---|---|---|---|---|---|
| **Price** | **FREE** | $129/yr | $144/yr | $78/yr | $249/yr |
| **Brokers** | **800+** | ~750 | ~100 | ~180 | ~350 |
| **Self-Hosted** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Open Source** | ✅ | ❌ | ❌ | ❌ | ❌ |

> 📊 [Full 40+ feature comparison →](COMPARISON.md)

---

## Features

🔍 **800+ Data Broker Database** — The largest open-source broker database in existence. People search, background checks, B2B, marketing, financial, real estate, health, international, and more.

📊 **Privacy DNA Score** — Weighted 0-100 privacy score factoring broker exposure, data depth, breaches, and social footprint.

🗑️ **Opt-Out Tracking** — Track every removal request from submission to confirmation. Monitor reappearances with scheduled re-scans.

⚖️ **Legal Engine** — Generate GDPR erasure requests, CCPA deletion demands, state-specific privacy letters, and formal cease-and-desist notices.

🌐 **Dark Web Monitoring** — Check if your email, passwords, or personal data appear in known breaches via HIBP API (k-anonymity).

💳 **Credit Freeze Center** — One-click links to freeze credit at all 6 bureaus: Equifax, Experian, TransUnion, Innovis, ChexSystems, NCTUE.

🚨 **Displacement Mode** — Emergency privacy lockdown for domestic violence survivors, evacuees, or anyone needing immediate protection.

📈 **Threat Timeline** — Visual timeline showing when and where your data appeared across brokers.

⚡ **Removal Velocity Dashboard** — Track how fast each broker actually processes your removal requests.

👨‍👩‍👧‍👦 **Family Shield** — Manage privacy for your entire family from a single dashboard. Unlimited profiles.

🔌 **REST API** — Full API access: `/api/score`, `/api/scan`, `/api/brokers` — integrate with your own tools.

🔔 **Webhook Notifications** — POST to any URL when new exposures are found.

🧹 **Account Cleanup** — Find old accounts and generate deletion emails for 100+ services.

📄 **PDF Reports** — Generate professional privacy audit reports for compliance or personal records.

🔒 **100% Local** — SQLite database, no cloud, no tracking, no third-party data sharing. Your data stays on your machine.

---

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/privacyscrub/privacyscrub.git
cd privacyscrub && pip install -r requirements.txt

# 2. Launch
python app.py

# 3. Open your browser
# → http://localhost:5000
```

That's it. No accounts, no API keys, no configuration required.

---

## Screenshots

<p align="center">
  <img src="docs/screenshots/dashboard.png" alt="Dashboard" width="80%"/>
  <br/><em>Privacy DNA Score Dashboard with Threat Timeline</em>
</p>

<p align="center">
  <img src="docs/screenshots/scanner.png" alt="Scanner" width="80%"/>
  <br/><em>800+ Broker Scanner with Category Filters</em>
</p>

<p align="center">
  <img src="docs/screenshots/optouts.png" alt="Opt-Out Center" width="80%"/>
  <br/><em>Opt-Out Tracking with Status Filters and Batch Actions</em>
</p>

<p align="center">
  <img src="docs/screenshots/legal.png" alt="Legal Center" width="80%"/>
  <br/><em>Legal Request Generator — GDPR, CCPA, State Laws</em>
</p>

<p align="center">
  <img src="docs/screenshots/displacement.png" alt="Displacement Mode" width="80%"/>
  <br/><em>Emergency Privacy Lockdown — Displacement Mode</em>
</p>

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Browser (UI)                      │
│  Dashboard │ Scanner │ Opt-Outs │ Legal │ Breaches  │
└────────────────────────┬────────────────────────────┘
                         │ HTTP
┌────────────────────────┴────────────────────────────┐
│                   Flask App (app.py)                  │
│                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │scanner.py│ │ optout.py│ │ legal.py │ │breach.py│ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └───┬────┘ │
│       │             │            │            │      │
│  ┌────┴─────────────┴────────────┴────────────┴───┐ │
│  │              models.py (SQLite)                 │ │
│  └────────────────────────────────────────────────┘ │
│                                                      │
│  ┌──────────┐ ┌──────────────┐ ┌───────────────┐   │
│  │ api.py   │ │  utils.py    │ │ brokers.json  │   │
│  │ REST API │ │ scoring/PDF  │ │   800+ entries │   │
│  └──────────┘ └──────────────┘ └───────────────┘   │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │           legal_templates/                    │   │
│  │  gdpr_erasure │ ccpa_delete │ cease_desist   │   │
│  │  state_specific/ (AZ, CA, CO, CT, VA)        │   │
│  └──────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────┘
         │                              │
    ┌────┴────┐                    ┌────┴────┐
    │ SQLite  │                    │  HIBP   │
    │ (local) │                    │  API    │
    └─────────┘                    └─────────┘
```

### Key Components

| File | Purpose |
|---|---|
| `app.py` | Flask routes, page rendering, session management |
| `scanner.py` | Data broker scanning engine, exposure detection |
| `optout.py` | Opt-out request submission, status tracking, batch processing |
| `legal.py` | Legal template rendering, GDPR/CCPA/state law generators |
| `breach.py` | HIBP integration, dark web monitoring, password checking |
| `models.py` | SQLite ORM, migrations, data models |
| `api.py` | REST API endpoints (`/api/score`, `/api/scan`, `/api/brokers`) |
| `utils.py` | Privacy DNA scoring, PDF generation, CSV/JSON export |
| `brokers.json` | 800+ data broker database with opt-out instructions |

### Tech Stack

- **Backend:** Python 3.10+ / Flask
- **Database:** SQLite (local, zero-config)
- **Frontend:** Vanilla JS, CSS, Chart.js, Font Awesome 6
- **Theme:** Dark mode (#1a1a2e background, #00d4aa accent)
- **External APIs:** HIBP (optional, for breach monitoring)

---

## API

PrivacyScrub includes a full REST API for integration with your own tools and automations.

### Endpoints

```
GET  /api/brokers              — List all brokers (filterable by category, tier)
GET  /api/brokers/:id          — Get broker details
GET  /api/score/:profile_id    — Get Privacy DNA score for a profile
POST /api/scan/:profile_id     — Trigger a scan for a profile
GET  /api/scan/:scan_id        — Get scan results
GET  /api/optouts/:profile_id  — List opt-out requests and their status
POST /api/webhook              — Register a webhook for exposure notifications
```

### Example

```bash
# Get your Privacy DNA score
curl http://localhost:5000/api/score/1

# List all people search brokers
curl http://localhost:5000/api/brokers?category=people_search

# Trigger a scan
curl -X POST http://localhost:5000/api/scan/1
```

---

## Legal Templates

PrivacyScrub includes ready-to-send legal request templates:

| Template | Use Case |
|---|---|
| GDPR Article 17 Erasure | EU right to be forgotten requests |
| CCPA Deletion Request | California consumer data deletion |
| Cease & Desist | Unauthorized data collection demands |
| Arizona ACDPA | Arizona-specific privacy request |
| California CCPA/CPRA Enhanced | Enhanced CA request with all CPRA rights |
| Colorado CPA | Colorado privacy request |
| Connecticut CTDPA | Connecticut privacy request |
| Virginia VCDPA | Virginia privacy request |

Templates include placeholder fields (`{{FULL_NAME}}`, `{{COMPANY_NAME}}`, etc.) that are automatically filled by the Legal Center UI.

---

## Contributing

We welcome contributions! Here's how to help:

### Ways to Contribute

1. **Add Brokers** — Found a data broker not in our database? Add it to `brokers.json`
2. **Improve Opt-Out Instructions** — Better step-by-step guides help everyone
3. **Add Legal Templates** — More states, more countries, more templates
4. **Bug Reports** — Open an issue with steps to reproduce
5. **Feature Requests** — Ideas for new features or improvements
6. **Documentation** — Improve README, add tutorials, write guides

### Adding a New Broker

1. Fork the repository
2. Add your broker entry to `brokers.json` following the schema
3. Include real opt-out URLs where possible
4. Add step-by-step removal instructions
5. Submit a pull request

### Broker Entry Schema

```json
{
  "id": "unique_slug",
  "name": "Display Name",
  "url": "https://site.com",
  "opt_out_url": "https://site.com/optout",
  "opt_out_method": "form|email|phone|mail|api|none",
  "difficulty": "easy|medium|hard|very_hard",
  "estimated_time_minutes": 5,
  "processing_days": 3,
  "verification_type": "email|phone|id|mail|none",
  "reappearance_months": 4,
  "tier": 1,
  "category": "people_search",
  "data_types": ["name", "address", "phone"],
  "legal_basis": ["CCPA"],
  "parent_company": null,
  "network_sites": [],
  "auto_removable": false,
  "step_by_step": "1. Go to opt-out page\n2. ...",
  "notes": "Special notes"
}
```

### Development Setup

```bash
git clone https://github.com/yourusername/privacyscrub.git
cd privacyscrub
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
python app.py
```

---

## Roadmap

### v1.0 — Foundation ✅
- [x] 800+ broker database
- [x] Privacy DNA scoring
- [x] Opt-out tracking
- [x] Legal request generator
- [x] Dark web monitoring
- [x] Credit freeze center
- [x] REST API
- [x] PDF reports

### v1.1 — Automation
- [ ] Browser extension for auto-detection
- [ ] Automated form-fill for simple opt-outs
- [ ] Email template auto-send (SMTP integration)
- [ ] Scheduled scans via cron/task scheduler

### v1.2 — Intelligence
- [ ] Machine learning broker detection
- [ ] Natural language opt-out parsing
- [ ] Broker response analysis
- [ ] Removal success prediction

### v1.3 — Platform
- [ ] Docker container + docker-compose
- [ ] Mobile-optimized PWA
- [ ] Multi-language support (ES, FR, DE, PT)
- [ ] Community broker database (crowdsourced updates)

### v2.0 — Enterprise
- [ ] Multi-tenant support
- [ ] LDAP/SSO integration
- [ ] Compliance reporting (SOC 2, HIPAA)
- [ ] White-label deployment
- [ ] SLA monitoring for broker removals

---

## FAQ

**Q: Is this really free?**
A: Yes. MIT licensed, no strings attached. No freemium, no upsells, no tracking.

**Q: Does my data leave my computer?**
A: Never. Everything runs locally with SQLite. The only outbound requests are to brokers (for scanning) and optionally HIBP (for breach checking).

**Q: How does this compare to DeleteMe?**
A: [See our detailed comparison →](COMPARISON.md) — TL;DR: more brokers, more features, $0/year.

**Q: Can I use this for my family?**
A: Yes. Unlimited profiles. Add every family member.

**Q: Do I need technical skills?**
A: Basic command line comfort (3 commands to start). The UI is designed for everyone.

**Q: How often should I scan?**
A: Quarterly is recommended. Some brokers re-add data within 3-6 months.

---

## License

[MIT License](LICENSE) — Use it, modify it, distribute it. Free forever.

---

## Star History

If PrivacyScrub helps you take back your privacy, consider giving it a ⭐

---

<p align="center">
  <strong>Your data. Your machine. Your privacy.</strong>
  <br/>
  Built with 🛡️ by privacy advocates, for everyone.
</p>
