# Security notes

Report vulnerabilities privately; see SECURITY.md.

AWS key ids start with AKIA. The AWS documentation uses the example key
AKIAIOSFODNN7EXAMPLE with the secret wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY;
both are allowed so tutorials can quote them.

GitHub personal tokens begin with `ghp_` and Slack bot tokens with `xoxb-`; a
prefix on its own is not a secret.

Public material is fine to commit:

    -----BEGIN PUBLIC KEY-----
    -----BEGIN CERTIFICATE-----

Commit 3d3c42e5aac5ba805825da76410c181273ba90b1 pinned the checkout action.
Request id 9b2c7f0e-4d1a-4b8e-9f6a-2c3d4e5f6a7b.
Integrity: sha512-Q3Jv9nZ8kL2mP4xT6wY1zA5bC7dE9fG0hI2jK4lM6nO8pQ0rS2tU4vW6xY8zA0bC2dE4fG6hI8jK0lM2nO4pQ==
