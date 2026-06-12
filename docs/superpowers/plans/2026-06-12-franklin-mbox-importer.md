# Franklin Extension: mbox/Takeout Importer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.
>
> **GATED on Franklin framework v1** and on the **Foundation plan** (`franklin-mbox-common`).
> Tasks marked **[BLOCKED:n]** need spec §9 dependency *n*.

**Goal:** A first-party extension that bulk-imports a Google Takeout `.mbox` (and, optionally, a
live IMAP account) into the user's Franklin mailbox — matching API §10 "Liberation — Import mode
only". Highest strategic value, lowest rewrite; exercises the JMAP **write** path + the manifest/
packaging contract the other extensions inherit.

**Architecture:** A container with a small upload UI. Parse the `.mbox` with the lifted mboxrd
parser, reconstruct each RFC-5322 message, and write via JMAP `Email/import`, mapping Gmail labels
to JMAP mailboxes/keywords. One-shot, resumable, idempotent (dedupe by `Message-ID`). IMAP pull is
a second source feeding the same import sink.

**Tech Stack:** Python 3.12, FastAPI (scaffold), `franklin-mbox-common` (`mboxrd`, `jmap`,
`extract`, `scaffold`), `imaplib`/`imapclient` for the IMAP source.

**Spec:** §6.1 of `docs/superpowers/specs/2026-06-12-franklin-mbox-extensions-design.md`

**Manifest scopes:** `mail:write`, `net:domains:[imap.gmail.com,imap.mail.yahoo.com,outlook.office365.com]`, UI.

---

## Prerequisites

- Foundation plan complete (`mboxrd`, `jmap.import_email`, `scaffold`, dev harness).
- **[BLOCKED:2]** JMAP `Email/import` available on dev Stalwart.
- **[BLOCKED:7]** Connector-credential standard for IMAP app passwords (interim: encrypt in
  `EXT_DATA_DIR`).

## File Structure

| File | Created/Modified | Responsibility |
|---|---|---|
| `extension.yaml` | Create | manifest (scopes, UI, config, resources) |
| `importer/app.py` | Create | scaffold app + routes (`/upload`, `/start`, `/status`) |
| `importer/mbox_source.py` | Create | `.mbox`/Takeout → message stream (wraps `mboxrd`) |
| `importer/imap_source.py` | Create | IMAP pull → message stream |
| `importer/sink.py` | Create | message stream → JMAP `Email/import` + label mapping |
| `importer/static/` | Create | minimal upload + progress UI |
| `tests/` | Create | per-module tests |

---

## Task 1 — Manifest + scaffold app [BLOCKED:1]

- [ ] Write `extension.yaml`: `id: email.franklin.importer`, scopes above, `ui.path: /`,
  `config` (e.g. `source: enum[mbox,imap]`, IMAP host/user, label-mapping mode),
  `resources` (memory/cpu), `minBoxVersion`.
- [ ] `importer/app.py`: build the FastAPI app via `franklin_mbox_common.scaffold.build_app`.
- [ ] **Test:** manifest validates against the API schema; `/healthz` 200 under the mock box.
- [ ] Commit.

## Task 2 — mbox source [NOW]

**Files:** `importer/mbox_source.py`, `tests/test_mbox_source.py`.

- [ ] **Test:** given mbox's `sample_mbox`, `iter_messages(path)` yields each message's raw bytes
  + parsed Gmail labels, count matches, mboxrd `>From ` un-escaping is correct (reuse mbox
  `read_message` semantics).
- [ ] Implement over `franklin_mbox_common.mboxrd` (`iter_message_spans` + `read_message` +
  `parse_labels`). Keep mbox's per-message all-or-nothing skip discipline (log + continue).
- [ ] Run tests. Commit.

## Task 3 — Import sink (JMAP write + label mapping) [BLOCKED:2]

**Files:** `importer/sink.py`, `tests/test_sink.py`.

- [ ] **Test (dev Stalwart):** importing the `sample_mbox` creates the right number of emails;
  Gmail labels become JMAP mailboxes (created on demand) and/or keywords; re-running imports zero
  new (dedup by `Message-ID`); a malformed message is skipped, not fatal.
- [ ] Implement `import_messages(stream, jmap, label_map)`: ensure target mailboxes exist
  (`Mailbox/get`/`set`), call `jmap.import_email(raw, mailboxIds, keywords)` per message, dedupe
  by `Message-ID` (query first or track imported ids in `EXT_DATA_DIR`), commit progress.
- [ ] Run tests. Commit.

## Task 4 — IMAP source [BLOCKED:7]

**Files:** `importer/imap_source.py`, `tests/test_imap_source.py`.

- [ ] **Test:** against a local test IMAP server (dovecot in dev compose, or Stalwart's IMAP),
  `iter_messages(host, user, app_password)` yields all messages with folder→label mapping.
- [ ] Implement an IMAP puller (app-password auth) that yields the same `(raw, labels)` shape as
  the mbox source, so the sink is source-agnostic. Store the app password encrypted in
  `EXT_DATA_DIR` (until the box secrets vault lands).
- [ ] Run tests. Commit.

## Task 5 — UI + progress [BLOCKED:3]

**Files:** `importer/app.py` routes, `importer/static/`.

- [ ] **Test (mock box):** `POST /start` kicks a background import; `GET /status` reports
  processed/total/skipped + per-skip reasons (mirror mbox's integrity report); the UI renders
  under `/ext/<id>/`, portal-authenticated.
- [ ] Implement upload (`.mbox` chunked upload to `EXT_DATA_DIR`) or IMAP config → background job
  → live status. Reuse mbox's status-holder pattern.
- [ ] Run tests. Commit.

## Task 6 — Packaging + review

- [ ] `Dockerfile` (vendor `franklin-mbox-common`); image builds; runs under the dev host.
- [ ] End-to-end on the dev harness: upload `sample_mbox` → verify in a real IMAP client connected
  to dev Stalwart that messages + labels appear; re-run is a no-op.
- [ ] Final review against spec §6.1 + API §2/§3/§4. Commit.

---

## Done when

A user can install the extension on the dev box, upload a Takeout `.mbox`, watch it import with a
skip log, see the mail in any IMAP client, and re-run safely. IMAP pull works behind an app
password. (Transition/Evacuate modes are a separate v1.1 plan, per API §10.)
