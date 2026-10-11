import os

# Tests use a local SQLite database and never require a running PostgreSQL instance.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["APP_VERSION"] = "test"
