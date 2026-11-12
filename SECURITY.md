# Security policy

## Supported versions

While the project is at 0.x, only the latest release gets security fixes.

## Report a vulnerability

Report it privately, never in a public issue, pull request or discussion.

1. Open this repository's **Security** tab.
2. Choose **Report a vulnerability** and fill in the form.

That form is GitHub's private vulnerability reporting. Only the maintainers see the report. A fix can be prepared in a private fork and published with an advisory that credits you.

If the Security tab doesn't offer the form, open [a private report directly](https://github.com/KevinOrosz/SOTACOMMAND/security/advisories/new), or contact a maintainer listed in `.github/CODEOWNERS` through their GitHub profile. Send no details until you have a private channel.

Please include:

- what's affected (a tool, workflow, hook, skill or release asset) and the version or commit;
- steps to reproduce, or a proof of concept;
- the impact as you see it.

Leave out real secrets and personal data. A redacted example is enough.

## What happens next

- **Acknowledgement:** within 7 days.
- **Assessment:** an assessment and a plan, usually within 30 days.
- **Disclosure:** coordinated. The advisory is published when the fix is released, and it credits you unless you'd rather it didn't. Keep the details private until then, or for 90 days after your report, whichever comes first.

## Scope

In scope:

- The tools under `tools/` and `adapters/`, the engine plugin's hooks and scripts, and the workflows in `.github/workflows/`.
- **Leak gate misses.** Text the gate should block but passes: private names, keys, home-directory paths, model identifiers. The gate is a security control here, so a miss counts as a vulnerability.
- **Release integrity.** Any way a release asset could differ from what the release workflow built and attested.

Out of scope:

- A skill's output quality or eval scores. Open a normal issue.
- Problems in an agent harness itself. Report those to its maintainers.

## How the project limits risk

- **Tools:** standard-library Python, with no network or model calls.
- **Workflows:** they run with a read-only token unless a job asks for more. They pin every action to a full commit SHA, and they never keep checkout credentials.
- **Pull request checks:** every pull request runs the leak gate, gitleaks and strict plugin validation. CodeQL also runs once the repository is public.
- **Releases:** immutable, and every asset has a build provenance attestation.

To verify a download:

```sh
gh release verify-asset vX.Y.Z FILE.zip --repo OWNER/REPO
gh attestation verify FILE.zip --repo OWNER/REPO
```

Agents you run against this repository act with your permissions. Before you install a plugin, read what its hooks run.
