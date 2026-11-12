# Install from source

    git clone git@github.com:owner/sotacommand.git ~/projects/sotacommand
    cd ~/projects/sotacommand
    python3 -m unittest discover -s tests -t .

Configuration lives in `$HOME/.config/sotacommand/` on Linux and macOS, and in
`%USERPROFILE%\.config\sotacommand\` on Windows. Nothing is written outside the
project or that folder.

Prefer HTTPS? Use `https://github.com/owner/sotacommand.git` instead.
