# Token hygiene

Secrets live in the CI secret store and reach jobs as environment variables.
Read them with `os.environ["API_TOKEN"]` and never echo them. Rotate a token
the moment it lands in a log.
