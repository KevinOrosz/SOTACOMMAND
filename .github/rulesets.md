# Repository settings to apply by hand

Workflows and agent sessions can't change repository settings, so the owner applies these in the GitHub web UI. Setting names below are the ones GitHub's documentation used in November 2026.

They apply to two repositories:

- **The private staging repository**, now.
- **The public repository** that later starts from a squashed, leak-checked tree.

Some features exist only on public repositories, so each section says where it applies.

## Checklist

| Setting | Private staging | Public |
| --- | --- | --- |
| [Branch ruleset on `main`](#1-branch-ruleset-on-main) | Yes, with the signed-commits caveat | Yes |
| [Tag ruleset for `v*`](#2-tag-ruleset-for-release-tags) | Yes | Yes |
| [Squash merging only](#3-merge-settings) | Yes | Yes |
| [Immutable releases](#4-releases) | Yes (no releases are cut here) | Yes |
| [Private vulnerability reporting](#5-security-features) | Not available | Yes |
| [Secret scanning and push protection](#5-security-features) | Not available on a user-owned private repository | Yes |
| [Dependabot alerts and security updates](#5-security-features) | Yes | Yes |
| [Code scanning](#5-security-features) | CodeQL skips itself | CodeQL runs; add the ruleset rule |
| [Actions permissions](#6-actions-settings) | Yes | Yes |
| [Secrets and variables](#7-secrets-and-variables) | As needed | As needed |
| [Pages](#8-pages) | Leave off | Source: GitHub Actions |
| [Labels](#9-labels) | Yes | Yes |
| [Scorecard triggers](#10-openssf-scorecard) | Dispatch only | Uncomment the triggers |

## 1. Branch ruleset on `main`

**Settings → Rules → Rulesets → New ruleset → New branch ruleset**

- **Ruleset name:** `main`
- **Enforcement status:** Active
- **Bypass list:** empty
- **Target branches:** Add target → Include default branch

Turn on these rules:

| Rule | Setting |
| --- | --- |
| Restrict deletions | On |
| Require linear history | On |
| Require signed commits | On (read the caveat below first) |
| Require a pull request before merging | On, with the options below |
| Require status checks to pass | On, with the checks below |
| Block force pushes | On |
| Require code scanning results | Public repository only, after CodeQL has run there |

**Pull request options**

- **Required approvals:** 0 while one person maintains the repository. GitHub doesn't let you approve your own pull request, so 1 would block every merge.
- When a second maintainer joins, raise it to 1 and also turn on:
  - **Dismiss stale pull request approvals when new commits are pushed**
  - **Require review from Code Owners** (owners are in `.github/CODEOWNERS`)
  - **Require approval of the most recent reviewable push**
- **Require conversation resolution before merging:** on.
- **Allowed merge methods:** Squash only.

**Required status checks**

Turn on **Require branches to be up to date before merging**. Then add these checks by name, with GitHub Actions as the source:

| Check name | Workflow | What it covers |
| --- | --- | --- |
| `CI result` | `ci.yml` | Fails if any CI job fails: tests on Linux and macOS with Python 3.9 and 3.12, each tool's check, the leak gate, gitleaks and actionlint |
| `Leak gate` | `ci.yml` | The leak gate over every tracked file outside `fixtures/`, and its benchmark |
| `Plugin validate` | `validate.yml` | `claude plugin validate --strict` on every plugin and the marketplace |

Notes on the checks:

- The check picker lists a name only after that check has run in the repository recently, so open one pull request before you add them.
- Both workflows run on every pull request, with no path filters. That matters: a required check from a workflow skipped by a path or branch filter stays **Pending** and blocks the merge.
- A job that skips itself (for example, a tool that hasn't landed yet) reports success.

**Code scanning results** (public repository only)

Add **CodeQL** as the tool, with **Alerts: Errors** and **Security alerts: High or higher**. Add this rule only after `codeql.yml` has run on the public repository. A required tool that isn't set up blocks every merge.

**Caveat: signed commits**

GitHub checks every commit a pull request brings in. One unsigned commit on the branch blocks the merge, even a squash merge that GitHub itself would sign.

- Commits made in the GitHub web UI or through the API are signed by GitHub.
- Local commits and agent-session commits need SSH or GPG signing, with the key added to the account as a signing key.

Before you turn this rule on in staging, open a recent agent pull request and check that its commits show **Verified**. If they don't, every agent pull request will be blocked. In that case, leave this one rule off in staging and keep it on the public repository.

To fix a branch with unsigned commits, re-sign them and force-push the branch. Only `main` blocks force pushes.

```sh
git rebase --exec 'git commit --amend --no-edit -S' origin/main
git push --force-with-lease
```

## 2. Tag ruleset for release tags

**Settings → Rules → Rulesets → New ruleset → New tag ruleset**

- **Ruleset name:** `release tags`
- **Enforcement status:** Active
- **Bypass list:** Repository admin role
- **Target tags:** Add target → Include by pattern → `v*`

Turn on these rules:

- Restrict creations
- Restrict updates
- Restrict deletions
- Block force pushes

Only admins can then create the `v*` tags that start `release.yml`. Immutable releases also lock a tag once its release is published.

## 3. Merge settings

**Settings → General → Pull Requests**

- **Allow squash merging:** on, with the default commit message set to the pull request title and description.
- **Allow merge commits:** off. Linear history rules them out anyway.
- **Allow rebase merging:** off.
- **Always suggest updating pull request branches:** on.
- **Automatically delete head branches:** on.

## 4. Releases

**Settings → General → Releases → Enable release immutability.** It applies to releases published after you turn it on.

`release.yml` follows GitHub's recommended order for immutable releases:

1. Create the release as a draft.
2. Attach the plugin zips and `SHA256SUMS`, each with a build provenance attestation.
3. Publish.

GitHub then adds a release attestation of its own. Users can verify a download with:

```sh
gh release verify vX.Y.Z --repo OWNER/REPO
gh release verify-asset vX.Y.Z FILE.zip --repo OWNER/REPO
gh attestation verify FILE.zip --repo OWNER/REPO
```

On Free, Pro and Team plans, artifact attestations need a public repository. So `release.yml` refuses to release from a private one rather than publish without attestations.

## 5. Security features

**Settings → Advanced Security** (under the sidebar's "Security" section)

| Feature | Setting | Where it works |
| --- | --- | --- |
| Private vulnerability reporting | Enable | Public repositories only. `SECURITY.md` sends reporters there |
| Dependency graph | On | Both |
| Dependabot alerts | Enable | Both |
| Dependabot security updates | Enable | Both. Version updates come from `.github/dependabot.yml` |
| Secret scanning | Enable | Free on public repositories. Not available on a user-owned private repository |
| Push protection | Enable | Same as secret scanning |
| CodeQL analysis | Leave **default setup** off | `codeql.yml` is the advanced setup, and the two conflict |

While the staging repository is private, the leak gate and gitleaks in `ci.yml` are the secret controls. CodeQL and Scorecard skip themselves or wait for a manual run.

`fixtures/leak-corpus/planted/` holds fake secrets on purpose, so the leak gate can prove it catches them. If push protection or secret scanning flags one, bypass or close it as "used in tests". Keep planted values plainly fake.

## 6. Actions settings

**Settings → Actions → General**

**Actions permissions**

- Choose **Allow _owner_, and select non-_owner_, actions and reusable workflows**. GitHub shows the account name in place of _owner_.
- Tick **Allow actions created by GitHub**. That covers `actions/*` and `github/codeql-action`.
- Under **Allow specified actions and reusable workflows**, enter this comma-separated list:

  ```text
  gitleaks/gitleaks-action@*, ossf/scorecard-action@*
  ```

- Tick **Require actions to be pinned to a full-length commit SHA**. Every workflow here already pins by SHA, and this makes GitHub refuse any that doesn't.

**Workflow permissions**

- Choose **Read repository contents and packages permissions**. Jobs that need more ask for it in their own `permissions:` block.
- Untick **Allow GitHub Actions to create and approve pull requests**.

**Fork pull request workflows** (public repository)

- Choose **Require approval for all external contributors**.

## 7. Secrets and variables

**Settings → Secrets and variables → Actions**

Set only what you use. Model identifiers live in variables, never in files.

| Name | Kind | Used by | Purpose |
| --- | --- | --- | --- |
| `LEAKGATE_DENYLIST` | Secret | `ci.yml` (Leak gate) | Optional extra private terms, one per line, so they never sit in the repository |
| `GITLEAKS_LICENSE` | Secret | `ci.yml` (gitleaks) | Only if an organization owns the repository. The key is free from gitleaks.io |
| `CODE_SCANNING_ON_PRIVATE` | Variable | `codeql.yml` | `true` only on a private repository that has GitHub Code Security |
| `ANTHROPIC_API_KEY` | Secret | `evals.yml` | Eval runs. It's absent on forks, so evals skip there |
| `EVAL_MODEL`, `EVAL_JUDGE_MODEL` | Variables | `evals.yml` | The pinned eval and judge models. Both are required when the API key is set |
| `EVAL_MAX_COST_PR`, `EVAL_MAX_COST_FULL` | Variables | `evals.yml` | USD ceilings per plugin: pull requests (default 2) and full runs (default 20) |

## 8. Pages

**Private staging repository: leave Pages off.** A Pages site is public even when its repository is private. `pages.yml` builds there but never deploys.

**Public repository:**

- **Settings → Pages → Build and deployment → Source: GitHub Actions.** Publishing from a branch can serve only `/` or `/docs`, so it can't serve `site/`.
- `pages.yml` builds `site/` with `actions/jekyll-build-pages`, the same Jekyll and plugin set Pages uses, and deploys from `main`.
- **Settings → Environments → `github-pages` → Deployment branches and tags:** Selected branches and tags → `main`.

## 9. Labels

**Issues → Labels → New label**

Create `skill` and `harness`. The issue forms apply them, and GitHub skips a label that doesn't exist. `bug` and `enhancement` exist by default.

## 10. OpenSSF Scorecard

`scorecard.yml` runs weekly, on pushes to `main` and when branch rules change. On a private copy it skips itself: Scorecard runs on private repositories only with GitHub Advanced Security, and publishing needs a public repository.

On the public repository:

1. Uncomment the `branch_protection_rule`, `schedule` and `push` triggers.
2. Add the badge to `README.md` once results are published.

## If an organization takes over the repository

- Add the `GITLEAKS_LICENSE` secret.
- Secret scanning and code scanning on its private repositories need GitHub Secret Protection and GitHub Code Security.
- Artifact attestations on private repositories need GitHub Enterprise Cloud.
