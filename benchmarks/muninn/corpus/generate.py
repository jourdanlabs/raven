"""Generate the MUNINN benchmark corpus deterministically.

Run: python -m benchmarks.muninn.corpus.generate
Outputs corpus.jsonl, queries.jsonl, corpus.sha256 in this directory.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

_NOW = 1745481600.0   # 2026-04-24 00:00:00 UTC — fixed anchor
_DAY = 86_400.0


def _ts(days_ago: float) -> float:
    return _NOW - days_ago * _DAY


def _entry(id: str, text: str, days_ago: float, source: str = "system",
           supersedes_id=None, validity_end_days_ago=None,
           entity_tags=None, metadata=None, confidence: float = 0.9) -> dict:
    ts = _ts(days_ago)
    ve = _ts(validity_end_days_ago) if validity_end_days_ago is not None else None
    return {
        "id": id,
        "text": text,
        "timestamp": ts,
        "source": source,
        "entity_tags": entity_tags or [],
        "topic_tags": [],
        "confidence_at_ingest": confidence,
        "supersedes_id": supersedes_id,
        "validity_start": ts,
        "validity_end": ve,
        "metadata": metadata or {},
    }


def _query(qid: str, text: str, hazard: str, eids: list[str],
           expected_status: str, expected_approved: list[str], notes: str = "") -> dict:
    return {
        "query_id": qid,
        "query_text": text,
        "hazard_mode": hazard,
        "entry_ids": eids,
        "expected_status": expected_status,
        "expected_approved_ids": expected_approved,
        "notes": notes,
    }


# ── Contradiction groups ──────────────────────────────────────────────────────
# Strategy: entries a+b trigger PULSAR (absolutist or predicate-negation).
# Entry c is a neutral "decision_log" entry that gets APPROVED.
# Each a/b pair shares ≥2 words (len≥4) and has absolutist conflict or negation mismatch.

_C_GROUPS = [
    ("c01", "api_auth",
     "Does our API always return 200 for valid authenticated requests?",
     "The authentication service always validates tokens before processing any API request.",
     "The authentication service never blocks requests without checking rate limits first.",
     "Authentication service v2.4 deployed with improved token validation pipeline."),
    ("c02", "deployment_tests",
     "Does the deployment pipeline always run tests before production?",
     "The deployment pipeline always runs integration tests before pushing to production.",
     "The deployment pipeline never delays releases for integration tests in hotfix mode.",
     "Deployment pipeline v3.1 shipped with parallel test execution support."),
    ("c03", "budget_approval",
     "Does budget always require board approval before proceeding?",
     "Budget requests above $50k always require board approval before proceeding.",
     "Budget requests never require board approval when backed by an existing purchase order.",
     "Budget approval workflow completed and published to the finance portal."),
    ("c04", "copper_forecast",
     "Will copper futures break $4.50 this quarter?",
     "Copper futures are definitely breaking above $4.50 resistance this quarter.",
     "Copper futures are certainly stuck below $4.00 for the remainder of this quarter.",
     "Commodity desk completed Q2 copper forecast analysis and approved distribution."),
    ("c05", "auth_token_ttl",
     "How long are auth tokens valid?",
     "Auth tokens are always valid for exactly 24 hours after issuance.",
     "Auth tokens never persist beyond 4 hours in our security configuration.",
     "Auth token service deployed with configurable TTL support for enterprise accounts."),
    ("c06", "db_backup",
     "Do database backups always complete in the maintenance window?",
     "Database backups always complete successfully within the maintenance window.",
     "Database backups never finish during peak load — must defer to off-peak hours.",
     "Database backup scheduler deployed with adaptive window detection."),
    ("c07", "ml_accuracy",
     "Does our classification model always hit 95% accuracy?",
     "The classification model always achieves above 95% accuracy on the validation dataset.",
     "The classification model never reaches 95% accuracy on real production distribution.",
     "Classification model v4.2 shipped with updated training pipeline and benchmarks."),
    ("c08", "security_patch_flow",
     "Does the security team require developer approval for hotfix deployments?",
     "The security team does not require developer approval for emergency hotfix deployments.",
     "The security team requires developer sign-off for every production hotfix deployment.",
     "Security hotfix workflow approved and deployed to the release management system."),
    ("c09", "sprint_velocity",
     "Does the engineering team always deliver all committed points?",
     "The engineering team always delivers all committed story points each sprint.",
     "The engineering team never commits to full sprint capacity without buffer allocation.",
     "Sprint planning tooling completed and deployed to the engineering dashboard."),
    ("c10", "redis_sessions",
     "Is Redis always the primary store for user sessions?",
     "Redis cache always serves as the primary data source for user session management.",
     "Redis cache never stores sensitive session data — only non-critical metadata.",
     "Redis session layer deployed with encryption-at-rest for all session keys."),
    ("c11", "code_review_policy",
     "Do code changes require security review before merge?",
     "Code changes do not require security review unless they modify authentication modules.",
     "Code changes require a security review for every commit that touches the main branch.",
     "Code review policy approved and published to the engineering handbook."),
    ("c12", "q3_revenue",
     "Will Q3 revenue exceed Q2 performance?",
     "Q3 revenue will definitely exceed Q2 numbers given the current growth trajectory.",
     "Q3 revenue is certainly not tracking above Q2 with current market headwinds.",
     "Q2 revenue analysis completed and approved by the finance committee."),
    ("c13", "team_meetings",
     "Does the product team hold weekly status meetings?",
     "The product team does not hold weekly status meetings — async updates only.",
     "The product team holds a mandatory weekly status meeting every Monday morning.",
     "Product team communication guidelines deployed and approved by leadership."),
    ("c14", "property_values",
     "Do commercial property values always recover after downturns?",
     "Commercial property values always recover within 18 months of market downturns.",
     "Commercial property values never fully recover within a single economic cycle.",
     "Property valuation model completed and approved for enterprise deployment."),
    ("c15", "service_mesh_latency",
     "Does our service mesh maintain sub-10ms latency?",
     "The service mesh always maintains under 10ms p99 latency for internal service calls.",
     "The service mesh never guarantees sub-10ms latency during high-traffic peak periods.",
     "Service mesh upgrade deployed with improved routing and latency monitoring."),
    ("c16", "feature_merge_policy",
     "Can new features merge without integration tests passing?",
     "New features cannot be merged without passing both unit tests and integration tests.",
     "New features can be merged with unit tests only if integration tests are still pending.",
     "Feature merge policy approved and integrated into the CI/CD configuration."),
    ("c17", "data_pipeline_sla",
     "Does our data pipeline always hit the 30-second SLA?",
     "The data pipeline always processes incoming events within 30 seconds of receipt.",
     "The data pipeline never completes batch jobs within the 30-second SLA under load.",
     "Data pipeline SLA monitoring deployed and approved for production tracking."),
    ("c18", "enterprise_compliance",
     "Do enterprise contracts always require compliance review before signing?",
     "All enterprise contracts must include a compliance review before signing.",
     "Enterprise contracts never require compliance review for renewals under $100k.",
     "Enterprise contract compliance workflow approved and deployed to legal portal."),
    ("c19", "copper_yield",
     "What extraction yield does our copper processing achieve?",
     "Copper extraction yield definitely exceeds 85% with our current processing method.",
     "Copper extraction yield is absolutely dependent on ore grade — never guaranteed above 80%.",
     "Copper processing audit completed with updated yield benchmarks approved."),
    ("c20", "feature_flags_prod",
     "Can feature flags be disabled in production without change management?",
     "Feature flags cannot be disabled in production without going through change management.",
     "Feature flags can be disabled instantly in production via the admin dashboard.",
     "Feature flag management system deployed with role-based access controls approved."),
    ("c21", "customer_onboarding",
     "Does SMB onboarding include an in-person kickoff session?",
     "Customer onboarding does not include an in-person kickoff session for SMB accounts.",
     "Customer onboarding includes a mandatory in-person kickoff for every new account.",
     "Customer onboarding playbook completed and approved for all account tiers."),
    ("c22", "vector_search_cost",
     "How much do vector search queries cost at our current tier?",
     "Vector search queries always cost under $0.001 per call at our current usage tier.",
     "Vector search queries never fall below $0.005 at enterprise volumes without negotiation.",
     "Vector search pricing analysis completed and approved by the infrastructure team."),
    ("c23", "infra_review_window",
     "Do infrastructure changes always require a 72-hour review period?",
     "Infrastructure changes always require a 72-hour review period before production deploy.",
     "Infrastructure changes never block releases — they deploy on an independent cadence.",
     "Infrastructure change review policy approved and deployed to the ops runbook."),
    ("c24", "vendor_legal_review",
     "Do vendor contracts require legal review regardless of value?",
     "Vendor contracts do not require legal review when under the $25k auto-approval threshold.",
     "Vendor contracts require legal review regardless of value per the updated policy.",
     "Vendor contract legal review workflow approved and published to procurement portal."),
    ("c25", "llm_json_output",
     "Does the language model always return valid JSON when prompted?",
     "The language model always returns valid JSON when instructed with a system prompt.",
     "The language model never guarantees valid JSON output without explicit output validation.",
     "Language model output validation layer deployed and approved for production usage."),
]


def _build_contradiction(groups) -> tuple[list[dict], list[dict]]:
    entries, queries = [], []
    for gid, topic, query_text, ta, tb, tc in groups:
        eid = lambda s: f"muninn_{gid}_{s}"
        ea = _entry(eid("a"), ta, days_ago=5, source="system")
        eb = _entry(eid("b"), tb, days_ago=3, source="system")
        ec = _entry(eid("c"), tc, days_ago=2, source="decision_log")
        entries += [ea, eb, ec]
        queries.append(_query(
            f"q_{gid}", query_text, "contradiction",
            [eid("a"), eid("b"), eid("c")],
            expected_status="APPROVED",
            expected_approved=[eid("c")],
            notes="a+b contradict; c is neutral and should be approved",
        ))
    return entries, queries


# ── Staleness groups ──────────────────────────────────────────────────────────
# Entry a: stale (validity_end in past for odd groups, superseded for even)
# Entry b: current replacement (supersedes a for even, or just newer)
# Entry c: neutral context, approved

_S_GROUPS = [
    ("s01", "api_key",
     "What is the current production API key?",
     "Production API key: pk_live_abc123xyz789 — rotate every 90 days.",
     "Production API key rotated: pk_live_qrs456uvw012 — valid from 2026-03-01.",
     "API key rotation policy deployed and approved for automated quarterly rotation.",
     "validity_end"),
    ("s02", "db_password",
     "What is the current database connection password?",
     "Primary database password: Tr0ub4dor&3 — last rotated 2025-12-01.",
     "Primary database password updated to Correct-Horse-Battery-Staple — rotated 2026-04-01.",
     "Database credential rotation workflow approved and integrated with secrets manager.",
     "supersedes"),
    ("s03", "server_hostname",
     "What hostname should services use to reach the API gateway?",
     "API gateway hostname: api-v1.internal.company.com — active until Feb 2026.",
     "API gateway migrated to api-v2.internal.company.com — all services updated.",
     "API gateway hostname migration completed and approved by infrastructure team.",
     "validity_end"),
    ("s04", "team_lead",
     "Who is the current engineering team lead?",
     "Engineering team lead: Marcus Chen — appointed Q4 2024.",
     "Engineering team lead updated: Priya Nair — appointed Q1 2026 after Marcus Chen transitioned.",
     "Team lead appointment approved and org chart published to HR portal.",
     "supersedes"),
    ("s05", "budget_q1",
     "What is the approved Q1 engineering budget?",
     "Q1 2026 engineering budget approved at $420,000 — valid through March 31.",
     "Q2 2026 engineering budget approved at $485,000 — effective April 1.",
     "Q2 budget planning completed and approved by the finance committee.",
     "validity_end"),
    ("s06", "sprint_goal",
     "What is the current sprint goal?",
     "Sprint 12 goal: complete METEOR entity resolution module and integration tests.",
     "Sprint 13 goal: complete MUNINN benchmark corpus and scoring harness.",
     "Sprint 13 kickoff completed and goals approved by product and engineering leads.",
     "supersedes"),
    ("s07", "product_price",
     "What is the current price for the Pro tier?",
     "Pro tier pricing: $49/month per seat — valid until end of Q1 2026.",
     "Pro tier pricing updated to $59/month per seat — effective April 1 2026.",
     "Pricing update approved by leadership and deployed to billing system.",
     "validity_end"),
    ("s08", "vendor_contact",
     "Who is our primary contact at DataStream Inc?",
     "DataStream primary contact: James Okafor (james@datastream.io) — account manager since 2024.",
     "DataStream primary contact updated: Fatima Al-Hassan (fatima@datastream.io) — new account manager Q1 2026.",
     "Vendor contact update approved and recorded in CRM system.",
     "supersedes"),
    ("s09", "deploy_config",
     "What environment variables does the prod deployment require?",
     "Production deployment requires NODE_ENV=production, PORT=8080, DB_POOL=10.",
     "Production deployment config updated: NODE_ENV=production, PORT=8443, DB_POOL=25, CACHE=redis.",
     "Deployment config update approved and deployed to infrastructure automation.",
     "validity_end"),
    ("s10", "model_version",
     "Which model version is deployed to production?",
     "Production model: RAVEN-classifier-v1.2 — deployed December 2025.",
     "Production model updated to RAVEN-classifier-v2.0 — deployed March 2026 after benchmark validation.",
     "Model version upgrade approved and deployed following MUNINN benchmark validation.",
     "supersedes"),
    ("s11", "security_policy",
     "What is the current password minimum length policy?",
     "Password policy: minimum 10 characters, updated 2024-06-01, valid for 18 months.",
     "Password policy updated: minimum 16 characters, MFA required — effective 2026-01-01.",
     "Security policy update approved by CISO and deployed to identity management.",
     "validity_end"),
    ("s12", "project_deadline",
     "What is the deadline for the RAVEN v1.0 launch?",
     "RAVEN v1.0 launch deadline: March 15, 2026 — approved by leadership.",
     "RAVEN v1.0 launch deadline revised to May 1, 2026 — extended for MUNINN benchmark completion.",
     "Deadline revision approved by product leadership and updated in project tracker.",
     "supersedes"),
    ("s13", "office_location",
     "Where is the Austin office located?",
     "Austin office: 500 W 2nd St, Suite 700, Austin TX 78701 — lease through Jan 2026.",
     "Austin office relocated to 901 S MoPac Expy, Building 1, Austin TX 78746 — effective Feb 2026.",
     "Office relocation completed and approved; new address published to company directory.",
     "validity_end"),
    ("s14", "api_endpoint",
     "What is the base URL for the Comtrade API integration?",
     "Comtrade API base URL: https://comtradeapi.un.org/public/v1/preview — valid through 2025.",
     "Comtrade API migrated to https://comtradeapi.un.org/data/v1/ — new endpoint live Jan 2026.",
     "API endpoint migration completed and approved; all integrations updated.",
     "supersedes"),
    ("s15", "license_key",
     "What is the current enterprise license key for the data platform?",
     "Enterprise data platform license: LK-ENT-2025-ABCD-WXYZ — expires Dec 31 2025.",
     "Enterprise data platform license renewed: LK-ENT-2026-PQRS-LMNO — valid through Dec 31 2026.",
     "License renewal approved and deployed to the license management system.",
     "validity_end"),
    ("s16", "schema_version",
     "What is the current database schema version?",
     "Database schema at version 0041 — migrated February 2026.",
     "Database schema updated to version 0047 — migrated April 2026 with new index structures.",
     "Schema migration 0047 approved and deployed to production database.",
     "supersedes"),
    ("s17", "cdn_url",
     "What CDN base URL should the frontend use for static assets?",
     "CDN base URL: https://cdn-v1.assets.company.com — active until March 2026.",
     "CDN migrated to https://cdn-v2.assets.company.com — new URL active April 2026.",
     "CDN migration completed and approved; frontend config updated.",
     "validity_end"),
    ("s18", "webhook_endpoint",
     "What webhook URL should payment processor use for notifications?",
     "Payment webhook endpoint: https://api.company.com/webhooks/payment-v1.",
     "Payment webhook endpoint updated: https://api.company.com/webhooks/payment-v2 — migration complete.",
     "Webhook endpoint migration approved and deployed with backward compatibility.",
     "supersedes"),
    ("s19", "rate_limit",
     "What is the current API rate limit for standard tier?",
     "Standard tier API rate limit: 500 requests per minute — valid through Q1 2026.",
     "Standard tier API rate limit increased to 1000 requests per minute — effective Q2 2026.",
     "Rate limit update approved and deployed to API gateway configuration.",
     "validity_end"),
    ("s20", "auth_provider",
     "Which SSO provider is configured for enterprise login?",
     "Enterprise SSO provider: Okta — configured with SAML 2.0, active since 2024.",
     "Enterprise SSO provider migrated from Okta to Auth0 — cutover completed March 2026.",
     "SSO provider migration approved and completed with zero-downtime cutover.",
     "supersedes"),
    ("s21", "cloud_region",
     "Which cloud region is primary for production workloads?",
     "Primary cloud region: AWS us-east-1 (Virginia) — designated primary through Q1 2026.",
     "Primary cloud region updated to AWS us-west-2 (Oregon) for latency and cost reasons — Q2 2026.",
     "Cloud region migration approved and completed with full traffic failover.",
     "validity_end"),
    ("s22", "service_account",
     "What service account runs the data ingestion pipeline?",
     "Data ingestion pipeline runs as: svc-ingest-prod@company.iam.gserviceaccount.com.",
     "Data ingestion service account rotated to svc-ingest-v2@company.iam.gserviceaccount.com — April 2026.",
     "Service account rotation approved and deployed with updated IAM bindings.",
     "supersedes"),
    ("s23", "data_retention",
     "How long is raw event data retained?",
     "Raw event data retention: 90 days — policy set 2024, valid through end of 2025.",
     "Raw event data retention updated to 180 days — new policy effective January 2026 per compliance.",
     "Data retention policy update approved by compliance team and deployed.",
     "validity_end"),
    ("s24", "cicd_version",
     "What CI/CD pipeline version is running on the main repo?",
     "CI/CD pipeline: GitHub Actions workflow v2.3 — deployed November 2025.",
     "CI/CD pipeline upgraded to GitHub Actions workflow v3.1 — deployed March 2026 with parallel jobs.",
     "CI/CD upgrade approved and deployed with improved caching and test parallelism.",
     "supersedes"),
    ("s25", "package_version",
     "What version of the raven package is in production?",
     "raven package version: 0.9.2 — released January 2026.",
     "raven package updated to version 1.0.0 — released April 2026 with MUNINN benchmark support.",
     "raven v1.0.0 release approved and published to internal package registry.",
     "validity_end"),
]


def _build_staleness(groups) -> tuple[list[dict], list[dict]]:
    entries, queries = [], []
    for gid, topic, query_text, ta, tb, tc, stype in groups:
        eid = lambda s: f"muninn_{gid}_{s}"
        if stype == "validity_end":
            ea = _entry(eid("a"), ta, days_ago=60, source="system", validity_end_days_ago=30)
            eb = _entry(eid("b"), tb, days_ago=2, source="decision_log")
        else:  # supersedes
            ea = _entry(eid("a"), ta, days_ago=60, source="system")
            eb = _entry(eid("b"), tb, days_ago=2, source="decision_log", supersedes_id=eid("a"))
        ec = _entry(eid("c"), tc, days_ago=1, source="decision_log")
        entries += [ea, eb, ec]
        queries.append(_query(
            f"q_{gid}", query_text, "staleness",
            [eid("a"), eid("b"), eid("c")],
            expected_status="APPROVED",
            expected_approved=[eid("b"), eid("c")],
            notes="a is stale/superseded and must be rejected; b+c approved",
        ))
    return entries, queries


# ── Importance-inversion groups ───────────────────────────────────────────────
# Entry a: HIGH importance, older (decision_log, decision keywords, ★, 30-45 days)
# Entry b: LOW importance, recent (agent, no keywords, 2-4 days)
# Entry c: MEDIUM importance, moderate (user, some keywords, 10 days)

_I_GROUPS = [
    ("i01", "security_vs_trivial",
     "What recent updates should I know about the security posture?",
     "★ DECISION: Critical CVE-2026-0142 resolved in auth middleware — patch deployed to all nodes.",
     "User mentioned they liked the new dashboard color scheme in today's meeting.",
     "Security team resolved the auth token expiry bug affecting enterprise accounts."),
    ("i02", "budget_vs_chat",
     "Was the Q2 budget request approved?",
     "★ DECISION: Q2 engineering budget approved at $485k — board approved unanimously.",
     "Noticed the office printer is out of paper again this morning.",
     "Finance team resolved the budget allocation discrepancy for Q2 tooling costs."),
    ("i03", "api_deprecation_vs_thanks",
     "Are there any breaking API changes I need to know about?",
     "★ DECISION: REST API v1 deprecated — all clients must migrate to v2 by June 30 2026.",
     "User said thanks for the quick response to their support ticket this afternoon.",
     "API v2 migration guide deployed and approved for distribution to all API consumers."),
    ("i04", "arch_decision_vs_reschedule",
     "Was the database architecture decision finalized?",
     "★ DECISION: Decided to migrate from PostgreSQL to CockroachDB for global distribution.",
     "Weekly standup rescheduled from 9am to 10am starting next Monday.",
     "Database migration spike completed with benchmarks approved by engineering leads."),
    ("i05", "breach_vs_coffee",
     "Were there any security incidents this month?",
     "★ DECISION: Vendor data breach resolved — affected credentials rotated, users notified.",
     "Coffee machine on floor 3 is being serviced and will be back by noon.",
     "Security incident response completed with root cause analysis approved by CISO."),
    ("i06", "compliance_vs_preference",
     "What are the updated compliance requirements for data handling?",
     "★ DECISION: SOC2 Type II audit completed — remediation controls deployed and approved.",
     "User prefers dark mode and noted it in their profile settings this week.",
     "Compliance controls audit resolved with updated documentation approved for distribution."),
    ("i07", "perf_regression_vs_upload",
     "Was the performance regression in the API resolved?",
     "★ DECISION: P0 latency regression resolved — root cause was N+1 query, patch deployed.",
     "Design team uploaded the new brand assets to the shared drive this afternoon.",
     "Performance regression resolved with query optimization deployed to production."),
    ("i08", "legal_vs_ooo",
     "What was the outcome of the legal review?",
     "★ DECISION: Legal settlement approved — $2.4M resolved, NDA signed, case closed.",
     "Colleague out of office until next Tuesday for a family commitment.",
     "Legal review process resolved with updated contract templates approved for use."),
    ("i09", "outage_vs_standup",
     "What caused last week's production outage?",
     "★ DECISION: Outage resolved — root cause: Redis OOM during peak; fixed with eviction policy.",
     "Daily standup notes from this morning uploaded to Notion page.",
     "Outage post-mortem completed with action items resolved and deployed."),
    ("i10", "client_decision_vs_calendar",
     "Did the enterprise client approve the contract renewal?",
     "★ DECISION: Apex Corp enterprise contract renewed — $1.2M ARR, 3-year term approved.",
     "Calendar invite accepted for the all-hands meeting scheduled for Friday.",
     "Contract renewal process completed with updated terms approved by legal team."),
    ("i11", "db_migration_vs_emoji",
     "Was the database migration to the new schema approved?",
     "★ DECISION: Database schema migration 0047 approved — runs this Friday maintenance window.",
     "Someone reacted with a thumbs-up emoji to the team announcement in Slack.",
     "Database migration plan resolved with rollback procedures approved by DBA team."),
    ("i12", "patent_vs_bookmark",
     "Was the patent filing for the memory ranking algorithm approved?",
     "★ DECISION: Patent application US-2026-RAVEN-001 filed and approved for prosecution.",
     "User bookmarked the architecture diagram page for future reference.",
     "Patent application process completed with IP counsel approval and filing confirmed."),
    ("i13", "regulatory_vs_settings",
     "Were there any regulatory findings from the audit?",
     "★ DECISION: Regulatory audit resolved — 3 findings remediated, all controls approved.",
     "User updated their notification preferences to email-only this morning.",
     "Regulatory remediation completed with updated policies deployed and approved."),
    ("i14", "critical_dep_vs_receipt",
     "Is there a critical dependency update we need to ship?",
     "★ DECISION: OpenSSL dependency updated to 3.3.2 — critical CVE patched and deployed.",
     "Read receipt received for the team announcement sent yesterday afternoon.",
     "Dependency update deployed and approved with full regression suite passing."),
    ("i15", "funding_vs_lunch",
     "Did we close the Series B funding round?",
     "★ DECISION: Series B closed — $12M raised, led by Sequoia, board resolution approved.",
     "Team ordered Thai food for today's working lunch — delivery confirmed for noon.",
     "Funding round process resolved with investor agreements approved and signed."),
    ("i16", "critical_bug_vs_thanks",
     "Was the critical bug in the payment flow resolved?",
     "★ DECISION: P0 payment processing bug resolved — duplicate charge issue fixed and deployed.",
     "User thanked the support team for the quick resolution of their billing question.",
     "Payment processing bug resolved with transaction rollback logic deployed to production."),
    ("i17", "compromise_vs_rename",
     "Were there any infrastructure security events this week?",
     "★ DECISION: Compromised build server resolved — isolated, rotated all secrets, patched.",
     "Config file renamed from settings.old.yml to settings.yml.bak for clarity.",
     "Build infrastructure security review resolved with updated access controls approved."),
    ("i18", "vendor_breach_vs_avatar",
     "Did any third-party vendors report security incidents?",
     "★ DECISION: Upstream vendor breach resolved — API keys rotated, tokens revoked, deployed.",
     "User updated their profile avatar to a new photo in the company directory.",
     "Third-party vendor security review completed with updated integration approved."),
    ("i19", "contract_vs_react",
     "Was the enterprise partnership contract signed?",
     "★ DECISION: Partnership agreement with MineralLogic signed — $800k deal approved.",
     "Someone reacted to the birthday message in the team Slack channel this morning.",
     "Contract signing process completed with legal review approved and filed."),
    ("i20", "infra_capacity_vs_ooo",
     "Was the infrastructure capacity expansion approved?",
     "★ DECISION: Production capacity doubled — 32 new nodes approved and deployed to cluster.",
     "Team member out of office on Friday for a personal appointment.",
     "Infrastructure capacity planning completed with procurement approved and deployed."),
    ("i21", "critical_cve_vs_pref",
     "Are there any unpatched CVEs in our production stack?",
     "★ DECISION: CVE-2026-5589 in core runtime resolved — emergency patch deployed to all nodes.",
     "User noted a preference for bullet-point format in async updates going forward.",
     "CVE remediation completed with security scan approved showing zero critical findings."),
    ("i22", "board_approval_vs_reaction",
     "Did the board approve the new product roadmap?",
     "★ DECISION: Product roadmap approved by board — Q3 launch of RAVEN v1.0 confirmed.",
     "Colleague reacted to the meeting summary with a checkmark in the group chat.",
     "Board approval process resolved with updated roadmap deployed to product portal."),
    ("i23", "data_loss_vs_note",
     "Was the data loss incident from last month fully resolved?",
     "★ DECISION: Data loss incident resolved — backups validated, all records restored, approved.",
     "Meeting notes from yesterday's retrospective uploaded to the team wiki.",
     "Data incident response completed with updated backup policy approved and deployed."),
    ("i24", "product_pivot_vs_comment",
     "Was the major product strategy change formally approved?",
     "★ DECISION: Product pivot approved — repositioning RAVEN as direct MemPalace competitor.",
     "User left a comment on the design mockup saying the colors look great.",
     "Product strategy review resolved with updated positioning approved by leadership."),
    ("i25", "enterprise_contract_vs_calendar",
     "Was the Fortune 500 contract approved?",
     "★ DECISION: Fortune 500 enterprise contract approved — $3.5M ARR, closes Q2 2026.",
     "Calendar invite sent for the weekly team sync recurring every Thursday at 2pm.",
     "Enterprise contract process completed with legal and finance approval confirmed."),
]


def _build_importance(groups) -> tuple[list[dict], list[dict]]:
    entries, queries = [], []
    for gid, topic, query_text, ta, tb, tc in groups:
        eid = lambda s: f"muninn_{gid}_{s}"
        ea = _entry(eid("a"), ta, days_ago=35, source="decision_log",
                    metadata={"importance": "critical"})
        eb = _entry(eid("b"), tb, days_ago=3, source="agent")
        ec = _entry(eid("c"), tc, days_ago=10, source="user")
        entries += [ea, eb, ec]
        queries.append(_query(
            f"q_{gid}", query_text, "importance_inversion",
            [eid("a"), eid("b"), eid("c")],
            expected_status="APPROVED",
            expected_approved=[eid("a"), eid("c")],
            notes="a is high-importance/old (should be approved), b is trivial/recent (should be rejected)",
        ))
    return entries, queries


# ── Entity-resolution groups ──────────────────────────────────────────────────
# All 3 entries are complementary (no contradiction), all should be approved.
# Tests that PULSAR does NOT false-positive on name-alias variations.

_E_GROUPS = [
    ("e01", "leland_person",
     "What do we know about Leland Jourdan?",
     "Leland completed the RAVEN architecture design and approved the MUNINN benchmark scope.",
     "Captain Jourdan approved the product repositioning strategy for JourdanLabs Research.",
     "LE Jourdan II confirmed the RAVEN v1.0 launch timeline and approved stakeholder communications."),
    ("e02", "jourdanlabs_org",
     "What projects is JourdanLabs Research working on?",
     "JourdanLabs approved the RAVEN memory system as the flagship research product for 2026.",
     "Jourdan Labs Research completed the VANTAGE audit on all four benchmark codebases.",
     "JLR deployed the MUNINN benchmark framework and approved the competitive analysis scope."),
    ("e03", "raven_system",
     "What is the current status of the RAVEN memory system?",
     "RAVEN completed the full validation pipeline including AURORA confidence gate testing.",
     "The memory system deployed seven validation engines from METEOR through AURORA.",
     "RAVEN pipeline approved for benchmark testing after all 87 unit tests passed successfully."),
    ("e04", "mineralscope_product",
     "What is the current status of the MineralScope platform?",
     "MineralLogic completed the SSRF remediation sprint and passed VANTAGE audit at 87%.",
     "MineralScope deployed the production data loader refactor reducing complexity from 40 to 12.",
     "The Mineral platform approved for production after VANTAGE scored 87.1% APPROVED."),
    ("e05", "aws_infra",
     "Which infrastructure region are production workloads running in?",
     "AWS us-east-1 Virginia approved as the primary production region for all API workloads.",
     "The Virginia datacenter completed capacity expansion with 32 additional compute nodes deployed.",
     "East Coast cloud deployment approved with updated routing and latency monitoring deployed."),
    ("e06", "propertygraph_product",
     "What is the current status of the PropertyGraph platform?",
     "PropertyGraph completed the search route error boundary fix and passed VANTAGE at 93.5%.",
     "The property intelligence platform deployed the try/catch guard for the search API handler.",
     "PropertyGraph search API approved for production after security review and VANTAGE validation."),
    ("e07", "nautilus_pipeline",
     "What is the current state of the NAUTILUS data ingestion pipeline?",
     "NAUTILUS completed the fetchComtrade error boundary fix — unhandled rejections resolved.",
     "The portgraph data pipeline deployed per-year error boundaries and batch summary logging.",
     "NAUTILUS ingest script approved after VANTAGE audit scored 88.1% APPROVED."),
    ("e08", "valhalla_archive",
     "What happened to the Valhalla AI project?",
     "Valhalla AI was archived and superseded by RAVEN as the primary memory system project.",
     "The deprecated memory service was replaced with a clean-start implementation named RAVEN.",
     "Valhalla AI codebase approved for archival with redirect notice pointing to RAVEN repository."),
    ("e09", "muninn_benchmark",
     "What is the MUNINN benchmark testing?",
     "MUNINN completed corpus generation with 500 entries across six hazard modes.",
     "The benchmark suite deployed scoring harness with six baseline comparisons including MemPalace.",
     "MUNINN benchmark approved for publication after SHA-sealed corpus and methodology review."),
    ("e10", "mempalace_competitor",
     "What is MemPalace and how does RAVEN compare?",
     "MemPalace deployed a graph-based memory system with basic entity deduplication.",
     "MemPalace AI ships without contradiction detection or temporal decay scoring.",
     "The competing memory system approved for inclusion in MUNINN baseline comparisons."),
    ("e11", "videl_client",
     "What does the client need from the VANTAGE reports?",
     "Videl approved the VANTAGE report format with AURORA score and top-findings breakdown.",
     "The client requested remediation sprint summaries for all critical findings.",
     "Stakeholder completed review of all four VANTAGE audit reports and approved distribution."),
    ("e12", "aurora_gate",
     "How does the AURORA confidence gate work?",
     "AURORA completed composite scoring with weights eclipse=0.25, quasar=0.45, pulsar=0.30.",
     "The confidence gate deployed approval threshold at 0.80 with NOVA additive bonus capped at 0.10.",
     "AURORA gate approved after benchmark validation confirmed correct approve/reject behavior."),
    ("e13", "copper_commodity",
     "What is the current copper market outlook?",
     "Copper spot price reached $4.22/lb in Q1 2026 driven by EV battery demand growth.",
     "Cu futures contracts deployed at $4.35/lb for June 2026 delivery on the CME exchange.",
     "Copper futures analysis completed and approved by the commodity research desk."),
    ("e14", "current_sprint",
     "What work is in scope for the current development sprint?",
     "The sequencing sprint approved MUNINN benchmark as the primary deliverable for the week.",
     "Sprint 13 completed the ATLAS remediation work for NAUTILUS and PropertyGraph.",
     "Current sprint goals confirmed: MUNINN corpus, harness, docs, and first git commit."),
    ("e15", "sequel_companies",
     "What company is behind JourdanLabs Research?",
     "Sequel Companies approved the JourdanLabs research program budget for 2026.",
     "Leland at sequel.io confirmed the competitive benchmark strategy against MemPalace.",
     "Sequel Companies completed the strategic review and approved RAVEN for public launch."),
    ("e16", "meteor_engine",
     "What does the METEOR engine do in the RAVEN pipeline?",
     "METEOR completed entity tagging with Levenshtein-distance alias resolution for fuzzy matching.",
     "The entity tagger deployed with configurable alias maps and normalization preprocessing.",
     "METEOR entity extraction module approved after passing all unit tests in the test suite."),
    ("e17", "quasar_engine",
     "How does QUASAR rank memories by importance?",
     "QUASAR completed importance scoring using decision keywords, source authority, and recency.",
     "The importance ranker deployed with configurable weights and causal centrality bonus.",
     "QUASAR scoring module approved after importance-inversion test cases validated correctly."),
    ("e18", "eclipse_engine",
     "What does the ECLIPSE engine compute?",
     "ECLIPSE completed temporal decay weighting using exponential half-life scoring.",
     "The decay engine deployed with configurable half-life and validity-end staleness detection.",
     "ECLIPSE module approved after recency tier classification tests passed at 100% coverage."),
    ("e19", "pulsar_engine",
     "How does PULSAR detect contradictions between memories?",
     "PULSAR completed contradiction detection using absolutist word patterns and predicate negation.",
     "The conflict engine deployed with 90-day temporal window for active contradiction checking.",
     "PULSAR module approved after contradiction and false-positive test cases validated correctly."),
    ("e20", "nova_engine",
     "How does NOVA build causal chains between memories?",
     "NOVA completed causal graph construction using keyword markers and word overlap scoring.",
     "The causal chain builder deployed with configurable overlap threshold and centrality bonus.",
     "NOVA module approved after causal chain and centrality test cases all passed successfully."),
    ("e21", "citadel_project",
     "What is the CITADEL project?",
     "CITADEL completed a prior VANTAGE security audit validating the scan pipeline configuration.",
     "The CITADEL codebase was used to calibrate VANTAGE scanning parameters before MineralLogic.",
     "CITADEL project approved as reference scan for VANTAGE pipeline configuration baseline."),
    ("e22", "peru_trade_data",
     "What trade data does portgraph-peru contain?",
     "The Peru trade data completed ingestion of 2021-2023 UN Comtrade export declarations.",
     "Portgraph-peru deployed global commodity corridor data across 11 countries and 12 HS codes.",
     "NAUTILUS dataset approved for production after VANTAGE remediation and error boundary fixes."),
    ("e23", "production_db",
     "What database is used for the primary production data store?",
     "The production database completed schema migration 0047 with updated index structures.",
     "Prod DB deployed with connection pooling at 25 and Redis cache layer for session management.",
     "The main postgres instance approved for production after DBA review of migration 0047."),
    ("e24", "staging_env",
     "How does the staging environment differ from production?",
     "The staging environment completed deployment of RAVEN v0.9.2 for pre-release validation.",
     "Stage.company.com deployed with production-equivalent data fixtures and reduced scale.",
     "Staging environment approved for MUNINN benchmark pre-release testing."),
    ("e25", "main_api",
     "What API does the frontend use for search and data retrieval?",
     "The main API completed upgrade to REST v2 with improved pagination and filtering support.",
     "The API gateway deployed with rate limiting, auth middleware, and structured error responses.",
     "Backend service approved for production after VANTAGE audit and security review completed."),
]


def _build_entity(groups) -> tuple[list[dict], list[dict]]:
    entries, queries = [], []
    for gid, topic, query_text, ta, tb, tc in groups:
        eid = lambda s: f"muninn_{gid}_{s}"
        ea = _entry(eid("a"), ta, days_ago=10, source="decision_log")
        eb = _entry(eid("b"), tb, days_ago=7, source="decision_log")
        ec = _entry(eid("c"), tc, days_ago=4, source="decision_log")
        entries += [ea, eb, ec]
        queries.append(_query(
            f"q_{gid}", query_text, "entity_resolution",
            [eid("a"), eid("b"), eid("c")],
            expected_status="APPROVED",
            expected_approved=[eid("a"), eid("b"), eid("c")],
            notes="all three entries describe the same entity under different names — all should be approved",
        ))
    return entries, queries


# ── Causal-coherence groups ───────────────────────────────────────────────────
# Entry a: root cause (oldest, contains decision keyword)
# Entry b: intermediate effect (middle, contains CAUSAL_KEYWORDS, shares words with a)
# Entry c: final effect (newest, contains CAUSAL_KEYWORDS, shares words with b)
# NOVA builds edges a→b, b→c giving centrality bonus to b.

_CC_GROUPS = [
    ("cc01", "db_migration_chain",
     "What led to the latency improvements after the migration?",
     "Database schema migration 0047 completed successfully, resolving all blocking index issues.",
     "The schema migration consequently triggered cache invalidation across all query services.",
     "As a result, query latency improved by 40% after cache rebuild was deployed to production."),
    ("cc02", "budget_hire_chain",
     "What happened after the Q2 budget was approved?",
     "Q2 engineering budget approved at $485k, resolving the headcount freeze from Q1.",
     "The budget approval therefore led to hiring four engineers who started onboarding this month.",
     "As a result, sprint velocity increased by 35% following the completed onboarding process."),
    ("cc03", "rate_limit_chain",
     "What caused the client-side errors last week?",
     "API rate limit configuration updated from 1000 to 500 requests per minute for cost reasons.",
     "The rate limit change consequently triggered a spike in 429 errors across all API clients.",
     "As a result, the rate limit rollback was deployed restoring 1000 requests per minute."),
    ("cc04", "security_patch_chain",
     "What caused the brief service interruption during the maintenance window?",
     "Security patch for CVE-2026-0142 deployed to all authentication service nodes.",
     "The security patch deployment triggered a rolling restart across all authentication nodes.",
     "As a result, a 3-minute degraded availability window occurred during peak hours."),
    ("cc05", "feature_flag_chain",
     "What is the current status of the new search algorithm experiment?",
     "Feature flag search-v2-beta enabled for 10% of production traffic on April 20.",
     "The feature flag rollout consequently started a live experiment comparing search algorithms.",
     "As a result, the experiment is running with statistical significance expected by May 1."),
    ("cc06", "compliance_chain",
     "What remediation work resulted from the compliance audit?",
     "Compliance audit completed with three critical findings identified in access controls.",
     "The audit findings consequently triggered a remediation sprint focused on access control gaps.",
     "As a result, all three findings were resolved and the remediation sprint was approved complete."),
    ("cc07", "vendor_contract_chain",
     "What revenue did the DataStream integration unlock?",
     "DataStream vendor contract approved and signed — integration scope finalized.",
     "The DataStream contract approval consequently started the API integration build sprint.",
     "As a result, the integration shipped and unlocked $200k in new data revenue per quarter."),
    ("cc08", "cve_chain",
     "Was the OpenSSL vulnerability fully mitigated?",
     "CVE-2026-5589 disclosed affecting OpenSSL versions below 3.3.2 in runtime dependencies.",
     "The CVE disclosure consequently triggered an emergency patch deployment to all production nodes.",
     "As a result, the vulnerability was mitigated with zero exploitation incidents confirmed."),
    ("cc09", "memory_leak_chain",
     "What caused the service degradation incident last Tuesday?",
     "Memory leak detected in the session management layer after deploying the v2.3 service update.",
     "The memory leak consequently caused service degradation with p99 latency spiking to 800ms.",
     "As a result, the service restart resolved degradation and the memory leak patch was deployed."),
    ("cc10", "schema_migration_fail_chain",
     "What caused last month's failed database migration?",
     "Database schema migration 0044 started with a missing index on the foreign key constraint.",
     "The missing index consequently caused migration timeout and rollback to schema version 0043.",
     "As a result, migration 0044 was revised with the index added and successfully deployed."),
    ("cc11", "api_key_expiry_chain",
     "What caused the integration outage on March 15?",
     "Comtrade API key expired on March 15 with no automated rotation configured.",
     "The expired API key consequently caused all Comtrade fetch requests to fail with 401 errors.",
     "As a result, outage was declared and resolved after key rotation and monitoring deployed."),
    ("cc12", "model_update_chain",
     "What happened after the classifier model was updated?",
     "Classification model v4.2 deployed after extended training on updated production corpus.",
     "The model update consequently improved accuracy from 91% to 97% on the validation benchmark.",
     "As a result, a new accuracy benchmark was established and approved for future model comparisons."),
    ("cc13", "market_crash_chain",
     "How did the portfolio respond to the Q1 market correction?",
     "Commodity market correction occurred in Q1 with copper dropping 12% in six weeks.",
     "The market correction consequently triggered an automated portfolio rebalancing review.",
     "As a result, the rebalancing minimized losses to 4% against a benchmark drawdown of 11%."),
    ("cc14", "hiring_onboard_chain",
     "Did the new engineering hires improve team productivity?",
     "Four senior engineers hired in Q1 following board approval of the headcount expansion.",
     "The new hire onboarding consequently completed in three weeks with full system access granted.",
     "As a result, team productivity improved with two additional features shipped ahead of schedule."),
    ("cc15", "scaling_latency_chain",
     "Did the infrastructure scaling resolve the latency SLA violations?",
     "Production infrastructure scaled from 8 to 16 nodes following capacity planning approval.",
     "The infrastructure scaling consequently reduced average API response time from 180ms to 95ms.",
     "As a result, the p99 latency SLA of 200ms was met for the first time since January."),
    ("cc16", "breach_investigation_chain",
     "What is the status of the security breach investigation?",
     "Security breach detected in build infrastructure — unauthorized access via compromised key.",
     "The breach detection consequently triggered a formal investigation with external forensics.",
     "As a result, the investigation identified root cause and remediation plan was approved."),
    ("cc17", "launch_adoption_chain",
     "Did the new feature launch drive the expected user adoption?",
     "RAVEN memory recall feature launched to 100% of production users on April 10.",
     "The feature launch consequently drove a 28% increase in weekly active session usage.",
     "As a result, revenue increased by $45k MRR within two weeks of the launch."),
    ("cc18", "cost_overrun_chain",
     "What happened after the infrastructure cost overrun was discovered?",
     "Infrastructure cost overrun of $80k identified in Q1 cloud spend review.",
     "The cost overrun consequently triggered an emergency budget review and spending audit.",
     "As a result, non-critical workloads were paused and spending was frozen pending review."),
    ("cc19", "server_failure_chain",
     "How was the primary server failure handled?",
     "Primary API server failure detected at 02:47 UTC due to disk full on root volume.",
     "The server failure consequently triggered automatic failover to the standby replica.",
     "As a result, service was restored within 4 minutes with zero data loss confirmed."),
    ("cc20", "research_funding_chain",
     "Did publishing the MUNINN benchmark results attract investor interest?",
     "MUNINN benchmark research completed with RAVEN outperforming MemPalace on all six hazard modes.",
     "The research findings consequently prompted investor outreach from three venture funds.",
     "As a result, a Series B conversation was started with Sequoia following benchmark publication."),
    ("cc21", "sprint_demo_chain",
     "Did the sprint demo result in client approval?",
     "Sprint 13 completed all deliverables — MUNINN corpus, harness, and documentation approved.",
     "The sprint completion consequently enabled a product demo delivered to the Videl stakeholder.",
     "As a result, the client approved the RAVEN roadmap and confirmed the Q2 launch timeline."),
    ("cc22", "load_test_chain",
     "What did the load test reveal about system bottlenecks?",
     "Load test completed at 10x expected traffic — database connection pool exhausted at 8x.",
     "The connection pool exhaustion consequently triggered a bottleneck analysis sprint.",
     "As a result, pool size was increased and query batching was deployed resolving the bottleneck."),
    ("cc23", "regulatory_chain",
     "How did the new data regulation affect our retention policy?",
     "Regulatory change announced requiring 180-day minimum retention for all financial records.",
     "The regulatory change consequently triggered a compliance review of all retention policies.",
     "As a result, retention policy was updated and deployed to meet the new regulatory requirement."),
    ("cc24", "pipeline_alert_chain",
     "What caused the on-call alert last Thursday evening?",
     "Data pipeline failure detected — Comtrade API returned 503 errors for all reporter codes.",
     "The pipeline failure consequently triggered PagerDuty alerts to the on-call engineer.",
     "As a result, the on-call engineer resolved the issue by implementing the retry backoff logic."),
    ("cc25", "pivot_roadmap_chain",
     "What changed on the product roadmap after the repositioning decision?",
     "Product pivot approved — RAVEN repositioned as direct MemPalace competitor with benchmark focus.",
     "The product pivot consequently required a full roadmap revision with new competitive priorities.",
     "As a result, the revised roadmap was approved and the team was realigned around MUNINN launch."),
]


def _build_causal(groups) -> tuple[list[dict], list[dict]]:
    entries, queries = [], []
    for gid, topic, query_text, ta, tb, tc in groups:
        eid = lambda s: f"muninn_{gid}_{s}"
        ea = _entry(eid("a"), ta, days_ago=30, source="system")
        eb = _entry(eid("b"), tb, days_ago=15, source="system")
        ec = _entry(eid("c"), tc, days_ago=2, source="system")
        entries += [ea, eb, ec]
        queries.append(_query(
            f"q_{gid}", query_text, "causal_coherence",
            [eid("a"), eid("b"), eid("c")],
            expected_status="APPROVED",
            expected_approved=[eid("a"), eid("b"), eid("c")],
            notes="a→b→c causal chain; NOVA centrality boosts b; all approved",
        ))
    return entries, queries


# ── Refusal-warranted groups ──────────────────────────────────────────────────
# All 3 entries in each group are stale (validity_end in past).
# Expected: AURORA REFUSES (all rejected, overall < 0.30).

_R_GROUPS = [
    ("r01", "expired_api_docs",
     "What does the old API v1 documentation say about authentication?",
     "API v1 authentication requires HMAC-SHA256 signature on all requests.",
     "API v1 rate limit was 100 requests per minute per authenticated client.",
     "API v1 documentation archived — all endpoints deprecated as of December 2025."),
    ("r02", "expired_pricing",
     "What were the 2024 pricing tiers for the platform?",
     "2024 Starter tier: $19/month, Pro tier: $49/month, Enterprise: custom.",
     "2024 pricing included a free tier with 1000 API calls per month.",
     "2024 pricing model retired in January 2025 — see updated pricing page."),
    ("r03", "expired_team_structure",
     "What was the org structure of the engineering team in 2024?",
     "2024 engineering org: Marcus Chen (lead), 3 senior engineers, 2 junior engineers.",
     "2024 team used a flat structure with no dedicated QA or DevOps roles.",
     "2024 org structure superseded — team restructured in Q1 2025."),
    ("r04", "expired_vendor",
     "What was the agreement with the old data vendor SmithData?",
     "SmithData contract: $24k/year for 500k API calls, signed January 2024.",
     "SmithData data quality SLA: 99.5% uptime, 24-hour refresh cadence.",
     "SmithData contract terminated in Q3 2024 — migrated to DataStream."),
    ("r05", "expired_security_creds",
     "What SSH keys were used to access the legacy build servers?",
     "Legacy build server SSH key: rsa-key-2023-build, fingerprint SHA256:abc123.",
     "Legacy build server IP: 10.0.1.42, accessible from office VPN only.",
     "Legacy build servers decommissioned in October 2025 — keys revoked."),
    ("r06", "expired_product_spec",
     "What was the original product specification for MemPalace integration?",
     "MemPalace integration spec: bidirectional sync via REST API, v1 schema.",
     "MemPalace integration required OAuth 2.0 tokens and webhook registration.",
     "MemPalace integration spec retired — RAVEN is now a direct competitor, not an integration."),
    ("r07", "expired_infra_config",
     "What was the original Kubernetes cluster configuration?",
     "Kubernetes cluster v1.24 deployed with 4 worker nodes and 2 control plane nodes.",
     "Original cluster used node pools with t3.large instances and 50GB storage per node.",
     "Original cluster config superseded — cluster rebuilt with v1.29 in March 2026."),
    ("r08", "expired_model_weights",
     "What were the hyperparameters used for the v0.8 classifier model?",
     "Classifier v0.8 trained with learning rate 1e-4, batch size 32, 50 epochs.",
     "Classifier v0.8 used BERT-base embeddings with a 2-layer classification head.",
     "Classifier v0.8 weights archived — superseded by v4.2 trained on updated corpus."),
    ("r09", "expired_compliance",
     "What was the SOC2 control framework used in the 2023 audit?",
     "2023 SOC2 audit used AICPA TSC framework with 47 controls in scope.",
     "2023 audit findings included 5 observations and 2 exceptions remediated by Q4.",
     "2023 SOC2 report expired — 2025 report available from the compliance team."),
    ("r10", "expired_roadmap",
     "What was the original 2025 product roadmap?",
     "2025 roadmap Q1: launch METEOR entity resolution and NOVA causal graph.",
     "2025 roadmap Q2: complete ECLIPSE decay engine and PULSAR contradiction detection.",
     "2025 roadmap superseded by product pivot — see 2026 roadmap approved by board."),
    ("r11", "expired_budget_2024",
     "What was the total engineering budget for fiscal year 2024?",
     "FY2024 engineering budget: $1.2M total, $800k headcount, $400k infrastructure.",
     "FY2024 included a $100k reserve fund for unexpected infrastructure scaling costs.",
     "FY2024 budget closed — FY2025 and FY2026 budgets approved and in effect."),
    ("r12", "expired_sprint_goals",
     "What were the goals for Sprint 8 back in November 2025?",
     "Sprint 8 goal: complete SQLite storage layer and TF-IDF embedder implementation.",
     "Sprint 8 stretch goal: begin QUASAR importance ranking engine prototype.",
     "Sprint 8 completed in November 2025 — see Sprint 13 for current goals."),
    ("r13", "expired_deployment_runbook",
     "What steps were in the v1.0 deployment runbook from 2024?",
     "v1.0 deployment runbook step 1: run database migrations with --dry-run flag.",
     "v1.0 deployment runbook step 2: deploy to staging, validate with smoke tests.",
     "v1.0 runbook retired — v3.1 runbook with automated rollback now in use."),
    ("r14", "expired_api_keys_old",
     "What API credentials were used for the original Comtrade integration?",
     "Original Comtrade API key: CTRADE-2024-KEY-ABCD1234, issued January 2024.",
     "Original integration used subscription tier with 500 requests per day limit.",
     "Original Comtrade credentials revoked April 2025 — using public API endpoint now."),
    ("r15", "expired_office_setup",
     "What was the setup process for the old downtown office?",
     "Downtown office onboarding: badge access from HR, desk on floor 3, parking pass.",
     "Downtown office had a dedicated server room on floor 2 for the on-premises build cluster.",
     "Downtown office lease ended January 2026 — team relocated to South Austin office."),
    ("r16", "expired_sla_2024",
     "What were the SLA commitments in the 2024 enterprise agreements?",
     "2024 enterprise SLA: 99.9% uptime, 4-hour response time for P1 incidents.",
     "2024 SLA included credits for downtime exceeding 4 hours in any calendar month.",
     "2024 SLA template superseded — 2026 template with 99.95% target now in use."),
    ("r17", "expired_staging_config",
     "What was the old staging environment configuration?",
     "Old staging environment: 2-node cluster on AWS t2.medium with shared database.",
     "Old staging used a reduced dataset of 10k records for faster test cycle times.",
     "Old staging config decommissioned March 2026 — production-parity staging now active."),
    ("r18", "expired_legal_template",
     "What was in the old vendor NDA template from 2023?",
     "2023 NDA template: mutual confidentiality, 2-year term, Texas law jurisdiction.",
     "2023 NDA required notarization for contracts above $500k value.",
     "2023 NDA template replaced by 2025 template with updated data privacy clauses."),
    ("r19", "expired_monitoring",
     "What alerts were configured in the original monitoring setup?",
     "Original monitoring: PagerDuty alerts for CPU above 80% and memory above 90%.",
     "Original setup used 5-minute polling intervals for all service health checks.",
     "Original monitoring config replaced — Datadog with 30-second intervals now deployed."),
    ("r20", "expired_data_schema",
     "What was the original database schema for the memories table?",
     "Original memories table: id, text, timestamp, source, tags — 5 columns total.",
     "Original schema lacked embedding storage — embeddings stored in a separate flat file.",
     "Original schema superseded by migration 0047 — see current schema in schema.py."),
    ("r21", "expired_ci_config",
     "What was the original CI pipeline configuration?",
     "Original CI: GitHub Actions with single job, no parallelism, 15-minute average runtime.",
     "Original CI lacked test coverage reporting and had no caching for dependencies.",
     "Original CI config replaced by v3.1 with parallel jobs and coverage gates."),
    ("r22", "expired_embed_model",
     "What embedding model was used in the original prototype?",
     "Original prototype used sentence-transformers/all-MiniLM-L6-v2 for embeddings.",
     "Original embedding model required 500MB download and 2GB RAM at inference time.",
     "Original embedding model replaced by deterministic TF-IDF trigram embedder in production."),
    ("r23", "expired_rate_limit_2024",
     "What rate limits applied to the 2024 API tier structure?",
     "2024 Free tier: 100 requests/hour. Starter: 1000/hour. Pro: 10000/hour.",
     "2024 rate limits reset at midnight UTC and did not support burst allowances.",
     "2024 rate limit tiers retired — 2026 per-minute rate limits now in effect."),
    ("r24", "expired_contact_info",
     "What were the emergency contact details for the legacy infrastructure team?",
     "Legacy infra emergency contact: ops@company.com, phone +1-512-555-0142.",
     "Legacy infra on-call rotation included 4 engineers on weekly rotation.",
     "Legacy infra team disbanded Q2 2025 — platform team handles all infrastructure now."),
    ("r25", "expired_partner_terms",
     "What were the original partnership terms with the first beta customers?",
     "Beta customer terms: free access through December 2024, feedback required monthly.",
     "Beta program included 10 design partners with weekly product feedback sessions.",
     "Beta program concluded December 2024 — all partners migrated to paid tiers."),
]


def _build_refusal(groups) -> tuple[list[dict], list[dict]]:
    entries, queries = [], []
    for gid, topic, query_text, ta, tb, tc in groups:
        eid = lambda s: f"muninn_{gid}_{s}"
        # All entries expired 60 days ago
        ea = _entry(eid("a"), ta, days_ago=120, source="ingest", validity_end_days_ago=60)
        eb = _entry(eid("b"), tb, days_ago=120, source="ingest", validity_end_days_ago=60)
        ec = _entry(eid("c"), tc, days_ago=120, source="ingest", validity_end_days_ago=60)
        entries += [ea, eb, ec]
        queries.append(_query(
            f"q_{gid}", query_text, "refusal_warranted",
            [eid("a"), eid("b"), eid("c")],
            expected_status="REFUSED",
            expected_approved=[],
            notes="all entries expired — AURORA must refuse rather than surface stale data",
        ))
    return entries, queries


# ── Clean entries (50) ────────────────────────────────────────────────────────

_CLEAN = [
    ("cl01", "What is the current RAVEN version in production?",
     "raven v1.0.0 deployed to production April 24 2026 — all 87 tests passing."),
    ("cl02", "What is the AURORA approval threshold?",
     "AURORA approval threshold set at 0.80 — entries scoring above this are approved."),
    ("cl03", "How many hazard modes does MUNINN benchmark cover?",
     "MUNINN benchmark completed with 500 entries across six hazard modes plus 50 clean entries."),
    ("cl04", "What is the half-life default for ECLIPSE decay?",
     "ECLIPSE decay default half-life set at 30 days — configurable per deployment."),
    ("cl05", "What source authority weight does decision_log get in QUASAR?",
     "QUASAR source authority for decision_log set at 1.0 — highest available authority weight."),
    ("cl06", "What is the NOVA causal bonus cap?",
     "NOVA additive bonus capped at 0.10 — applied on top of base AURORA composite score."),
    ("cl07", "What storage backend does RAVEN use?",
     "RAVEN storage deployed with SQLite backend and TF-IDF trigram embedder for hybrid search."),
    ("cl08", "What CLI commands does RAVEN expose?",
     "RAVEN CLI deployed with four commands: recall, remember, ingest, and status."),
    ("cl09", "What test framework does RAVEN use?",
     "RAVEN test suite built with pytest and pytest-cov — 87 tests at 94% coverage deployed."),
    ("cl10", "What is the PULSAR contradiction detection window?",
     "PULSAR contradiction window set at 90 days — entries more than 90 days apart are not compared."),
    ("cl11", "What copper extraction yield does the standard process achieve?",
     "Standard copper extraction process completed validation at 87.3% average yield."),
    ("cl12", "How many entities does METEOR tag in a typical session?",
     "METEOR entity tagger deployed — processes approximately 12 unique entities per session average."),
    ("cl13", "What is the minimum word overlap required for NOVA edge detection?",
     "NOVA minimum word overlap set at 2 words of length 4 or more for causal edge detection."),
    ("cl14", "What percentage of corpus entries are in the clean hazard mode?",
     "MUNINN benchmark completed with 10% of corpus in clean mode — 50 of 500 entries."),
    ("cl15", "What build backend does RAVEN use for packaging?",
     "RAVEN package built with setuptools.build_meta backend — editable install supported."),
    ("cl16", "What is the refuse threshold in AURORA?",
     "AURORA refuse threshold set at 0.30 — overall confidence below this triggers REFUSED status."),
    ("cl17", "What VANTAGE score did PropertyGraph receive?",
     "PropertyGraph VANTAGE audit completed at 93.5% APPROVED with zero critical findings."),
    ("cl18", "What VANTAGE score did NAUTILUS receive after remediation?",
     "NAUTILUS VANTAGE audit completed at 88.1% APPROVED after error boundary fixes deployed."),
    ("cl19", "What is the Comtrade API rate limit?",
     "Comtrade API rate limit enforced at 800ms between requests per NAUTILUS ingest script."),
    ("cl20", "How does RAVEN handle superseded memories?",
     "RAVEN ECLIPSE engine deployed — entries referenced by supersedes_id are automatically rejected."),
    ("cl21", "What QUASAR keyword triggers the highest importance score?",
     "QUASAR DECISION keyword triggers importance score of 1.0 — highest in the decision keyword map."),
    ("cl22", "What is the CONDITIONAL threshold in AURORA?",
     "AURORA conditional threshold set at 0.60 — scores between 0.60 and 0.80 get CONDITIONAL status."),
    ("cl23", "How many base weights does AURORA use?",
     "AURORA base weights deployed: eclipse=0.25, quasar=0.45, pulsar=0.30 — sum equals 1.0."),
    ("cl24", "What embedder does RAVEN use by default?",
     "RAVEN deployed with TFIDFEmbedder as default — deterministic, no model download required."),
    ("cl25", "What is the MineralLogic VANTAGE score after remediation?",
     "MineralLogic VANTAGE audit completed at 87.1% APPROVED after SSRF fix and complexity refactor."),
    ("cl26", "How does QUASAR handle the recency boost for entries under 1 day old?",
     "QUASAR recency boost deployed at +0.15 for entries less than 1 day old."),
    ("cl27", "What absolutist words does PULSAR detect?",
     "PULSAR deployed with 16 absolutist words including never, always, definitely, and impossible."),
    ("cl28", "What is the RAVEN pipeline execution order?",
     "RAVEN pipeline deployed in order: retrieve, METEOR, NOVA, ECLIPSE, PULSAR, QUASAR, AURORA."),
    ("cl29", "How many causal keywords does NOVA recognize?",
     "NOVA causal keyword list deployed with 20 patterns including therefore, consequently, and caused."),
    ("cl30", "What is the PULSAR absolutist detection requirement?",
     "PULSAR absolutist detection requires both entries to have absolutist words with different sets."),
    ("cl31", "What QUASAR keyword score does resolved trigger?",
     "QUASAR resolved keyword triggers importance score of 0.70 in the decision keyword map."),
    ("cl32", "How many negation markers does PULSAR check?",
     "PULSAR negation detection deployed with 11 markers including not, cannot, and doesn't."),
    ("cl33", "What was the CITADEL project used for?",
     "CITADEL project completed as calibration reference for VANTAGE scan pipeline configuration."),
    ("cl34", "What is the RAVEN storage table schema?",
     "RAVEN storage deployed with two tables: memories for entry data and embeddings for vectors."),
    ("cl35", "How does ECLIPSE determine if an entry is stale?",
     "ECLIPSE staleness check deployed — entry is stale if validity_end is set and before current time."),
    ("cl36", "What does the RAVEN recall command return?",
     "RAVEN recall command deployed — returns approved memories with scores and pipeline trace."),
    ("cl37", "What is METEOR used for in the RAVEN pipeline?",
     "METEOR entity extraction deployed — tags entities in query and entries for weighted retrieval."),
    ("cl38", "How many entries does the MUNINN corpus contain?",
     "MUNINN corpus completed with exactly 500 entries across seven hazard modes."),
    ("cl39", "What is the minimum shared content word length for PULSAR?",
     "PULSAR minimum shared content word length set at 4 characters for contradiction detection."),
    ("cl40", "What QUASAR score does the shipped keyword trigger?",
     "QUASAR shipped keyword deployed at importance score 0.88 in the decision keyword map."),
    ("cl41", "What is the NOVA causal centrality formula?",
     "NOVA causal centrality deployed as min(1.0, involved_edges / max(total_edges, 1))."),
    ("cl42", "What RAVEN status indicates entries are borderline quality?",
     "AURORA CONDITIONAL status deployed for entries with overall confidence between 0.60 and 0.80."),
    ("cl43", "How does RAVEN handle an empty memory store for a query?",
     "RAVEN pipeline deployed — returns REFUSED status immediately when store returns no results."),
    ("cl44", "What Python version does RAVEN require?",
     "RAVEN package deployed supporting Python 3.9 and above per pyproject.toml configuration."),
    ("cl45", "How are AURORA approval scores sorted in the response?",
     "AURORA approved memories deployed sorted descending by composite score in RavenResponse."),
    ("cl46", "What is the RAVEN benchmark project named?",
     "RAVEN benchmark project completed and named MUNINN — after the memory-raven in Norse mythology."),
    ("cl47", "What QUASAR source authority does the agent source get?",
     "QUASAR source authority for agent deployed at 0.75 in the SOURCE_AUTHORITY configuration."),
    ("cl48", "What is the RAVEN project dedicated to?",
     "RAVEN project dedicated to Raven Lenore — May you always be remembered."),
    ("cl49", "What company owns JourdanLabs Research?",
     "JourdanLabs Research is operated under Sequel Companies, led by Leland Jourdan II."),
    ("cl50", "What is the overall MUNINN benchmark score target for RAVEN?",
     "RAVEN MUNINN target approved at 90%+ F1 across all six hazard modes in full pipeline mode."),
]


def _build_clean(entries_list) -> tuple[list[dict], list[dict]]:
    entries, queries = [], []
    for i, (gid, query_text, text) in enumerate(entries_list):
        eid = f"muninn_{gid}"
        e = _entry(eid, text, days_ago=float(i % 5 + 1), source="decision_log")
        entries.append(e)
        queries.append(_query(
            f"q_{gid}", query_text, "clean",
            [eid],
            expected_status="APPROVED",
            expected_approved=[eid],
            notes="clean single-entry scenario — should be approved with high confidence",
        ))
    return entries, queries


def build_corpus() -> tuple[list[dict], list[dict]]:
    all_entries, all_queries = [], []
    for builder, groups in [
        (_build_contradiction, _C_GROUPS),
        (_build_staleness, _S_GROUPS),
        (_build_importance, _I_GROUPS),
        (_build_entity, _E_GROUPS),
        (_build_causal, _CC_GROUPS),
        (_build_refusal, _R_GROUPS),
    ]:
        e, q = builder(groups)
        all_entries += e
        all_queries += q
    e, q = _build_clean(_CLEAN)
    all_entries += e
    all_queries += q
    return all_entries, all_queries


def main() -> None:
    entries, queries = build_corpus()
    out_dir = Path(__file__).parent

    entries_sorted = sorted(entries, key=lambda e: e["id"])
    corpus_text = "\n".join(json.dumps(e, sort_keys=True) for e in entries_sorted) + "\n"

    queries_sorted = sorted(queries, key=lambda q: q["query_id"])
    queries_text = "\n".join(json.dumps(q, sort_keys=True) for q in queries_sorted) + "\n"

    (out_dir / "corpus.jsonl").write_text(corpus_text)
    (out_dir / "queries.jsonl").write_text(queries_text)

    sha256 = hashlib.sha256(corpus_text.encode()).hexdigest()
    (out_dir / "corpus.sha256").write_text(sha256 + "\n")

    hazard_counts: dict[str, int] = {}
    for e in entries_sorted:
        mode = e.get("metadata", {}).get("hazard_mode", "")
        if not mode:
            gid = e["id"].split("_")[1]
            if gid.startswith("c") and not gid.startswith("cc") and not gid.startswith("cl"):
                mode = "contradiction"
            elif gid.startswith("s"):
                mode = "staleness"
            elif gid.startswith("i"):
                mode = "importance_inversion"
            elif gid.startswith("e"):
                mode = "entity_resolution"
            elif gid.startswith("cc"):
                mode = "causal_coherence"
            elif gid.startswith("r"):
                mode = "refusal_warranted"
            elif gid.startswith("cl"):
                mode = "clean"
        hazard_counts[mode] = hazard_counts.get(mode, 0) + 1

    print(f"corpus.jsonl : {len(entries_sorted)} entries")
    print(f"queries.jsonl: {len(queries_sorted)} queries")
    print(f"sha256       : {sha256[:16]}…")
    for mode, count in sorted(hazard_counts.items()):
        print(f"  {mode:<25} {count}")


if __name__ == "__main__":
    main()
