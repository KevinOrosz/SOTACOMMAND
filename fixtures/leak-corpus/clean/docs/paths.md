# Paths

Use `~` or `$HOME` in docs, never an absolute home directory:

- Good: `~/projects/sotacommand`, `$HOME/.config`, `%USERPROFILE%`
- Placeholder style when a full path is unavoidable: `/Users/<name>/`, `/home/<user>/`
- System locations are fine: `/usr/local/bin`, `/opt/sotacommand`, `C:\Program Files\sotacommand\`
- Shared and service accounts are fine too: `/Users/Shared/sotacommand`,
  `C:\Users\Public\Documents`, `/home/linuxbrew/.linuxbrew/bin`

On GitHub-hosted runners the checkout sits under `/home/runner/work/sotacommand/sotacommand`.
Keep the /home partition out of fixtures entirely.
